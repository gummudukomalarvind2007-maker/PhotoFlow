from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from photoflow_backend.albums import (
    add_album_media,
    create_album,
    delete_album,
    list_album_media,
    list_albums,
    list_occasion_media,
    list_occasions,
    list_smart_album_media,
    list_smart_albums,
    remove_album_media,
    rename_album,
)
from photoflow_backend.database import connect, database_path
from photoflow_backend.media_filters import MEDIA_FILTER_KEYS, media_filter_arguments, normalize_media_filters
from photoflow_backend.metadata import ensure_cached_thumbnail, is_cached_thumbnail
from photoflow_backend.migrations import apply_migrations
from photoflow_backend.photo_review import (
    DEFAULT_SIMILARITY_THRESHOLD,
    MAX_SIMILARITY_THRESHOLD,
    PhotoReviewManager,
    list_review,
    review_overview,
    set_review_status,
)
from photoflow_backend.repository import (
    latest_resumable_job,
    library_overview,
    list_failed_photo_analysis,
    list_failed_photo_analysis_jobs,
    list_failed_scan_jobs,
    list_media,
    list_scan_failures,
    count_media,
    record_media_viewed,
    remove_matching_media,
    remove_media_records,
    update_thumbnail,
)
from photoflow_backend.scanner import ScanManager
from photoflow_backend.settings import set_skip_unchanged_files, settings_overview
from photoflow_backend.smart_collections import (
    create_saved_smart_album,
    delete_saved_smart_album,
    list_saved_smart_album_media,
    list_saved_smart_albums,
    list_smart_collection_media,
    list_smart_collections,
)
from photoflow_backend.transfers import TransferManager, _thumbnail_root, cleanup_abandoned_temporary_files


scanner = ScanManager()
photo_review = PhotoReviewManager()
transfers = TransferManager()


def _positive_int(request: dict[str, Any], key: str) -> int:
    value = request.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Invalid {key}")
    return value


