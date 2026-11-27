from __future__ import annotations

import json
import math
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from photoflow_backend.database import connect
from photoflow_backend.metadata import sha256_file

MAX_SIMILARITY_THRESHOLD = 7
DEFAULT_SIMILARITY_THRESHOLD = 6
REVIEW_CATEGORIES = {"exact", "similar", "blurry", "low_resolution", "exposure"}
REVIEW_STATUSES = {"pending", "later", "reviewed", "all"}
PHOTO_BATCH_SIZE = 24
HASH_BAND_COUNT = 8
HASH_BAND_BITS = 8


def _perceptual_hash(image: Any) -> str:
    from PIL import Image

    resampling = getattr(Image, "Resampling", Image).LANCZOS
    pixels = list(image.resize((32, 32), resampling).getdata())
    cosine = [
        [math.cos((2 * position + 1) * frequency * math.pi / 64) for position in range(32)]
        for frequency in range(8)
    ]
    horizontal = [
        [
            sum(pixels[row * 32 + column] * cosine[frequency][column] for column in range(32))
            for frequency in range(8)
        ]
        for row in range(32)
    ]
    coefficients = [
        sum(horizontal[row][u] * cosine[v][row] for row in range(32))
        for v in range(8)
        for u in range(8)
    ]
    median = sorted(coefficients[1:])[len(coefficients[1:]) // 2]
    value = 0
    for coefficient in coefficients:
        value = (value << 1) | int(coefficient > median)
    return f"{value:016x}"


def analyze_photo(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as error:
        raise RuntimeError("Photo review requires Pillow. Install the backend requirements.") from error

    try:
        with Image.open(path) as source:
            try:
                source.draft("L", (1024, 1024))
            except (AttributeError, ValueError):
                pass
            source.load()
            image = ImageOps.exif_transpose(source)
            width, height = image.size
            grayscale = image.convert("L")
            perceptual_hash = _perceptual_hash(grayscale)
            preview = grayscale.copy()
            preview.thumbnail((512, 512), getattr(Image, "Resampling", Image).LANCZOS)
            pixels = list(preview.getdata())
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError("This image is corrupted or its format is unsupported.") from error

    count = len(pixels)
    mean_luminance = sum(pixels) / count if count else 0.0
    dark_percent = sum(pixel <= 30 for pixel in pixels) * 100.0 / count if count else 0.0
    bright_percent = sum(pixel >= 245 for pixel in pixels) * 100.0 / count if count else 0.0
    laplacian_values: list[float] = []
    image_width, image_height = preview.size
    for y in range(1, image_height - 1):
        row = y * image_width
        for x in range(1, image_width - 1):
            index = row + x
            laplacian_values.append(
                4 * pixels[index] - pixels[index - 1] - pixels[index + 1]
                - pixels[index - image_width] - pixels[index + image_width]
            )
    if laplacian_values:
        lap_mean = sum(laplacian_values) / len(laplacian_values)
        laplacian_variance = sum((value - lap_mean) ** 2 for value in laplacian_values) / len(laplacian_values)
    else:
        laplacian_variance = 0.0

    issues: list[str] = []
    if laplacian_variance < 80:
        issues.append("blurry")
    if min(width, height) < 480 or width * height < 640 * 480:
        issues.append("low_resolution")
    if mean_luminance < 45 and dark_percent >= 60:
        issues.append("dark")
    if mean_luminance > 210 and bright_percent >= 60:
        issues.append("overexposed")
    return {
        "width": width,
        "height": height,
        "perceptual_hash": perceptual_hash,
        "laplacian_variance": laplacian_variance,
        "mean_luminance": mean_luminance,
        "dark_pixel_percent": dark_percent,
        "bright_pixel_percent": bright_percent,
        "issues": issues,
    }


def _hash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _hash_bands(perceptual_hash: str) -> list[int]:
    value = int(perceptual_hash, 16)
    return [(value >> (index * HASH_BAND_BITS)) & 0xFF for index in range(HASH_BAND_COUNT)]


def _ensure_review_state(connection: sqlite3.Connection, media_id: int) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO photo_review_state(media_id) VALUES (?)",
        (media_id,),
    )


def _upsert_photo_analysis(
    connection: sqlite3.Connection,
    media_id: int,
    source_hash: str,
    result: dict[str, Any] | None,
    error: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO photo_analysis
           (media_id, source_hash, perceptual_hash, laplacian_variance, mean_luminance,
            dark_pixel_percent, bright_pixel_percent, issues_json, analysis_error, analyzed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(media_id) DO UPDATE SET
             source_hash = excluded.source_hash,
             perceptual_hash = excluded.perceptual_hash,
             laplacian_variance = excluded.laplacian_variance,
             mean_luminance = excluded.mean_luminance,
             dark_pixel_percent = excluded.dark_pixel_percent,
             bright_pixel_percent = excluded.bright_pixel_percent,
             issues_json = excluded.issues_json,
             analysis_error = excluded.analysis_error,
             analyzed_at = CURRENT_TIMESTAMP""",
        (
            media_id,
            source_hash,
            result.get("perceptual_hash") if result else None,
            result.get("laplacian_variance") if result else None,
            result.get("mean_luminance") if result else None,
            result.get("dark_pixel_percent") if result else None,
            result.get("bright_pixel_percent") if result else None,
            json.dumps(result.get("issues", []) if result else []),
            error,
        ),
    )
    _ensure_review_state(connection, media_id)


def _save_hash_bands(connection: sqlite3.Connection, media_id: int, perceptual_hash: str, source_hash: str) -> None:
    band_values = _hash_bands(perceptual_hash)
    placeholders = ", ".join("(?, ?)" for _ in band_values)
    parameters = tuple(value for index, band in enumerate(band_values) for value in (index, band))
    candidates = connection.execute(
        """SELECT DISTINCT a.media_id, a.perceptual_hash, m.content_hash
           FROM photo_hash_bands candidate_band
           JOIN photo_analysis a ON a.media_id = candidate_band.media_id
           JOIN media_items m ON m.id = a.media_id
           WHERE (candidate_band.band_index, candidate_band.band_value) IN ({placeholders})
             AND candidate_band.media_id != ? AND a.perceptual_hash IS NOT NULL""".format(placeholders=placeholders),
        (*parameters, media_id),
    )
    pairs: set[tuple[int, int, int]] = set()
    for candidate_id, candidate_hash, candidate_source_hash in candidates:
        if candidate_source_hash == source_hash:
            continue
        distance = _hash_distance(perceptual_hash, candidate_hash)
        if distance <= MAX_SIMILARITY_THRESHOLD:
            left_id, right_id = sorted((media_id, int(candidate_id)))
            pairs.add((left_id, right_id, distance))
    connection.execute("DELETE FROM similar_photo_pairs WHERE left_media_id = ? OR right_media_id = ?", (media_id, media_id))
    connection.execute("DELETE FROM photo_hash_bands WHERE media_id = ?", (media_id,))
    connection.executemany(
        "INSERT OR REPLACE INTO similar_photo_pairs(left_media_id, right_media_id, hamming_distance) VALUES (?, ?, ?)",
        pairs,
    )
    connection.executemany(
        "INSERT INTO photo_hash_bands(media_id, band_index, band_value) VALUES (?, ?, ?)",
        ((media_id, index, band) for index, band in enumerate(band_values)),
    )


class PhotoReviewManager:
    """Runs cached image analysis off-thread and persists progress/results locally."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel: threading.Event | None = None
        self._active_job_id: str | None = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._active_job_id is not None

    def start(
        self,
        similarity_threshold: int = DEFAULT_SIMILARITY_THRESHOLD,
        retry_failed_only: bool = False,
    ) -> dict[str, Any]:
        if isinstance(similarity_threshold, bool) or not isinstance(similarity_threshold, int) or not 0 <= similarity_threshold <= MAX_SIMILARITY_THRESHOLD:
            raise ValueError(f"Similarity threshold must be between 0 and {MAX_SIMILARITY_THRESHOLD}")
        try:
            from PIL import Image  # noqa: F401
        except ImportError as error:
            raise ValueError("Photo review requires Pillow. Install the backend requirements.") from error

        with self._lock:
            if self._active_job_id:
                raise ValueError("Photo analysis is already running")
            with connect() as connection:
                active = connection.execute(
                    "SELECT 1 FROM photo_analysis_jobs WHERE status = 'running' LIMIT 1"
                ).fetchone()
                if active:
                    raise ValueError("Photo analysis is already running")
                failure_filter = "AND a.analysis_error IS NOT NULL" if retry_failed_only else ""
                total = connection.execute(
                    f"""SELECT COUNT(*) FROM media_items m
                       LEFT JOIN photo_analysis a ON a.media_id = m.id
                       WHERE m.media_type = 'photo'
                         AND (a.media_id IS NULL OR a.source_hash != COALESCE(m.content_hash, '') OR a.analysis_error IS NOT NULL)
                         {failure_filter}"""
                ).fetchone()[0]
                if retry_failed_only and not total:
                    raise ValueError("There are no failed photo-analysis items to retry")
                job_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO photo_analysis_jobs(id, status, similarity_threshold, total_files)
                       VALUES (?, 'running', ?, ?)""",
                    (job_id, similarity_threshold, total),
                )
            cancellation = threading.Event()
            self._cancel = cancellation
            self._active_job_id = job_id
        worker = threading.Thread(
            target=self._run,
            args=(job_id, similarity_threshold, cancellation, retry_failed_only),
            daemon=True,
            name=f"photoflow-review-{job_id[:8]}",
        )
        try:
            worker.start()
        except Exception:
            try:
                with connect() as connection:
                    connection.execute(
                        """UPDATE photo_analysis_jobs
                           SET status = 'failed', completed_at = CURRENT_TIMESTAMP,
                               error_summary = 'Photo analysis could not be started.'
                           WHERE id = ?""",
                        (job_id,),
                    )
            finally:
                with self._lock:
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                        self._cancel = None
            raise
        return self.status(job_id)

    def status(self, job_id: str | None = None) -> dict[str, Any] | None:
        with connect() as connection:
            connection.row_factory = sqlite3.Row
            if job_id:
                row = connection.execute("SELECT * FROM photo_analysis_jobs WHERE id = ?", (job_id,)).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM photo_analysis_jobs ORDER BY started_at DESC, rowid DESC LIMIT 1"
                ).fetchone()
        if not row:
            return None
        result = dict(row)
        with self._lock:
            worker_active = result["id"] == self._active_job_id
        if result["status"] == "running" and not worker_active:
            with connect() as connection:
                connection.execute(
                    """UPDATE photo_analysis_jobs
                       SET status = 'interrupted', completed_at = CURRENT_TIMESTAMP,
                           error_summary = 'Photo analysis stopped unexpectedly.'
                       WHERE id = ? AND status = 'running'""",
                    (result["id"],),
                )
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT * FROM photo_analysis_jobs WHERE id = ?", (result["id"],)
                ).fetchone()
            if row:
                result = dict(row)
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if self._active_job_id == job_id and self._cancel:
                self._cancel.set()
        with connect() as connection:
            cursor = connection.execute(
                "UPDATE photo_analysis_jobs SET cancel_requested = 1 WHERE id = ? AND status = 'running'",
                (job_id,),
            )
            if cursor.rowcount == 0:
                raise ValueError("Photo analysis job is not running")
        status = self.status(job_id)
        if status is None:
            raise ValueError("Unknown photo analysis job")
        return status

    def retry_failed(self, similarity_threshold: int = DEFAULT_SIMILARITY_THRESHOLD) -> dict[str, Any]:
        with connect() as connection:
            failed_items = connection.execute(
                "SELECT 1 FROM photo_analysis WHERE analysis_error IS NOT NULL LIMIT 1"
            ).fetchone()
            failed_jobs = connection.execute(
                "SELECT 1 FROM photo_analysis_jobs WHERE status IN ('failed', 'interrupted') LIMIT 1"
            ).fetchone()
        if not failed_items and not failed_jobs:
            raise ValueError("There are no failed photo-analysis records to retry")
        return self.start(similarity_threshold, retry_failed_only=bool(failed_items))

    def _run(
        self,
        job_id: str,
        threshold: int,
        cancellation: threading.Event,
        retry_failed_only: bool = False,
    ) -> None:
        status = "completed"
        try:
            last_id = 0
            while not cancellation.is_set():
                with connect() as connection:
                    failure_filter = "AND a.analysis_error IS NOT NULL" if retry_failed_only else ""
                    candidates = connection.execute(
                        f"""SELECT m.id, m.source_path, m.content_hash, m.size_bytes, m.modified_time_ns
                           FROM media_items m
                           LEFT JOIN photo_analysis a ON a.media_id = m.id
                           WHERE m.media_type = 'photo' AND m.id > ?
                             AND (a.media_id IS NULL OR a.source_hash != COALESCE(m.content_hash, '') OR a.analysis_error IS NOT NULL)
                             {failure_filter}
                           ORDER BY m.id LIMIT ?""",
                        (last_id, PHOTO_BATCH_SIZE),
                    ).fetchall()
                if not candidates:
                    break
                for media_id, source_path, content_hash, size_bytes, modified_time_ns in candidates:
                    if cancellation.is_set():
                        break
                    last_id = int(media_id)
                    result: dict[str, Any] | None = None
                    analysis_error: str | None = None
                    source_hash = str(content_hash or "")
                    try:
                        path = Path(source_path)
                        stat = path.stat()
                        if stat.st_size != size_bytes or stat.st_mtime_ns != modified_time_ns:
                            raise ValueError("File changed since indexing; rescan this folder before reviewing.")
                        if not source_hash:
                            source_hash = sha256_file(path)
                        result = analyze_photo(path)
                        after = path.stat()
                        if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                            raise ValueError("File changed during analysis; rescan this folder before reviewing.")
                    except (OSError, ValueError, RuntimeError) as error:
                        analysis_error = str(error) if isinstance(error, (ValueError, RuntimeError)) else "Image file is unavailable."
                    except Exception:
                        analysis_error = "Image could not be analyzed."
                    with connect() as connection:
                        if result and not content_hash:
                            connection.execute("UPDATE media_items SET content_hash = ? WHERE id = ?", (source_hash, media_id))
                        _upsert_photo_analysis(connection, int(media_id), source_hash, result, analysis_error)
                        if result:
                            _save_hash_bands(connection, int(media_id), result["perceptual_hash"], source_hash)
                        else:
                            connection.execute("DELETE FROM similar_photo_pairs WHERE left_media_id = ? OR right_media_id = ?", (media_id, media_id))
                            connection.execute("DELETE FROM photo_hash_bands WHERE media_id = ?", (media_id,))
                        connection.execute(
                            """UPDATE photo_analysis_jobs
                               SET processed_files = processed_files + 1,
                                   analyzed_files = analyzed_files + ?,
                                   failed_files = failed_files + ?
                               WHERE id = ?""",
                            (int(result is not None), int(result is None), job_id),
                        )
                if cancellation.is_set():
                    break
            if cancellation.is_set():
                status = "cancelled"
        except Exception:
            status = "failed"
        finally:
            try:
                with connect() as connection:
                    connection.execute(
                        """UPDATE photo_analysis_jobs SET status = ?, completed_at = CURRENT_TIMESTAMP,
                           error_summary = CASE WHEN ? = 'failed' THEN 'Photo analysis stopped unexpectedly.' ELSE error_summary END
                           WHERE id = ?""",
                        (status, status, job_id),
                    )
            finally:
                with self._lock:
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                        self._cancel = None


def _public_review_item(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    source_path = result.pop("source_path", None)
    result["location"] = Path(source_path).parent.name if source_path else "Local library"
    if result.get("issues_json") is not None:
        issues = json.loads(result.pop("issues_json") or "[]")
        result["issues"] = issues
        reasons = []
        if "blurry" in issues:
            reasons.append(f"Low Laplacian variance ({result['laplacian_variance']:.1f}).")
        if "low_resolution" in issues:
            reasons.append(f"Resolution is {result['width']} × {result['height']}; this is below the review threshold.")
        if "dark" in issues:
            reasons.append(f"Mean brightness is {result['mean_luminance']:.1f}/255 with {result['dark_pixel_percent']:.1f}% very dark pixels.")
        if "overexposed" in issues:
            reasons.append(f"Mean brightness is {result['mean_luminance']:.1f}/255 with {result['bright_pixel_percent']:.1f}% near-white pixels.")
        result["reason"] = " ".join(reasons)
    return result


def review_overview(similarity_threshold: int = DEFAULT_SIMILARITY_THRESHOLD) -> dict[str, int]:
    with connect() as connection:
        exact = connection.execute(
            """SELECT COUNT(*) FROM (
                 SELECT content_hash FROM media_items
                 WHERE content_hash IS NOT NULL AND content_hash != ''
                 GROUP BY content_hash HAVING COUNT(*) > 1
               )"""
        ).fetchone()[0]
        similar = connection.execute(
            "SELECT COUNT(*) FROM similar_photo_pairs WHERE hamming_distance <= ?",
            (similarity_threshold,),
        ).fetchone()[0]
        blurry, low, exposure = connection.execute(
            """SELECT
                 COALESCE(SUM(instr(issues_json, '"blurry"') > 0), 0),
                 COALESCE(SUM(instr(issues_json, '"low_resolution"') > 0), 0),
                 COALESCE(SUM(instr(issues_json, '"dark"') > 0 OR instr(issues_json, '"overexposed"') > 0), 0)
               FROM photo_analysis WHERE analysis_error IS NULL"""
        ).fetchone()
    return {"exact": exact, "similar": similar, "blurry": blurry, "low_resolution": low, "exposure": exposure}


def list_review(
    category: str,
    limit: int,
    offset: int,
    status: str = "pending",
    similarity_threshold: int = DEFAULT_SIMILARITY_THRESHOLD,
) -> dict[str, Any]:
    if category not in REVIEW_CATEGORIES:
        raise ValueError("Unknown photo review category")
    if status not in REVIEW_STATUSES:
        raise ValueError("Unknown review status")
    with connect() as connection:
        connection.row_factory = sqlite3.Row
        if category == "exact":
            groups = connection.execute(
                """SELECT m.content_hash, COUNT(*) AS item_count, MIN(m.id) AS representative_id
                   FROM media_items m
                   LEFT JOIN photo_review_state s ON s.media_id = m.id
                   WHERE m.content_hash IS NOT NULL AND m.content_hash != ''
                   GROUP BY m.content_hash HAVING COUNT(*) > 1
                     AND (? = 'all' OR SUM(CASE WHEN COALESCE(s.review_status, 'pending') = ? THEN 1 ELSE 0 END) > 0)
                   ORDER BY m.content_hash LIMIT ? OFFSET ?""",
                (status, status, limit, offset),
            ).fetchall()
            output = []
            for content_hash, item_count, representative_id in groups:
                rows = connection.execute(
                    """SELECT m.id, m.media_type, m.filename, m.extension, m.size_bytes, m.width, m.height,
                              m.source_path, COALESCE(s.review_status, 'pending') AS review_status
                       FROM media_items m LEFT JOIN photo_review_state s ON s.media_id = m.id
                       WHERE m.content_hash = ? ORDER BY m.id""",
                    (content_hash,),
                ).fetchall()
                items = [_public_review_item(row) for row in rows]
                for item in items:
                    item["is_representative"] = item["id"] == representative_id
                output.append({
                    "id": int(representative_id),
                    "item_count": int(item_count),
                    "representative_id": int(representative_id),
                    "items": items,
                    "explanation": "Exact duplicates have identical SHA-256 content hashes. Originals are unchanged.",
                })
            return {"groups": output, "items": []}

        if category == "similar":
            groups = connection.execute(
                """SELECT p.left_media_id, p.right_media_id, p.hamming_distance
                   FROM similar_photo_pairs p
                   LEFT JOIN photo_review_state ls ON ls.media_id = p.left_media_id
                   LEFT JOIN photo_review_state rs ON rs.media_id = p.right_media_id
                   WHERE p.hamming_distance <= ?
                     AND (? = 'all' OR COALESCE(ls.review_status, 'pending') = ? OR COALESCE(rs.review_status, 'pending') = ?)
                   ORDER BY p.hamming_distance, p.left_media_id LIMIT ? OFFSET ?""",
                (similarity_threshold, status, status, status, limit, offset),
            ).fetchall()
            output = []
            for left_id, right_id, distance in groups:
                rows = connection.execute(
                    """SELECT m.id, m.media_type, m.filename, m.extension, m.size_bytes, m.width, m.height,
                              m.source_path, COALESCE(s.review_status, 'pending') AS review_status
                       FROM media_items m LEFT JOIN photo_review_state s ON s.media_id = m.id
                       WHERE m.id IN (?, ?) ORDER BY m.id""",
                    (left_id, right_id),
                ).fetchall()
                output.append({
                    "id": int(left_id),
                    "distance": int(distance),
                    "items": [_public_review_item(row) for row in rows],
                    "explanation": f"These photos have similar visual structure (perceptual-hash distance {distance}). They are not necessarily identical.",
                })
            return {"groups": output, "items": []}

        issue_test = {
            "blurry": "instr(a.issues_json, '\"blurry\"') > 0",
            "low_resolution": "instr(a.issues_json, '\"low_resolution\"') > 0",
            "exposure": "(instr(a.issues_json, '\"dark\"') > 0 OR instr(a.issues_json, '\"overexposed\"') > 0)",
        }[category]
        rows = connection.execute(
            f"""SELECT m.id, m.media_type, m.filename, m.extension, m.size_bytes, m.width, m.height,
                       m.capture_date, m.camera_make, m.camera_model, m.source_path,
                       a.issues_json, a.laplacian_variance, a.mean_luminance, a.dark_pixel_percent,
                       a.bright_pixel_percent, COALESCE(s.review_status, 'pending') AS review_status
                FROM photo_analysis a JOIN media_items m ON m.id = a.media_id
                LEFT JOIN photo_review_state s ON s.media_id = m.id
                WHERE a.analysis_error IS NULL AND {issue_test}
                  AND (? = 'all' OR COALESCE(s.review_status, 'pending') = ?)
                ORDER BY m.id LIMIT ? OFFSET ?""",
            (status, status, limit, offset),
        ).fetchall()
    return {"groups": [], "items": [_public_review_item(row) for row in rows]}


def set_review_status(media_id: int, status: str) -> dict[str, Any]:
    if isinstance(media_id, bool) or not isinstance(media_id, int) or media_id < 1:
        raise ValueError("Invalid media identifier")
    if status not in {"pending", "later", "reviewed"}:
        raise ValueError("Invalid review status")
    with connect() as connection:
        exists = connection.execute("SELECT 1 FROM media_items WHERE id = ?", (media_id,)).fetchone()
        if not exists:
            raise ValueError("Unknown media item")
        connection.execute(
            """INSERT INTO photo_review_state(media_id, review_status) VALUES (?, ?)
               ON CONFLICT(media_id) DO UPDATE SET review_status = excluded.review_status, updated_at = CURRENT_TIMESTAMP""",
            (media_id, status),
        )
    return {"media_id": media_id, "review_status": status}
