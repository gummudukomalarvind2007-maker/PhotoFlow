from __future__ import annotations

import csv
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import quote

from photoflow_backend.albums import get_album, list_album_media
from photoflow_backend.database import application_data_dir, connect, database_path, thumbnails_dir
from photoflow_backend.media_filters import media_filter_arguments, normalize_media_filters
from photoflow_backend.migrations import MIGRATIONS_DIR
from photoflow_backend.repository import (
    MEDIA_COLUMNS,
    count_media,
    list_media,
)


LOGGER = logging.getLogger("photoflow.transfers")
PAGE_SIZE = 100
MEDIA_TYPES = {"all", "photo", "video"}
TRANSFER_HISTORY_LIMIT = 20
TRANSFER_PERSIST_INTERVAL = 0.5
TRANSFER_PERSIST_WARNING = (
    "Recovery status could not be saved. This operation may appear interrupted after a restart."
)
METADATA_FIELDS = (
    "filename", "media_type", "extension", "size_bytes", "width", "height",
    "capture_date", "camera_make", "camera_model", "duration_seconds", "frame_rate",
)


class TransferCancelled(Exception):
    pass


def _safe_csv_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if re.match(r"^[\s\x00-\x1f]*[=+\-@]", value):
        return "'" + value
    return value


def _positive_id(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Invalid {label} identifier")
    return value


def _target_directory(value: Any) -> Path:
    if not isinstance(value, str) or not value or len(value) > 32767:
        raise ValueError("Invalid export destination")
    try:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("Export destination must be an absolute path")
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("Export destination is unavailable") from error
    if not resolved.is_dir():
        raise ValueError("Export destination must be a folder")
    return resolved


def _target_file(value: Any, allowed_suffixes: set[str]) -> Path:
    if not isinstance(value, str) or not value or len(value) > 32767:
        raise ValueError("Invalid destination file")
    path = Path(value)
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise ValueError("Destination must be an absolute file path")
    if path.suffix.lower() not in allowed_suffixes:
        raise ValueError("Unsupported destination file type")
    try:
        parent = path.parent.resolve(strict=True)
        target = parent / path.name
    except (OSError, RuntimeError) as error:
        raise ValueError("Destination folder is unavailable") from error
    if not parent.is_dir():
        raise ValueError("Destination folder is unavailable")
    if target.exists():
        raise ValueError("Destination already exists; choose a different filename")
    return target


def _validated_filters(media_type: Any, filters: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(media_type, str) or media_type not in MEDIA_TYPES:
        raise ValueError("Invalid media type")
    return media_type, normalize_media_filters(filters)


def _media_arguments(filters: dict[str, Any]) -> dict[str, Any]:
    return media_filter_arguments(filters)


def _iter_selected(media_ids: list[int]) -> Iterator[dict[str, Any]]:
    for start in range(0, len(media_ids), 500):
        group = media_ids[start:start + 500]
        placeholders = ",".join("?" for _ in group)
        with connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"SELECT {MEDIA_COLUMNS} FROM media_items WHERE id IN ({placeholders}) ORDER BY id",
                group,
            ).fetchall()
        yield from (dict(row) for row in rows)


def _iter_album(album_id: int) -> Iterator[dict[str, Any]]:
    offset = 0
    while True:
        with connect() as connection:
            rows = list_album_media(connection, album_id, PAGE_SIZE, offset)
        if not rows:
            return
        yield from rows
        offset += len(rows)
        if len(rows) < PAGE_SIZE:
            return


def _iter_filtered(media_type: str, filters: dict[str, Any]) -> Iterator[dict[str, Any]]:
    offset = 0
    while True:
        with connect() as connection:
            rows = list_media(connection, media_type, PAGE_SIZE, offset, **_media_arguments(filters))
        if not rows:
            return
        yield from rows
        offset += len(rows)
        if len(rows) < PAGE_SIZE:
            return


def _new_temporary_output(
    directory: Path, mode: str = "wb", **options: Any
) -> tuple[Path, Any]:
    for _ in range(10):
        temporary = directory / f".photoflow-transfer-{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
                0o600,
            )
            return temporary, os.fdopen(descriptor, mode, **options)
        except FileExistsError:
            continue
    raise OSError("Could not allocate a temporary export file")