def _string_id(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError(f"Invalid {key}")
    return value


def _optional_date(request: dict[str, Any], key: str) -> str | None:
    value = request.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{key} must be a date in YYYY-MM-DD format")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as error:
        raise ValueError(f"{key} must be a valid date in YYYY-MM-DD format") from error


def _media_filters(request: dict[str, Any]) -> dict[str, Any]:
    return normalize_media_filters({key: request[key] for key in MEDIA_FILTER_KEYS if key in request})


def initialize_database(recover_interrupted_scans: bool = False) -> None:
    if recover_interrupted_scans:
        cleanup_abandoned_temporary_files()
    with connect() as connection:
        apply_migrations(connection)
        # A process cannot resume an in-memory thread after a restart; retain its job for explicit resume.
        if recover_interrupted_scans:
            connection.execute("UPDATE scan_jobs SET status = 'interrupted' WHERE status = 'running'")
            connection.execute(
                "UPDATE photo_analysis_jobs SET status = 'interrupted' WHERE status = 'running'"
            )
        connection.commit()


def health_payload() -> dict[str, Any]:
    initialize_database()
    return {
        "status": "ok",
        "python_version": sys.version.split()[0],
        "sqlite_version": sqlite3.sqlite_version,
        "database_path": str(database_path()),
    }


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    """Progress deliberately excludes source folder/current-file paths from renderer-facing responses."""
    public = {key: job.get(key) for key in (
        "id", "status", "discovered_files", "processed_files", "indexed_files", "skipped_files",
        "failed_files", "cancel_requested", "started_at", "completed_at",
    )}
    public["error_summary"] = (
        "Some files could not be processed. Check folder access and local media tools."
        if job.get("error_summary")
        else None
    )
    return public


def _public_scan_failure(error: str) -> str:
    lowered = error.lower()
    if "permission" in lowered or "access is denied" in lowered:
        return "Access was denied. Check the file or folder permissions."
    if "corrupt" in lowered or "cannot identify image" in lowered or "invalid data" in lowered:
        return "The file is corrupt or uses an unsupported format."
    if "ffprobe" in lowered or "ffmpeg" in lowered:
        return "The video could not be processed. Check that FFmpeg is available."
    return "The file could not be read. Check that it still exists and is supported."


def _public_analysis_failure(error: str) -> str:
    lowered = error.lower()
    if "changed since indexing" in lowered or "changed during analysis" in lowered:
        return "The image changed during analysis. Rescan its folder before reviewing."
    if "corrupt" in lowered or "unsupported" in lowered:
        return "The image is corrupt or uses an unsupported format."
    return "The image could not be analyzed. Check that it is available and supported."


def public_media(record: dict[str, Any]) -> dict[str, Any]:
    """Expose display metadata only; source paths, hashes, and GPS remain backend-private."""
    public = {key: record.get(key) for key in (
        "id", "media_type", "extension", "filename", "size_bytes", "width", "height",
        "duration_seconds", "frame_rate", "capture_date", "camera_make", "camera_model",
        "metadata_status", "metadata_error",
    )}
    if record.get("metadata_status") == "failed":
        public["metadata_error"] = _public_scan_failure(str(record.get("metadata_error") or ""))
    return public


def _remove_cached_thumbnails(removed: int, thumbnail_paths: list[str]) -> dict[str, int]:
    deleted = 0
    failed = 0
    for path in thumbnail_paths:
        if not is_cached_thumbnail(path):
            continue
        try:
            Path(path).unlink()
            deleted += 1
        except OSError:
            failed += 1
    return {
        "removed": removed,
        "thumbnails_deleted": deleted,
        "thumbnail_cleanup_failed": failed,
    }


def thumbnail_data(media_id: int) -> dict[str, Any]:
    with connect() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT thumbnail_path, source_path, media_type, content_hash FROM media_items WHERE id = ?",
            (media_id,),
        ).fetchone()
    if not row:
        return {"data_url": None}
    thumbnail_path = row["thumbnail_path"]
    path = Path(str(thumbnail_path)) if thumbnail_path else None
    try:
        cache_root = _thumbnail_root()
        if path and path.is_symlink():
            resolved = None
        else:
            resolved = path.resolve() if path else None
        if resolved:
            resolved.relative_to(cache_root)
    except (OSError, ValueError):
        resolved = None
    if not resolved or not resolved.is_file():
        content_hash = row["content_hash"]
        source = Path(str(row["source_path"]))
        if (
            not isinstance(content_hash, str)
            or not re.fullmatch(r"[a-fA-F0-9]{64}", content_hash)
            or source.is_symlink()
            or not source.is_file()
        ):
            return {"data_url": None}
        try:
            generated = ensure_cached_thumbnail(source, str(row["media_type"]), content_hash)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            return {"data_url": None}
        if not generated:
            return {"data_url": None}
        resolved = Path(generated).resolve()
        try:
            resolved.relative_to(cache_root)
        except ValueError:
            return {"data_url": None}
        with connect() as connection:
            update_thumbnail(connection, int(media_id), str(resolved), content_hash)
    encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
    return {"data_url": f"data:image/jpeg;base64,{encoded}"}


