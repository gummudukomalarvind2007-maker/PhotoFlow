from __future__ import annotations

import json
import sqlite3
from typing import Any


MEDIA_COLUMNS = """
id, source_path, file_identity, content_hash, media_type, extension, filename,
size_bytes, modified_time_ns, width, height, duration_seconds, frame_rate,
capture_date, camera_make, camera_model, gps_latitude, gps_longitude,
thumbnail_path, metadata_json, metadata_provenance, metadata_status, metadata_error, last_scan_id,
first_scanned_at, last_scanned_at, last_viewed_at
"""
MEDIA_DISPLAY_COLUMNS = """
id, media_type, extension, filename, size_bytes, width, height, duration_seconds,
frame_rate, capture_date, camera_make, camera_model, metadata_status, metadata_error
"""
NORMALIZED_CAPTURE_DATE = (
    "date(CASE WHEN substr(capture_date, 5, 1) = ':' "
    "THEN substr(capture_date, 1, 4) || '-' || substr(capture_date, 6, 2) || '-' || substr(capture_date, 9, 2) "
    "ELSE substr(capture_date, 1, 10) END)"
)


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for field in ("metadata_json", "metadata_provenance"):
        if field in result:
            result[field] = json.loads(result[field] or "{}")
    return result


def get_media_by_path(connection: sqlite3.Connection, source_path: str) -> dict[str, Any] | None:
    connection.row_factory = sqlite3.Row
    row = connection.execute(f"SELECT {MEDIA_COLUMNS} FROM media_items WHERE source_path = ?", (source_path,)).fetchone()
    return row_to_dict(row) if row else None


def update_thumbnail(connection: sqlite3.Connection, media_id: int, thumbnail_path: str, content_hash: str | None) -> None:
    connection.execute(
        "UPDATE media_items SET thumbnail_path = ?, content_hash = COALESCE(?, content_hash) WHERE id = ?",
        (thumbnail_path, content_hash, media_id),
    )
    connection.commit()


def touch_existing(connection: sqlite3.Connection, media_id: int, scan_id: str) -> None:
    connection.execute(
        "UPDATE media_items SET last_scan_id = ?, last_scanned_at = CURRENT_TIMESTAMP WHERE id = ?",
        (scan_id, media_id),
    )
    connection.commit()


def record_media_viewed(connection: sqlite3.Connection, media_id: int) -> None:
    connection.execute(
        """UPDATE media_items SET last_viewed_at = CURRENT_TIMESTAMP
           WHERE id = ? AND media_type = 'photo'""",
        (media_id,),
    )


def upsert_media(connection: sqlite3.Connection, record: dict[str, Any], scan_id: str) -> None:
    fields = (
        "source_path", "file_identity", "content_hash", "media_type", "extension", "filename",
        "size_bytes", "modified_time_ns", "width", "height", "duration_seconds", "frame_rate",
        "capture_date", "camera_make", "camera_model", "gps_latitude", "gps_longitude",
        "thumbnail_path", "metadata_json", "metadata_provenance", "metadata_status", "metadata_error", "last_scan_id",
    )
    values = [record.get(field) for field in fields[:-1]]
    values[fields.index("metadata_status")] = record.get("metadata_status") or "unavailable"
    values.append(scan_id)
    assignments = ", ".join(f"{field} = excluded.{field}" for field in fields[1:])
    placeholders = ", ".join("?" for _ in fields)
    connection.execute(
        f"INSERT INTO media_items ({', '.join(fields)}) VALUES ({placeholders}) "
        f"ON CONFLICT(source_path) DO UPDATE SET {assignments}, last_scanned_at = CURRENT_TIMESTAMP",
        values,
    )
    connection.commit()


def create_job(connection: sqlite3.Connection, job_id: str, folders: list[str], resumed_from_id: str | None = None) -> None:
    connection.execute(
        "INSERT INTO scan_jobs (id, folders_json, status, resumed_from_id) VALUES (?, ?, 'running', ?)",
        (job_id, json.dumps(folders), resumed_from_id),
    )
    connection.commit()