def _commit_without_overwrite(temporary: Path, destination: Path) -> None:
    if os.name == "nt":
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)
        temporary.unlink()


def _export_targets(directory: Path, filename: str) -> Iterator[Path]:
    safe_name = Path(filename.replace("\\", "/")).name
    if not safe_name or safe_name in {".", ".."}:
        safe_name = "media"
    original = Path(safe_name)
    stem, suffix = original.stem, original.suffix
    for index in range(100_000):
        name = safe_name if index == 0 else f"{stem} ({index}){suffix}"
        yield directory / name


def _copy_file(source: Path, destination_dir: Path, filename: str, cancelled: threading.Event) -> None:
    if source.is_symlink():
        raise ValueError("Indexed media file is a symbolic link")
    if not source.is_file():
        raise FileNotFoundError("Indexed media file is unavailable")
    temporary, output = _new_temporary_output(destination_dir)
    try:
        with output:
            with source.open("rb") as input_file:
                while True:
                    if cancelled.is_set():
                        raise TransferCancelled()
                    block = input_file.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
            output.flush()
            os.fsync(output.fileno())
        for target in _export_targets(destination_dir, filename):
            try:
                _commit_without_overwrite(temporary, target)
                return
            except FileExistsError:
                continue
        raise OSError("Could not allocate a unique export filename")
    finally:
        temporary.unlink(missing_ok=True)