def full_resolution_data(media_id: int) -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute(
            "SELECT source_path, media_type FROM media_items WHERE id = ?",
            (media_id,),
        ).fetchone()
    if not row or row[1] != "photo":
        raise ValueError("This photo is no longer available in the library")
    source = Path(str(row[0]))
    if source.is_symlink() or not source.is_file():
        raise ValueError("This photo is no longer available in the library")
    try:
        from PIL import Image, ImageOps  # type: ignore[import-not-found]
    except ImportError as error:
        raise ValueError("Full-resolution viewing requires Pillow in the local backend") from error
    try:
        with Image.open(str(row[0])) as image:
            oriented = ImageOps.exif_transpose(image)
            if "A" in oriented.getbands():
                background = Image.new("RGB", oriented.size, "white")
                background.paste(oriented, mask=oriented.getchannel("A"))
                rendered = background
            else:
                rendered = oriented.convert("RGB")
            output = BytesIO()
            rendered.save(output, format="JPEG", quality=95)
    except (OSError, ValueError, RuntimeError, Image.DecompressionBombError) as error:
        raise ValueError("This image is corrupt, unsupported, or no longer readable") from error
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return {"data_url": f"data:image/jpeg;base64,{encoded}"}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError("Request must be a JSON object")
    command = request.get("command")
    if command == "transfer:status":
        return {"job": transfers.status(_string_id(request, "job_id"))}
    if command == "transfer:cancel":
        return {"job": transfers.cancel(_string_id(request, "job_id"))}
    if transfers.database_maintenance_active:
        raise ValueError("Database restoration is in progress; retry after it completes")
    if command == "health":
        return health_payload()
    initialize_database()
    if command == "scan:start":
        if transfers.cache_clear_active:
            raise ValueError("Wait for thumbnail cache maintenance to finish before scanning")
        folders = request.get("folders")
        if not isinstance(folders, list) or not all(isinstance(folder, str) for folder in folders):
            raise ValueError("Scan requires an approved list of local folders")
        return {"scan": public_job(scanner.start(folders))}
    if command == "scan:status":
        return {"scan": public_job(scanner.status(_string_id(request, "scan_id"))) }
    if command == "scan:cancel":
        return {"scan": public_job(scanner.cancel(_string_id(request, "scan_id"))) }
    if command == "scan:resume":
        if transfers.cache_clear_active:
            raise ValueError("Wait for thumbnail cache maintenance to finish before scanning")
        return {"scan": public_job(scanner.resume(_string_id(request, "scan_id"))) }
    if command == "scan:retry-failed":
        if transfers.cache_clear_active:
            raise ValueError("Wait for thumbnail cache maintenance to finish before scanning")
        scan_id = request.get("scan_id")
        if not isinstance(scan_id, str):
            raise ValueError("Invalid scan identifier")
        return {"scan": public_job(scanner.retry_failed(scan_id))}
    if command == "scan:history":
        with connect() as connection:
            connection.row_factory = sqlite3.Row
            jobs = connection.execute(
                "SELECT * FROM scan_jobs ORDER BY COALESCE(completed_at, started_at) DESC, rowid DESC LIMIT 10"
            ).fetchall()
        history = []
        for row in jobs:
            job = dict(row)
            job["folders"] = json.loads(job.pop("folders_json"))
            job["cancel_requested"] = bool(job["cancel_requested"])
            history.append(public_job(job))
        return {"scans": history}
    if command == "retry:failed-list":
        with connect() as connection:
            failures = list_scan_failures(connection)
            failed_scans = list_failed_scan_jobs(connection)
            failed_analysis = list_failed_photo_analysis(connection)
            failed_analysis_jobs = list_failed_photo_analysis_jobs(connection)
        scan_items = [{
            "id": failure["id"],
            "scan_id": failure["scan_job_id"],
            "kind": "scan-item",
            "filename": Path(failure["source_path"]).name if failure["source_path"] else "Folder access",
            "media_type": failure["media_type"],
            "error_summary": _public_scan_failure(str(failure["error_summary"])),
            "created_at": failure["created_at"],
        } for failure in failures]
        scan_items.extend({
            "id": job["id"],
            "scan_id": job["id"],
            "kind": "scan-job",
            "filename": "Scan job",
            "media_type": None,
            "error_summary": "Scan stopped unexpectedly. Retry to continue indexing this selection.",
            "created_at": job.get("completed_at") or job.get("started_at"),
        } for job in failed_scans)
        analysis_items = [{
            "id": f"analysis-{item['media_id']}",
            "media_id": item["media_id"],
            "kind": "photo-analysis",
            "filename": item["filename"],
            "error_summary": _public_analysis_failure(str(item["analysis_error"])),
            "created_at": item["analyzed_at"],
        } for item in failed_analysis]
        analysis_items.extend({
            "id": job["id"],
            "kind": "photo-analysis-job",
            "filename": "Photo analysis job",
            "error_summary": "Photo analysis stopped before all pending images were processed.",
            "created_at": job.get("completed_at") or job.get("started_at"),
        } for job in failed_analysis_jobs)
        return {"scan_failures": scan_items, "analysis_failures": analysis_items}
    if command == "scan:resumable":
        with connect() as connection:
            job = latest_resumable_job(connection)
        return {"scan": public_job(job) if job else None}
    if command == "library:overview":
        with connect() as connection:
            return {"overview": library_overview(connection), "is_real_data": True}
    if command == "media:list":
        media_type = request.get("media_type")
        if not isinstance(media_type, str) or media_type not in {"all", "photo", "video"}:
            raise ValueError("Media type must be all, photo, or video")
        limit = request.get("limit", 60)
        offset = request.get("offset", 0)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Media limit must be an integer from 1 to 100")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 9_007_199_254_740_991:
            raise ValueError("Media offset must be a non-negative safe integer")
        filters = _media_filters(request)
        with connect() as connection:
            items = [public_media(item) for item in list_media(
                connection, media_type, limit, offset, **media_filter_arguments(filters), display_only=True
            )]
        return {"items": items}
    if command == "media:count":
        media_type = request.get("media_type")
        if not isinstance(media_type, str) or media_type not in {"all", "photo", "video"}:
            raise ValueError("Media type must be all, photo, or video")
        filters = _media_filters(request)
        with connect() as connection:
            total = count_media(
                connection, media_type, **media_filter_arguments(filters, include_sort=False)
            )
        return {"count": total}
    if command == "media:remove-selected":
        mode = request.get("mode")
        if mode == "ids":
            raw_ids = request.get("media_ids")
            if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 5000:
                raise ValueError("Select between 1 and 5000 media items")
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in raw_ids):
                raise ValueError("Invalid media identifier")
            media_ids = list(dict.fromkeys(raw_ids))
        elif mode == "filtered":
            media_type = request.get("media_type")
            filters = request.get("filters")
            excluded_ids = request.get("excluded_ids", [])
            if not isinstance(media_type, str) or media_type not in {"all", "photo", "video"}:
                raise ValueError("Media type must be all, photo, or video")
            if not isinstance(filters, dict):
                raise ValueError("Invalid media filters")
            if not isinstance(excluded_ids, list) or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in excluded_ids
            ):
                raise ValueError("Invalid excluded media identifiers")
            normalized_filters = _media_filters(filters)
            arguments = media_filter_arguments(normalized_filters, include_sort=False)
            with connect() as connection:
                removed, thumbnail_paths = remove_matching_media(
                    connection,
                    media_type,
                    arguments["search"],
                    arguments["extension"],
                    arguments["date_from"],
                    arguments["date_to"],
                    arguments["camera_make"],
                    arguments["camera_model"],
                    excluded_ids,
                    arguments["numeric_filters"],
                )
            return _remove_cached_thumbnails(removed, thumbnail_paths)
        else:
            raise ValueError("Invalid media selection")
        with connect() as connection:
            removed, thumbnail_paths = remove_media_records(connection, media_ids)
        return _remove_cached_thumbnails(removed, thumbnail_paths)
    if command == "media:thumbnail":
        return thumbnail_data(_positive_int(request, "media_id"))
    if command == "media:full-resolution":
        media_id = request.get("media_id")
        if isinstance(media_id, bool) or not isinstance(media_id, int) or media_id < 1:
            raise ValueError("Invalid media identifier")
        result = full_resolution_data(media_id)
        with connect() as connection:
            record_media_viewed(connection, media_id)
        return result
    if command == "settings:overview":
        return {"settings": settings_overview()}
    if command == "settings:set-scan-preference":
        return {"skip_unchanged_files": set_skip_unchanged_files(request.get("skip_unchanged_files"))}
    if command == "settings:clear-cache":
        if scanner.is_running:
            raise ValueError("Stop the active scan before clearing the thumbnail cache")
        return {"job": transfers.start_cache_clear()}
    if command == "export:media":
        return {"job": transfers.start_media_export(
            request.get("mode"), request.get("destination"), request.get("media_ids"),
            request.get("album_id"), request.get("media_type", "all"),
            request.get("filters"), request.get("organize_by_type", False),
        )}
    if command == "export:metadata":
        return {"job": transfers.start_metadata_export(request.get("destination"), request.get("format"))}
    if command == "backup:create":
        return {"job": transfers.start_backup(request.get("destination"))}
    if command == "backup:restore":
        return {"job": transfers.start_restore(
            request.get("source"), scanner.is_running or photo_review.is_running
        )}
    if command == "albums:list":
        with connect() as connection:
            albums = list_albums(connection)
        return {"albums": albums}
    if command == "albums:create":
        name = request.get("name")
        description = request.get("description", "")
        with connect() as connection:
            album = create_album(connection, name, description)
        return {"album": album}
    if command == "albums:rename":
        album_id = request.get("album_id")
        name = request.get("name")
        description = request.get("description", "")
        with connect() as connection:
            album = rename_album(connection, album_id, name, description)
        return {"album": album}
    if command == "albums:delete":
        with connect() as connection:
            delete_album(connection, request.get("album_id"))
        return {"deleted": True}
    if command in {"albums:add-media", "albums:remove-media"}:
        with connect() as connection:
            result = (
                add_album_media(connection, request.get("album_id"), request.get("media_ids"))
                if command == "albums:add-media"
                else remove_album_media(connection, request.get("album_id"), request.get("media_ids"))
            )
        return result
    if command == "albums:media":
        limit = request.get("limit", 60)
        offset = request.get("offset", 0)
        with connect() as connection:
            items = [public_media(item) for item in list_album_media(
                connection, request.get("album_id"), limit, offset
            )]
        return {"items": items}
    if command == "smart-albums:list":
        with connect() as connection:
            return {"albums": list_smart_albums(connection)}
    if command == "smart-albums:media":
        limit = request.get("limit", 60)
        offset = request.get("offset", 0)
        with connect() as connection:
            items = [public_media(item) for item in list_smart_album_media(
                connection, request.get("kind"), request.get("value"), limit, offset
            )]
        return {"items": items}
    if command == "smart-collections:list":
        with connect() as connection:
            collections = list_smart_collections(connection)
        return {"collections": collections}
    if command == "smart-collections:media":
        limit = request.get("limit", 60)
        offset = request.get("offset", 0)
        with connect() as connection:
            items = [public_media(item) for item in list_smart_collection_media(
                connection, request.get("kind"), request.get("value"), limit, offset
            )]
        return {"items": items}
    if command == "saved-smart-albums:list":
        with connect() as connection:
            albums = list_saved_smart_albums(connection)
        return {"albums": albums}
    if command == "saved-smart-albums:create":
        with connect() as connection:
            album = create_saved_smart_album(
                connection, request.get("name"), request.get("description", ""),
                request.get("media_type"), request.get("filters"),
            )
        return {"album": album}
    if command == "saved-smart-albums:delete":
        with connect() as connection:
            delete_saved_smart_album(connection, request.get("album_id"))
        return {"deleted": True}
    if command == "saved-smart-albums:media":
        limit = request.get("limit", 60)
        offset = request.get("offset", 0)
        with connect() as connection:
            items = [public_media(item) for item in list_saved_smart_album_media(
                connection, request.get("album_id"), limit, offset
            )]
        return {"items": items}
    if command == "occasions:list":
        group_by = request.get("group_by", "month")
        limit = request.get("limit", 100)
        offset = request.get("offset", 0)
        date_from = _optional_date(request, "date_from")
        date_to = _optional_date(request, "date_to")
        with connect() as connection:
            occasions = list_occasions(connection, group_by, date_from, date_to, limit, offset)
        return {"occasions": occasions}
    if command == "occasions:media":
        limit = request.get("limit", 60)
        offset = request.get("offset", 0)
        with connect() as connection:
            items = [public_media(item) for item in list_occasion_media(
                connection, request.get("group_by"), request.get("period"),
                request.get("date_source"), limit, offset
            )]
        return {"items": items}
    if command == "photo-review:start":
        threshold = request.get("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD)
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= MAX_SIMILARITY_THRESHOLD:
            raise ValueError(f"Similarity threshold must be between 0 and {MAX_SIMILARITY_THRESHOLD}")
        return {"job": photo_review.start(threshold)}
    if command == "photo-review:retry-failed":
        threshold = request.get("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD)
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= MAX_SIMILARITY_THRESHOLD:
            raise ValueError(f"Similarity threshold must be between 0 and {MAX_SIMILARITY_THRESHOLD}")
        return {"job": photo_review.retry_failed(threshold)}
    if command == "photo-review:status":
        job_id = request.get("job_id")
        if job_id is not None and not isinstance(job_id, str):
            raise ValueError("Invalid photo analysis job identifier")
        return {"job": photo_review.status(job_id)}
    if command == "photo-review:cancel":
        job_id = request.get("job_id")
        if not isinstance(job_id, str):
            raise ValueError("Invalid photo analysis job identifier")
        return {"job": photo_review.cancel(job_id)}
    if command == "photo-review:overview":
        threshold = request.get("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD)
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= MAX_SIMILARITY_THRESHOLD:
            raise ValueError(f"Similarity threshold must be between 0 and {MAX_SIMILARITY_THRESHOLD}")
        return {"overview": review_overview(threshold)}
    if command == "photo-review:list":
        category = request.get("category")
        status = request.get("status", "pending")
        limit = request.get("limit", 30)
        offset = request.get("offset", 0)
        threshold = request.get("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD)
        if not isinstance(category, str):
            raise ValueError("Invalid photo review category")
        if not isinstance(status, str):
            raise ValueError("Invalid review status")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Review page size must be an integer from 1 to 100")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 9_007_199_254_740_991:
            raise ValueError("Review page offset must be a non-negative safe integer")
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= MAX_SIMILARITY_THRESHOLD:
            raise ValueError(f"Similarity threshold must be between 0 and {MAX_SIMILARITY_THRESHOLD}")
        return {"result": list_review(category, limit, offset, status, threshold)}
    if command == "photo-review:set-status":
        media_id = request.get("media_id")
        status = request.get("status")
        if isinstance(media_id, bool) or not isinstance(media_id, int):
            raise ValueError("Invalid media identifier")
        if not isinstance(status, str):
            raise ValueError("Invalid review status")
        return set_review_status(media_id, status)
    raise ValueError("Unknown command")


def serve() -> int:
    initialize_database(recover_interrupted_scans=True)
    for raw_line in sys.stdin:
        if len(raw_line) > 1_000_000:
            print(json.dumps({"id": None, "ok": False, "error": "Request is too large"}), flush=True)
            continue
        request_id: Any = None
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("Request must be a JSON object")
            if not isinstance(request.get("command"), str):
                raise ValueError("Request command must be a string")
            request_id = request.get("id")
            response = {"id": request_id, "ok": True, **handle(request)}
        except (json.JSONDecodeError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
            if isinstance(error, sqlite3.OperationalError) and (
                "locked" in str(error).lower() or "busy" in str(error).lower()
            ):
                message = "The PhotoFlow database is busy. Stop maintenance or retry shortly."
            else:
                message = str(error)
            response = {"id": request_id, "ok": False, "error": message}
        print(json.dumps(response), flush=True)
    transfers.shutdown()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PhotoFlow local backend")
    parser.add_argument("--health-check", action="store_true", help="Run SQLite migrations and emit health JSON")
    parser.add_argument("--serve", action="store_true", help="Serve JSON Lines requests on stdin/stdout")
    arguments = parser.parse_args()
    if arguments.health_check:
        print(json.dumps(health_payload()))
        return 0
    if arguments.serve:
        return serve()
    parser.error("Specify --health-check or --serve")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
