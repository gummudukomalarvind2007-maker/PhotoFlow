from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from photoflow_backend.database import connect
from photoflow_backend.metadata import classify, ensure_cached_thumbnail, inspect_media, is_cached_thumbnail, sha256_file
from photoflow_backend.repository import (
    create_job,
    get_job,
    get_media_by_path,
    record_scan_failure,
    resolve_scan_failures,
    touch_existing,
    update_job,
    update_thumbnail,
    upsert_media,
)


class ScanManager:
    """Owns background, cancellable scans. Source files are opened read-only only."""

    def __init__(self) -> None:
        self._cancellations: dict[str, threading.Event] = {}
        self._active_folders: set[str] = set()
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(self._cancellations)

    def start(self, folders: list[str], resumed_from_id: str | None = None) -> dict[str, Any]:
        normalized: list[str] = []
        for folder in folders:
            try:
                path = Path(folder)
                if path.is_symlink():
                    raise ValueError("Scan folders must not be symbolic links")
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError):
                raise ValueError("Scan folders must be existing local directories")
            if not resolved.is_dir():
                raise ValueError("Scan folders must be existing local directories")
            normalized.append(str(resolved))
        normalized = sorted(set(normalized))
        if not normalized:
            raise ValueError("Scan folders must be existing local directories")
        with connect() as connection:
            preference = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'skip_unchanged_files'"
            ).fetchone()
        skip_unchanged_files = preference is None or preference[0] != "false"
        job_id = str(uuid.uuid4())
        cancelled = threading.Event()
        with self._lock:
            if self._cancellations:
                raise ValueError("A scan is already running")
            for folder in normalized:
                if folder in self._active_folders:
                    raise ValueError("One or more selected folders are already being scanned")
            with connect() as connection:
                create_job(connection, job_id, normalized, resumed_from_id)
            self._cancellations[job_id] = cancelled
            self._active_folders.update(normalized)
        worker = threading.Thread(
            target=self._run,
            args=(job_id, normalized, cancelled, skip_unchanged_files),
            daemon=True,
            name=f"photoflow-scan-{job_id[:8]}",
        )
        try:
            worker.start()
        except Exception as error:
            try:
                self._finish(job_id, "failed", str(error))
            finally:
                with self._lock:
                    self._cancellations.pop(job_id, None)
                    self._active_folders.difference_update(normalized)
            raise
        return self.status(job_id)

    def status(self, job_id: str) -> dict[str, Any]:
        with connect() as connection:
            job = get_job(connection, job_id)
        if not job:
            raise ValueError("Unknown scan job")
        with self._lock:
            worker_active = job_id in self._cancellations
        if job["status"] == "running" and not worker_active:
            with connect() as connection:
                connection.execute(
                    """UPDATE scan_jobs SET status = 'interrupted', completed_at = CURRENT_TIMESTAMP
                       WHERE id = ? AND status = 'running'""",
                    (job_id,),
                )
                job = get_job(connection, job_id)
        if not job:
            raise ValueError("Unknown scan job")
        return job

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            cancellation = self._cancellations.get(job_id)
        if cancellation:
            cancellation.set()
        with connect() as connection:
            update_job(connection, job_id, cancel_requested=1)
        return self.status(job_id)

    def resume(self, job_id: str) -> dict[str, Any]:
        previous = self.status(job_id)
        if previous["status"] not in {"cancelled", "interrupted", "failed"}:
            raise ValueError("Only stopped scans can be resumed")
        return self.start(previous["folders"], resumed_from_id=job_id)

    def retry_failed(self, job_id: str) -> dict[str, Any]:
        previous = self.status(job_id)
        with connect() as connection:
            has_unresolved_failures = connection.execute(
                "SELECT 1 FROM scan_failures WHERE scan_job_id = ? AND resolved_at IS NULL LIMIT 1",
                (job_id,),
            ).fetchone()
        if previous["status"] != "failed" and not has_unresolved_failures:
            raise ValueError("This scan has no failed items to retry")
        return self.start(previous["folders"], resumed_from_id=job_id)

    def _run(self, job_id: str, folders: list[str], cancelled: threading.Event, skip_unchanged_files: bool) -> None:
        try:
            processed = 0
            for path in self._discover(job_id, folders, cancelled):
                if cancelled.is_set():
                    self._finish(job_id, "cancelled")
                    return
                processed += 1
                self._process_one(job_id, path, skip_unchanged_files)
                with connect() as connection:
                    update_job(
                        connection,
                        job_id,
                        processed_files=processed,
                        current_path=str(path),
                    )
            self._finish(job_id, "cancelled" if cancelled.is_set() else "completed")
        except Exception as error:  # Keep the service alive and retain the job for resume/debugging.
            self._finish(job_id, "failed", str(error))
        finally:
            with self._lock:
                self._cancellations.pop(job_id, None)
                self._active_folders.difference_update(folders)

    def _discover(self, job_id: str, folders: list[str], cancelled: threading.Event) -> Iterator[Path]:
        batch: list[Path] = []
        discovered = 0

        def publish_count() -> None:
            nonlocal discovered
            if not batch:
                return
            discovered += len(batch)
            with connect() as connection:
                update_job(connection, job_id, discovered_files=discovered)

        for folder in folders:
            for root, _, filenames in os.walk(folder, onerror=lambda error: self._record_discovery_error(job_id, error)):
                if cancelled.is_set():
                    publish_count()
                    batch.clear()
                    return
                for filename in filenames:
                    if cancelled.is_set():
                        publish_count()
                        batch.clear()
                        return
                    path = Path(root, filename)
                    if path.is_symlink():
                        continue
                    try:
                        resolved = path.resolve(strict=True)
                        resolved.relative_to(Path(folder).resolve())
                    except (OSError, RuntimeError, ValueError):
                        continue
                    if classify(path):
                        batch.append(path)
                        if len(batch) >= 500:
                            publish_count()
                            yield from batch
                            batch.clear()
        if batch:
            publish_count()
            yield from batch

    @staticmethod
    def _record_discovery_error(job_id: str, error: OSError) -> None:
        with connect() as connection:
            row = connection.execute("SELECT failed_files FROM scan_jobs WHERE id = ?", (job_id,)).fetchone()
            failed = int(row[0]) if row else 0
            update_job(connection, job_id, failed_files=failed + 1, error_summary=str(error)[:500])
            record_scan_failure(connection, job_id, getattr(error, "filename", None) or "", None, str(error))

    def _process_one(self, job_id: str, path: Path, skip_unchanged_files: bool) -> None:
        try:
            if path.is_symlink():
                return
            stat = path.stat()
            with connect() as connection:
                existing = get_media_by_path(connection, str(path))
            provenance = existing.get("metadata_provenance", {}) if existing else {}
            metadata_was_attempted = bool(provenance.get("dimensions") or provenance.get("video"))
            needs_metadata_retry = bool(existing and existing["metadata_status"] == "unavailable" and not metadata_was_attempted)
            if (
                skip_unchanged_files
                and existing
                and existing["size_bytes"] == stat.st_size
                and existing["modified_time_ns"] == stat.st_mtime_ns
                and existing["metadata_status"] != "failed"
                and not needs_metadata_retry
            ):
                self._restore_thumbnail(existing, path)
                with connect() as connection:
                    touch_existing(connection, existing["id"], job_id)
                    resolve_scan_failures(connection, str(path))
                    update_job(connection, job_id, skipped_files=self._counter(connection, job_id, "skipped_files") + 1)
                return
            media_type = classify(path)
            if not media_type:
                return
            record = inspect_media(path, media_type, sha256_file(path))
            with connect() as connection:
                upsert_media(connection, record, job_id)
                resolve_scan_failures(connection, str(path))
                update_job(connection, job_id, indexed_files=self._counter(connection, job_id, "indexed_files") + 1)
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            with connect() as connection:
                update_job(connection, job_id, failed_files=self._counter(connection, job_id, "failed_files") + 1, error_summary=str(error)[:500])
                record_scan_failure(connection, job_id, str(path), classify(path), str(error))
                connection.execute(
                    "UPDATE media_items SET metadata_status = 'failed', metadata_error = ? WHERE source_path = ?",
                    (str(error)[:500], str(path)),
                )
                connection.commit()

    def _restore_thumbnail(self, existing: dict[str, Any], path: Path) -> None:
        """Recreate a missing cache preview. Unchanged source files stay untouched."""
        if is_cached_thumbnail(existing.get("thumbnail_path")):
            return
        content_hash = existing.get("content_hash") or sha256_file(path)
        try:
            rebuilt = ensure_cached_thumbnail(path, str(existing["media_type"]), str(content_hash))
        except (OSError, ValueError, RuntimeError, TimeoutError):
            return
        if not rebuilt:
            return
        with connect() as connection:
            update_thumbnail(connection, int(existing["id"]), rebuilt, str(content_hash))

    @staticmethod
    def _counter(connection: Any, job_id: str, name: str) -> int:
        if name not in {"skipped_files", "indexed_files", "failed_files"}:
            raise ValueError("Unknown scan counter")
        row = connection.execute(f"SELECT {name} FROM scan_jobs WHERE id = ?", (job_id,)).fetchone()
        return int(row[0]) if row else 0

    def _finish(self, job_id: str, status: str, error: str | None = None) -> None:
        with connect() as connection:
            fields: dict[str, Any] = {"status": status}
            if error:
                fields["error_summary"] = error[:500]
            update_job(connection, job_id, **fields)
            connection.execute("UPDATE scan_jobs SET completed_at = CURRENT_TIMESTAMP WHERE id = ?", (job_id,))
            connection.commit()