JOB_UPDATE_COLUMNS = {
    "status",
    "discovered_files",
    "processed_files",
    "indexed_files",
    "skipped_files",
    "failed_files",
    "current_path",
    "error_summary",
    "cancel_requested",
}


def update_job(connection: sqlite3.Connection, job_id: str, **values: Any) -> None:
    if not values:
        return
    unknown = set(values) - JOB_UPDATE_COLUMNS
    if unknown:
        raise ValueError(f"Unknown scan job columns: {', '.join(sorted(unknown))}")
    assignments = ", ".join(f"{column} = ?" for column in values)
    connection.execute(f"UPDATE scan_jobs SET {assignments} WHERE id = ?", (*values.values(), job_id))
    connection.commit()


def get_job(connection: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM scan_jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["folders"] = json.loads(result.pop("folders_json"))
    result["cancel_requested"] = bool(result["cancel_requested"])
    return result


def latest_resumable_job(connection: sqlite3.Connection) -> dict[str, Any] | None:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """SELECT * FROM scan_jobs
           WHERE status IN ('cancelled', 'interrupted', 'failed')
             AND NOT EXISTS (
               SELECT 1 FROM scan_jobs resumed WHERE resumed.resumed_from_id = scan_jobs.id
             )
           ORDER BY COALESCE(completed_at, started_at) DESC, rowid DESC
           LIMIT 1"""
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["folders"] = json.loads(result.pop("folders_json"))
    result["cancel_requested"] = bool(result["cancel_requested"])
    return result


def latest_active_job(connection: sqlite3.Connection) -> dict[str, Any] | None:
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM scan_jobs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        return None
    result = dict(row)
    result["folders"] = json.loads(result.pop("folders_json"))
    return result


def list_media(
    connection: sqlite3.Connection,
    media_type: str,
    limit: int,
    offset: int,
    search: str = "",
    extension: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    camera_make: str = "",
    camera_model: str = "",
    sort: str = "newest",
    *,
    numeric_filters: dict[str, int | float] | None = None,
    display_only: bool = False,
) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    where_clause, parameters = media_where_clause(
        media_type, search, extension, date_from, date_to, camera_make, camera_model, numeric_filters
    )
    order_by = {
        "newest": f"{NORMALIZED_CAPTURE_DATE} IS NULL, {NORMALIZED_CAPTURE_DATE} DESC",
        "oldest": f"{NORMALIZED_CAPTURE_DATE} IS NULL, {NORMALIZED_CAPTURE_DATE} ASC",
        "filename-asc": "filename COLLATE NOCASE ASC",
        "filename-desc": "filename COLLATE NOCASE DESC",
        "size-desc": "size_bytes DESC",
        "size-asc": "size_bytes ASC",
    }[sort]
    columns = MEDIA_DISPLAY_COLUMNS if display_only else MEDIA_COLUMNS
    rows = connection.execute(
        f"SELECT {columns} FROM media_items{where_clause} ORDER BY {order_by}, id LIMIT ? OFFSET ?",
        (*parameters, limit, offset),
    ).fetchall()
    return [dict(row) if display_only else row_to_dict(row) for row in rows]


def media_where_clause(
    media_type: str,
    search: str = "",
    extension: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    camera_make: str = "",
    camera_model: str = "",
    numeric_filters: dict[str, int | float] | None = None,
) -> tuple[str, list[Any]]:
    predicates: list[str] = []
    parameters: list[Any] = []
    if media_type != "all":
        predicates.append("media_type = ?")
        parameters.append(media_type)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        predicates.append(
            "(filename LIKE ? ESCAPE '\\' COLLATE NOCASE OR extension LIKE ? ESCAPE '\\' COLLATE NOCASE "
            "OR camera_make LIKE ? ESCAPE '\\' COLLATE NOCASE OR camera_model LIKE ? ESCAPE '\\' COLLATE NOCASE)"
        )
        parameters.extend((pattern, pattern, pattern, pattern))
    if extension:
        predicates.append("extension = ?")
        parameters.append(extension)
    if date_from:
        predicates.append(f"{NORMALIZED_CAPTURE_DATE} >= date(?)")
        parameters.append(date_from)
    if date_to:
        predicates.append(f"{NORMALIZED_CAPTURE_DATE} <= date(?)")
        parameters.append(date_to)
    for column, value in (("camera_make", camera_make), ("camera_model", camera_model)):
        if value:
            escaped_value = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            predicates.append(f"{column} LIKE ? ESCAPE '\\' COLLATE NOCASE")
            parameters.append(f"%{escaped_value}%")
    numeric_columns = {
        "min_width": ("width", ">="),
        "max_width": ("width", "<="),
        "min_height": ("height", ">="),
        "max_height": ("height", "<="),
        "min_resolution_px": ("width * height", ">="),
        "max_resolution_px": ("width * height", "<="),
        "min_duration_seconds": ("duration_seconds", ">="),
        "max_duration_seconds": ("duration_seconds", "<="),
        "min_size_bytes": ("size_bytes", ">="),
        "max_size_bytes": ("size_bytes", "<="),
    }
    for key, (column, operator) in numeric_columns.items():
        if numeric_filters and key in numeric_filters:
            predicates.append(f"{column} {operator} ?")
            parameters.append(numeric_filters[key])

    where_clause = f" WHERE {' AND '.join(predicates)}" if predicates else ""
    return where_clause, parameters


def count_media(
    connection: sqlite3.Connection,
    media_type: str,
    search: str = "",
    extension: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    camera_make: str = "",
    camera_model: str = "",
    numeric_filters: dict[str, int | float] | None = None,
) -> int:
    where_clause, parameters = media_where_clause(
        media_type, search, extension, date_from, date_to, camera_make, camera_model, numeric_filters
    )
    return int(connection.execute(f"SELECT COUNT(*) FROM media_items{where_clause}", parameters).fetchone()[0])


def remove_media_records(connection: sqlite3.Connection, media_ids: list[int]) -> tuple[int, list[str]]:
    if not media_ids:
        return 0, []
    connection.row_factory = sqlite3.Row
    thumbnail_paths: set[str] = set()
    existing = 0
    for start in range(0, len(media_ids), 500):
        batch = media_ids[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            f"SELECT id, thumbnail_path FROM media_items WHERE id IN ({placeholders})",
            batch,
        ).fetchall()
        existing += len(rows)
        thumbnail_paths.update(row["thumbnail_path"] for row in rows if row["thumbnail_path"])
    if existing != len(media_ids):
        raise ValueError("One or more selected media items are no longer in the library")

    for start in range(0, len(media_ids), 500):
        batch = media_ids[start:start + 500]
        batch_placeholders = ",".join("?" for _ in batch)
        connection.execute(
            f"DELETE FROM media_items WHERE id IN ({batch_placeholders})",
            batch,
        )

    unreferenced = [
        path for path in thumbnail_paths
        if connection.execute(
            "SELECT 1 FROM media_items WHERE thumbnail_path = ? LIMIT 1", (path,)
        ).fetchone() is None
    ]
    return existing, unreferenced


def remove_matching_media(
    connection: sqlite3.Connection,
    media_type: str,
    search: str,
    extension: str | None,
    date_from: str | None,
    date_to: str | None,
    camera_make: str,
    camera_model: str,
    excluded_ids: list[int],
    numeric_filters: dict[str, int | float] | None = None,
) -> tuple[int, list[str]]:
    where_clause, parameters = media_where_clause(
        media_type, search, extension, date_from, date_to, camera_make, camera_model, numeric_filters
    )
    excluded = set(excluded_ids)
    last_id = 0
    removed = 0
    thumbnail_paths: set[str] = set()
    while True:
        continuation = f"{where_clause}{' AND' if where_clause else ' WHERE'} id > ?"
        rows = connection.execute(
            f"SELECT id FROM media_items{continuation} ORDER BY id LIMIT 500",
            (*parameters, last_id),
        ).fetchall()
        if not rows:
            break
        last_id = int(rows[-1][0])
        batch_ids = [int(row[0]) for row in rows if int(row[0]) not in excluded]
        batch_removed, batch_thumbnails = remove_media_records(connection, batch_ids)
        removed += batch_removed
        thumbnail_paths.update(batch_thumbnails)
    return removed, sorted(thumbnail_paths)


def library_overview(connection: sqlite3.Connection) -> dict[str, int]:
    totals = {"photos": 0, "videos": 0, "albums": 0, "total_bytes": 0}
    for row in connection.execute("SELECT media_type, COUNT(*) AS total, COALESCE(SUM(size_bytes), 0) AS bytes FROM media_items GROUP BY media_type"):
        if row[0] == "photo":
            totals["photos"] = row[1]
        elif row[0] == "video":
            totals["videos"] = row[1]
        totals["total_bytes"] += row[2]
    totals["albums"] = connection.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
    return totals


def record_scan_failure(
    connection: sqlite3.Connection,
    scan_id: str,
    source_path: str,
    media_type: str | None,
    error_summary: str,
) -> None:
    connection.execute(
        """INSERT INTO scan_failures(scan_job_id, source_path, media_type, error_summary)
           VALUES (?, ?, ?, ?)""",
        (scan_id, source_path, media_type, error_summary[:500]),
    )
    connection.commit()


def resolve_scan_failures(connection: sqlite3.Connection, source_path: str) -> None:
    connection.execute(
        "UPDATE scan_failures SET resolved_at = CURRENT_TIMESTAMP WHERE source_path = ? AND resolved_at IS NULL",
        (source_path,),
    )
    connection.commit()


def list_scan_failures(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT f.id, f.scan_job_id, f.source_path, f.media_type, f.error_summary, f.created_at
           FROM scan_failures f
           WHERE f.resolved_at IS NULL
             AND NOT EXISTS (
               SELECT 1 FROM scan_failures newer
               WHERE newer.source_path = f.source_path AND newer.resolved_at IS NULL
                 AND (newer.created_at > f.created_at OR (newer.created_at = f.created_at AND newer.id > f.id))
             )
           ORDER BY f.created_at DESC, f.id DESC"""
    ).fetchall()
    return [dict(row) for row in rows]


def list_failed_scan_jobs(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT j.* FROM scan_jobs j
           WHERE j.status = 'failed'
             AND NOT EXISTS (
               SELECT 1 FROM scan_failures f
               WHERE f.scan_job_id = j.id AND f.resolved_at IS NULL
             )
           ORDER BY COALESCE(j.completed_at, j.started_at) DESC, j.rowid DESC"""
    ).fetchall()
    result = []
    for row in rows:
        job = dict(row)
        job["folders"] = json.loads(job.pop("folders_json"))
        job["cancel_requested"] = bool(job["cancel_requested"])
        result.append(job)
    return result


def list_failed_photo_analysis(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT m.id AS media_id, m.filename, m.media_type, a.analysis_error, a.analyzed_at
           FROM photo_analysis a JOIN media_items m ON m.id = a.media_id
           WHERE a.analysis_error IS NOT NULL
           ORDER BY a.analyzed_at DESC, m.id"""
    ).fetchall()
    return [dict(row) for row in rows]


def list_failed_photo_analysis_jobs(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT id, status, failed_files, error_summary, started_at, completed_at
           FROM photo_analysis_jobs
           WHERE status IN ('failed', 'interrupted')
             AND NOT EXISTS (
               SELECT 1 FROM photo_analysis WHERE analysis_error IS NOT NULL
             )
             AND EXISTS (
               SELECT 1 FROM media_items m
               LEFT JOIN photo_analysis a ON a.media_id = m.id
               WHERE m.media_type = 'photo'
                 AND (a.media_id IS NULL OR a.source_hash != COALESCE(m.content_hash, '')
                   OR a.analysis_error IS NOT NULL)
             )
           ORDER BY COALESCE(completed_at, started_at) DESC, rowid DESC"""
    ).fetchall()
    return [dict(row) for row in rows]


def thumbnail_for_media(connection: sqlite3.Connection, media_id: int) -> str | None:
    row = connection.execute("SELECT thumbnail_path FROM media_items WHERE id = ?", (media_id,)).fetchone()
    return str(row[0]) if row and row[0] else None