def _sqlite_backup(
    source_path: Path,
    target_path: Path,
    cancelled: threading.Event,
    progress: Any = None,
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(source_path, timeout=30)
    target = sqlite3.connect(target_path, timeout=30)
    try:
        target.execute("PRAGMA journal_mode = DELETE")

        def on_progress(status: int, remaining: int, total: int) -> None:
            if cancelled.is_set():
                raise TransferCancelled()
            if progress:
                progress(max(0, total - remaining), total)

        source.backup(target, pages=128, progress=on_progress, sleep=0.05)
        target.commit()
    finally:
        target.close()
        source.close()


def _validate_backup(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Backup file is missing or empty")
    uri = f"file:{quote(path.as_posix(), safe='/:')}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=30)
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup integrity check failed")
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        required = {"schema_migrations", "media_items", "scan_jobs", "albums", "album_members"}
        if not required.issubset(tables):
            raise ValueError("Backup does not contain a compatible PhotoFlow library")
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        required_migrations = {migration.name for migration in MIGRATIONS_DIR.glob("*.sql")}
        if not required_migrations.issubset(applied):
            raise ValueError("Backup does not contain a compatible PhotoFlow schema")
    except sqlite3.Error as error:
        raise ValueError("Backup file is invalid or incomplete") from error
    finally:
        if connection is not None:
            connection.close()


class TransferManager:
    """Runs cancellable file transfers and SQLite maintenance without blocking JSON Lines."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancellations: dict[str, threading.Event] = {}
        self._active_id: str | None = None
        self._active_thread: threading.Thread | None = None
        self._database_maintenance = False
        self._loaded = False
        self._last_persisted_at = 0.0

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        state_file = application_data_dir() / "transfer-jobs.json"
        try:
            records = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError("Transfer recovery state must be a list")
            jobs: dict[str, dict[str, Any]] = {}
            for record in records[-TRANSFER_HISTORY_LIMIT:]:
                if not isinstance(record, dict):
                    raise ValueError("Transfer recovery record must be an object")
                job_id = record.get("id")
                operation = record.get("operation")
                status = record.get("status")
                if not isinstance(job_id, str) or not isinstance(operation, str):
                    raise ValueError("Transfer recovery record has invalid identifiers")
                if status not in {"running", "completed", "cancelled", "interrupted", "failed"}:
                    raise ValueError("Transfer recovery record has an invalid status")
                if any(type(record.get(key)) is not int for key in ("total", "completed", "succeeded", "failed")):
                    raise ValueError("Transfer recovery record has invalid progress")
                if type(record.get("cancel_requested")) is not bool:
                    raise ValueError("Transfer recovery record has an invalid cancellation flag")
                error_summary = record.get("error_summary")
                if error_summary is not None and not isinstance(error_summary, str):
                    raise ValueError("Transfer recovery record has an invalid error")
                job = {
                    "id": job_id,
                    "operation": operation,
                    "status": status,
                    "total": record["total"],
                    "completed": record["completed"],
                    "succeeded": record["succeeded"],
                    "failed": record["failed"],
                    "cancel_requested": record["cancel_requested"],
                    "error_summary": error_summary,
                }
                if status == "running":
                    job["status"] = "interrupted"
                    job["error_summary"] = (
                        "The operation was interrupted and was not resumed automatically. "
                        "Check its output before retrying."
                    )
                jobs[job_id] = job
            self._jobs = jobs
        except FileNotFoundError:
            return
        except (OSError, TypeError, ValueError):
            LOGGER.warning("Previous transfer recovery history is unavailable", exc_info=True)
            return
        if any(job["status"] == "interrupted" for job in self._jobs.values()):
            self._persist_jobs()

    def _persist_jobs(self) -> bool:
        temporary: Path | None = None
        try:
            root = application_data_dir()
            temporary, stream = _new_temporary_output(root, "w", encoding="utf-8")
            with stream:
                json.dump(list(self._jobs.values()), stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, root / "transfer-jobs.json")
            self._last_persisted_at = time.monotonic()
            return True
        except OSError:
            LOGGER.warning("Transfer recovery history could not be saved", exc_info=True)
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning("Transfer recovery staging file could not be removed")

    @property
    def database_maintenance_active(self) -> bool:
        with self._lock:
            return self._database_maintenance

    @property
    def cache_clear_active(self) -> bool:
        with self._lock:
            return bool(
                self._active_id
                and self._jobs.get(self._active_id, {}).get("operation") == "cache-clear"
                and self._jobs.get(self._active_id, {}).get("status") == "running"
            )

    def _create_job(self, operation: str, total: int, maintenance: bool = False) -> tuple[str, threading.Event]:
        with self._lock:
            self._ensure_loaded()
            if self._active_id and self._jobs.get(self._active_id, {}).get("status") == "running":
                raise ValueError("Another export or maintenance operation is already running")
            job_id = str(uuid.uuid4())
            cancellation = threading.Event()
            self._jobs[job_id] = {
                "id": job_id, "operation": operation, "status": "running",
                "total": max(0, total), "completed": 0, "succeeded": 0, "failed": 0,
                "cancel_requested": False, "error_summary": None,
            }
            self._cancellations[job_id] = cancellation
            self._active_id = job_id
            self._database_maintenance = maintenance
            self._jobs = dict(list(self._jobs.items())[-TRANSFER_HISTORY_LIMIT:])
            if not self._persist_jobs():
                self._jobs[job_id]["error_summary"] = TRANSFER_PERSIST_WARNING
            return job_id, cancellation

    def _start_thread(self, job_id: str, target: Any, args: tuple[Any, ...]) -> dict[str, Any]:
        thread = threading.Thread(target=target, args=(job_id, *args), daemon=True, name=f"photoflow-{job_id[:8]}")
        with self._lock:
            self._active_thread = thread
        try:
            thread.start()
        except Exception:
            self._update(job_id, failed=1)
            self._finish(job_id, "failed", "Could not start this operation. Try again.")
            raise
        return self.status(job_id)

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            self._ensure_loaded()
            job = self._jobs.get(job_id)
            if job:
                job.update(values)
                now = time.monotonic()
                if (
                    now - self._last_persisted_at >= TRANSFER_PERSIST_INTERVAL
                    or (job["total"] > 0 and job["completed"] >= job["total"])
                ):
                    if job.get("error_summary") == TRANSFER_PERSIST_WARNING:
                        job["error_summary"] = None
                    if not self._persist_jobs() and not job.get("error_summary"):
                        job["error_summary"] = TRANSFER_PERSIST_WARNING

    def _finish(self, job_id: str, status: str, error_summary: str | None = None) -> None:
        with self._lock:
            self._ensure_loaded()
            job = self._jobs.get(job_id)
            if job:
                job.update(status=status, error_summary=error_summary)
                if not self._persist_jobs() and not error_summary:
                    job["error_summary"] = TRANSFER_PERSIST_WARNING
            self._cancellations.pop(job_id, None)
            if self._active_id == job_id:
                self._active_id = None
                self._active_thread = None
                self._database_maintenance = False

    def start_media_export(
        self,
        mode: Any,
        destination: Any,
        media_ids: Any = None,
        album_id: Any = None,
        media_type: Any = "all",
        filters: Any = None,
        organize_by_type: Any = False,
    ) -> dict[str, Any]:
        destination_path = _target_directory(destination)
        if not isinstance(organize_by_type, bool):
            raise ValueError("Invalid export organization option")
        if mode == "selected":
            if not isinstance(media_ids, list) or not media_ids or len(media_ids) > 5000:
                raise ValueError("Select between 1 and 5000 media items")
            ids = list(dict.fromkeys(_positive_id(value, "media") for value in media_ids))
            with connect() as connection:
                count = 0
                for start in range(0, len(ids), 500):
                    group = ids[start:start + 500]
                    placeholders = ",".join("?" for _ in group)
                    count += connection.execute(
                        f"SELECT COUNT(*) FROM media_items WHERE id IN ({placeholders})", group
                    ).fetchone()[0]
            if count != len(ids):
                raise ValueError("One or more selected media items are unavailable")
            criteria = ids
            total = len(ids)
        elif mode == "album":
            album_id = _positive_id(album_id, "album")
            with connect() as connection:
                get_album(connection, album_id)
                total = int(connection.execute(
                    "SELECT COUNT(*) FROM album_members WHERE album_id = ?", (album_id,)
                ).fetchone()[0])
            criteria = album_id
        elif mode == "filtered":
            media_type, parsed = _validated_filters(media_type, {} if filters is None else filters)
            with connect() as connection:
                count_arguments = _media_arguments(parsed)
                count_arguments.pop("sort")
                total = count_media(connection, media_type, **count_arguments)
            criteria = (media_type, parsed)
        else:
            raise ValueError("Invalid export selection")

        job_id, cancellation = self._create_job("media-export", total)
        return self._start_thread(
            job_id, self._run_media_export,
            (mode, criteria, destination_path, organize_by_type, cancellation),
        )

    def _run_media_export(
        self,
        job_id: str,
        mode: str,
        criteria: Any,
        destination: Path,
        organize_by_type: bool,
        cancellation: threading.Event,
    ) -> None:
        failed = 0
        completed = 0
        succeeded = 0
        try:
            records = (
                _iter_selected(criteria) if mode == "selected"
                else _iter_album(criteria) if mode == "album"
                else _iter_filtered(criteria[0], criteria[1])
            )
            for record in records:
                if cancellation.is_set():
                    break
                try:
                    target_dir = destination
                    if organize_by_type:
                        target_dir = destination / ("Photos" if record["media_type"] == "photo" else "Videos")
                        if target_dir.is_symlink():
                            raise ValueError("Export category folder is not a local destination")
                        target_dir.mkdir(exist_ok=True)
                        target_dir = target_dir.resolve(strict=True)
                        target_dir.relative_to(destination)
                    _copy_file(Path(str(record["source_path"])), target_dir, str(record["filename"]), cancellation)
                    succeeded += 1
                except TransferCancelled:
                    completed += 1
                    self._update(job_id, completed=completed, succeeded=succeeded, failed=failed)
                    break
                except (OSError, ValueError):
                    failed += 1
                completed += 1
                self._update(job_id, completed=completed, succeeded=succeeded, failed=failed)
            status = "cancelled" if cancellation.is_set() else "completed"
            self._finish(
                job_id, status,
                "Some media could not be exported. Check source file and destination access." if failed else None,
            )
        except Exception:
            LOGGER.error("Media export failed")
            self._update(job_id, failed=max(1, failed))
            self._finish(job_id, "failed", "Media export failed. Check destination access and try again.")

    def start_metadata_export(self, destination: Any, format_name: Any) -> dict[str, Any]:
        if format_name not in {"csv", "json"}:
            raise ValueError("Metadata format must be CSV or JSON")
        output = _target_file(destination, {f".{format_name}"})
        with connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0])
        job_id, cancellation = self._create_job("metadata-export", total)
        return self._start_thread(job_id, self._run_metadata_export, (output, format_name, cancellation))

    def _run_metadata_export(
        self, job_id: str, output: Path, format_name: str, cancellation: threading.Event
    ) -> None:
        temporary: Path | None = None
        completed = 0
        try:
            temporary, stream = _new_temporary_output(
                output.parent, "w", encoding="utf-8", newline=""
            )
            with stream:
                writer: Any = None
                if format_name == "csv":
                    writer = csv.DictWriter(stream, fieldnames=METADATA_FIELDS)
                    writer.writeheader()
                else:
                    stream.write("[")
                offset = 0
                first = True
                while True:
                    if cancellation.is_set():
                        raise TransferCancelled()
                    with connect() as connection:
                        rows = list_media(connection, "all", PAGE_SIZE, offset)
                    if not rows:
                        break
                    for row in rows:
                        if cancellation.is_set():
                            raise TransferCancelled()
                        record = {field: row.get(field) for field in METADATA_FIELDS}
                        if format_name == "csv":
                            writer.writerow({
                                field: _safe_csv_text(value)
                                if field in {"filename", "media_type", "extension", "capture_date", "camera_make", "camera_model"}
                                else value
                                for field, value in record.items()
                            })
                        else:
                            if not first:
                                stream.write(",")
                            json.dump(record, stream, ensure_ascii=False, separators=(",", ":"))
                            first = False
                        completed += 1
                        self._update(job_id, completed=completed, succeeded=completed)
                    offset += len(rows)
                    if len(rows) < PAGE_SIZE:
                        break
                if format_name == "json":
                    stream.write("]\n")
                stream.flush()
                os.fsync(stream.fileno())
            _commit_without_overwrite(temporary, output)
            temporary = None
            self._finish(job_id, "completed")
        except TransferCancelled:
            if temporary:
                temporary.unlink(missing_ok=True)
            self._finish(job_id, "cancelled")
        except Exception:
            LOGGER.error("Metadata export failed")
            if temporary:
                temporary.unlink(missing_ok=True)
            self._update(job_id, failed=1)
            self._finish(job_id, "failed", "Metadata export failed. Check destination access and try again.")

    def start_backup(self, destination: Any) -> dict[str, Any]:
        output = _target_file(destination, {".db", ".sqlite", ".sqlite3"})
        job_id, cancellation = self._create_job("backup", 1)
        return self._start_thread(job_id, self._run_backup, (output, cancellation))

    def _run_backup(self, job_id: str, output: Path, cancellation: threading.Event) -> None:
        temporary: Path | None = None
        try:
            temporary, stream = _new_temporary_output(output.parent)
            stream.close()

            def progress(completed: int, total: int) -> None:
                self._update(job_id, completed=completed, total=max(1, total))

            _sqlite_backup(database_path(), temporary, cancellation, progress)
            _validate_backup(temporary)
            _commit_without_overwrite(temporary, output)
            temporary = None
            self._update(job_id, completed=1, total=1, succeeded=1)
            self._finish(job_id, "completed")
        except TransferCancelled:
            if temporary:
                temporary.unlink(missing_ok=True)
            self._finish(job_id, "cancelled")
        except Exception:
            LOGGER.error("Database backup failed")
            if temporary:
                temporary.unlink(missing_ok=True)
            self._update(job_id, failed=1)
            self._finish(job_id, "failed", "Database backup failed. Check destination access and try again.")

    def start_restore(self, source: Any, external_work_running: bool = False) -> dict[str, Any]:
        if external_work_running:
            raise ValueError("Stop scanning and photo analysis before restoring a database backup")
        source_path = _target_file_source(source)
        if source_path == database_path().resolve():
            raise ValueError("The current PhotoFlow database cannot be restored as a backup")
        job_id, cancellation = self._create_job("restore", 100, maintenance=True)
        return self._start_thread(job_id, self._run_restore, (source_path, cancellation))

    def _run_restore(self, job_id: str, source: Path, cancellation: threading.Event) -> None:
        temporary = application_data_dir() / f".photoflow-restore-{uuid.uuid4().hex}.tmp"
        try:
            _validate_backup(source)
            if cancellation.is_set():
                raise TransferCancelled()
            self._update(job_id, completed=5)

            safety_dir = application_data_dir() / "backups"
            safety_dir.mkdir(parents=True, exist_ok=True)
            if safety_dir.is_symlink() or not safety_dir.is_dir():
                raise ValueError("The application safety-backup folder is unavailable")
            safety_path = safety_dir / f"pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{job_id[:8]}.db"
            safety_temporary, safety_stream = _new_temporary_output(safety_dir)
            safety_stream.close()

            def safety_progress(completed: int, total: int) -> None:
                self._update(job_id, completed=5 + int(40 * completed / max(1, total)))

            try:
                _sqlite_backup(database_path(), safety_temporary, cancellation, safety_progress)
                _validate_backup(safety_temporary)
                _commit_without_overwrite(safety_temporary, safety_path)
            except BaseException:
                safety_temporary.unlink(missing_ok=True)
                raise
            if cancellation.is_set():
                raise TransferCancelled()
            self._update(job_id, completed=50)

            _sqlite_backup(source, temporary, cancellation, lambda completed, total: self._update(
                job_id, completed=50 + int(45 * completed / max(1, total))
            ))
            _validate_backup(temporary)
            if cancellation.is_set():
                raise TransferCancelled()

            current = sqlite3.connect(database_path(), timeout=30)
            try:
                checkpoint = current.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint and checkpoint[0] != 0:
                    raise ValueError("Could not safely checkpoint the current database")
            finally:
                current.close()
            wal_path = Path(str(database_path()) + "-wal")
            shm_path = Path(str(database_path()) + "-shm")
            if wal_path.exists() or shm_path.exists():
                raise ValueError("The current database is still in use")
            os.replace(temporary, database_path())
            self._update(job_id, completed=100, succeeded=1)
            self._finish(job_id, "completed")
        except TransferCancelled:
            temporary.unlink(missing_ok=True)
            self._finish(job_id, "cancelled")
        except Exception:
            LOGGER.error("Database restore failed")
            temporary.unlink(missing_ok=True)
            self._update(job_id, failed=1)
            self._finish(job_id, "failed", "Restore failed. The current library was kept; check that the backup is valid and PhotoFlow is idle.")

    def start_cache_clear(self) -> dict[str, Any]:
        root = _thumbnail_root()
        entries = list(root.iterdir())
        total = sum(1 for entry in entries for _ in _walk_files(entry)) if entries else 0
        job_id, cancellation = self._create_job("cache-clear", total)
        return self._start_thread(job_id, self._run_cache_clear, (root, entries, cancellation))

    def _run_cache_clear(
        self, job_id: str, root: Path, entries: list[Path], cancellation: threading.Event
    ) -> None:
        completed = 0
        try:
            for entry in entries:
                if cancellation.is_set():
                    break
                if entry.is_symlink() or entry.is_file():
                    entry.unlink(missing_ok=True)
                    completed += 1
                elif entry.is_dir():
                    for current, directories, filenames in os.walk(entry, topdown=False, followlinks=False):
                        current_path = Path(current)
                        for filename in filenames:
                            if cancellation.is_set():
                                break
                            (current_path / filename).unlink(missing_ok=True)
                            completed += 1
                            self._update(job_id, completed=completed, succeeded=completed)
                        if cancellation.is_set():
                            break
                        for directory in directories:
                            child_directory = current_path / directory
                            if child_directory.is_symlink():
                                child_directory.unlink()
                            else:
                                child_directory.rmdir()
                        current_path.rmdir()
                self._update(job_id, completed=completed, succeeded=completed)
            self._finish(job_id, "cancelled" if cancellation.is_set() else "completed")
        except Exception:
            LOGGER.error("Thumbnail cache clear failed")
            self._update(job_id, failed=1)
            self._finish(job_id, "failed", "Could not clear the thumbnail cache. Check local cache access.")

    def status(self, job_id: Any) -> dict[str, Any]:
        if not isinstance(job_id, str):
            raise ValueError("Invalid transfer identifier")
        with self._lock:
            self._ensure_loaded()
            job = self._jobs.get(job_id)
            if not job:
                raise ValueError("Unknown transfer operation")
            return dict(job)

    def cancel(self, job_id: Any) -> dict[str, Any]:
        if not isinstance(job_id, str):
            raise ValueError("Invalid transfer identifier")
        with self._lock:
            self._ensure_loaded()
            job = self._jobs.get(job_id)
            cancellation = self._cancellations.get(job_id)
            if not job or not cancellation:
                raise ValueError("Transfer operation is not running")
            cancellation.set()
            job["cancel_requested"] = True
            if not self._persist_jobs() and not job.get("error_summary"):
                job["error_summary"] = TRANSFER_PERSIST_WARNING
            return dict(job)

    def shutdown(self) -> None:
        with self._lock:
            if self._active_id:
                cancellation = self._cancellations.get(self._active_id)
                if cancellation:
                    cancellation.set()
            thread = self._active_thread
        if thread:
            thread.join(timeout=3)


def cleanup_abandoned_temporary_files() -> int:
    """Remove only restore/export temp files owned by PhotoFlow's application data directory."""
    root = application_data_dir()
    removed = 0
    backup_directory = root / "backups"
    abandoned = list(root.glob(".photoflow-restore-*.tmp"))
    abandoned.extend(root.glob(".photoflow-transfer-*.tmp"))
    if backup_directory.is_dir() and not backup_directory.is_symlink():
        abandoned.extend(backup_directory.glob(".photoflow-transfer-*.tmp"))
    for path in abandoned:
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
                removed += 1
        except OSError:
            LOGGER.warning("Could not remove an abandoned application temporary file")
            continue
    return removed


def _target_file_source(value: Any) -> Path:
    if not isinstance(value, str) or not value or len(value) > 32767:
        raise ValueError("Invalid backup file")
    try:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("Backup path must be absolute")
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("Backup file is unavailable") from error
    if not resolved.is_file():
        raise ValueError("Backup file must be a file")
    return resolved


def _thumbnail_root() -> Path:
    data_root = application_data_dir().resolve()
    expected = data_root / "thumbnails"
    if expected.is_symlink():
        raise ValueError("Thumbnail cache location is invalid")
    root = thumbnails_dir().resolve()
    if root.parent != data_root or root.name != "thumbnails":
        raise ValueError("Thumbnail cache location is invalid")
    return root


def _walk_files(path: Path) -> Iterator[Path]:
    if path.is_symlink():
        yield path
    elif path.is_file():
        yield path
    elif path.is_dir():
        for current, directories, filenames in os.walk(path, followlinks=False):
            for directory in directories:
                child = Path(current, directory)
                if child.is_symlink():
                    yield child
            for filename in filenames:
                yield Path(current, filename)

