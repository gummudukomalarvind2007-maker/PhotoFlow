from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from photoflow_backend.albums import add_album_media, create_album
from photoflow_backend.database import connect, database_path, thumbnails_dir
from photoflow_backend.migrations import apply_migrations
from photoflow_backend.settings import set_skip_unchanged_files, settings_overview
from photoflow_backend.transfers import (
    TransferManager,
    _commit_without_overwrite,
    _copy_file,
    _sqlite_backup,
    _validate_backup,
    cleanup_abandoned_temporary_files,
)


class TransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = os.environ.get("PHOTOFLOW_DATA_DIR")
        self.state = Path(self.temp.name, "state")
        os.environ["PHOTOFLOW_DATA_DIR"] = str(self.state)
        self.source = Path(self.temp.name, "source")
        self.source.mkdir()
        self.photo_path = self.source / "image.jpg"
        self.video_path = self.source / "movie.mp4"
        self.photo_path.write_bytes(b"photo bytes")
        self.video_path.write_bytes(b"video bytes")
        with connect() as connection:
            apply_migrations(connection)
            self.photo_id = self.insert_media(connection, self.photo_path, "photo", ".jpg")
            self.video_id = self.insert_media(connection, self.video_path, "video", ".mp4")
        self.manager = TransferManager()
        self.output = Path(self.temp.name, "output")
        self.output.mkdir()

    def tearDown(self) -> None:
        if self.old_data_dir is None:
            os.environ.pop("PHOTOFLOW_DATA_DIR", None)
        else:
            os.environ["PHOTOFLOW_DATA_DIR"] = self.old_data_dir
        self.temp.cleanup()

    def insert_media(self, connection, path: Path, media_type: str, extension: str) -> int:
        cursor = connection.execute(
            """INSERT INTO media_items
               (source_path, media_type, extension, filename, size_bytes, modified_time_ns,
                width, height, duration_seconds, frame_rate, capture_date, camera_make, camera_model,
                gps_latitude, gps_longitude)
               VALUES (?, ?, ?, ?, ?, 1, 120, 80, 4.5, 24.0, '2024-01-02', 'Test make', 'Test model', 1.2, 3.4)""",
            (str(path), media_type, extension, path.name, path.stat().st_size),
        )
        return int(cursor.lastrowid)

    def wait(self, job: dict[str, object]) -> dict[str, object]:
        for _ in range(500):
            current = self.manager.status(str(job["id"]))
            if current["status"] != "running":
                return current
            time.sleep(0.01)
        self.fail("transfer did not finish")

    def test_selected_photo_and_video_export_are_byte_preserving_and_organized(self) -> None:
        job = self.manager.start_media_export(
            "selected", str(self.output), media_ids=[self.photo_id, self.video_id], organize_by_type=True
        )
        result = self.wait(job)
        self.assertEqual((result["status"], result["succeeded"], result["failed"]), ("completed", 2, 0))
        self.assertEqual((self.output / "Photos" / "image.jpg").read_bytes(), self.photo_path.read_bytes())
        self.assertEqual((self.output / "Videos" / "movie.mp4").read_bytes(), self.video_path.read_bytes())
        self.assertEqual(self.photo_path.read_bytes(), b"photo bytes")
        self.assertEqual(self.video_path.read_bytes(), b"video bytes")

    def test_album_and_filtered_search_exports(self) -> None:
        with connect() as connection:
            album = create_album(connection, "Trip")
            add_album_media(connection, album["id"], [self.video_id])
        album_job = self.manager.start_media_export("album", str(self.output), album_id=album["id"])
        album_result = self.wait(album_job)
        self.assertEqual(album_result["succeeded"], 1)
        self.assertEqual((self.output / "movie.mp4").read_bytes(), b"video bytes")

        filtered_job = self.manager.start_media_export(
            "filtered", str(self.output), media_type="photo", filters={"search": "image"}
        )
        filtered_result = self.wait(filtered_job)
        self.assertEqual((filtered_result["total"], filtered_result["succeeded"]), (1, 1))
        self.assertEqual((self.output / "image.jpg").read_bytes(), b"photo bytes")

    def test_collision_uses_a_new_name_without_overwriting(self) -> None:
        existing = self.output / "image.jpg"
        existing.write_bytes(b"keep existing")
        result = self.wait(self.manager.start_media_export(
            "selected", str(self.output), media_ids=[self.photo_id]
        ))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(existing.read_bytes(), b"keep existing")
        self.assertEqual((self.output / "image (1).jpg").read_bytes(), b"photo bytes")

    def test_missing_source_is_reported_without_exposing_its_path(self) -> None:
        self.photo_path.unlink()
        result = self.wait(self.manager.start_media_export(
            "selected", str(self.output), media_ids=[self.photo_id]
        ))
        self.assertEqual((result["status"], result["failed"]), ("completed", 1))
        self.assertNotIn(str(self.photo_path), str(result))
        self.assertIn("could not be exported", result["error_summary"])

    def test_destination_permission_error_is_reported_without_exposing_paths(self) -> None:
        original_open = os.open

        def deny_export_destination(path, *args, **kwargs):
            if Path(path).parent == self.output:
                raise PermissionError("denied")
            return original_open(path, *args, **kwargs)

        with patch("photoflow_backend.transfers.os.open", side_effect=deny_export_destination):
            job = self.manager.start_media_export("selected", str(self.output), media_ids=[self.photo_id])
            result = self.wait(job)
        self.assertEqual((result["status"], result["failed"]), ("completed", 1))
        self.assertNotIn(str(self.photo_path), str(result))
        self.assertNotIn(str(self.output), str(result))
        self.assertIn("destination access", result["error_summary"])

    def test_media_export_can_be_cancelled_and_removes_partial_output(self) -> None:
        started = threading.Event()

        def wait_until_cancelled(source: Path, destination: Path, filename: str, cancelled: threading.Event) -> None:
            started.set()
            cancelled.wait(2)
            _copy_file(source, destination, filename, cancelled)

        with patch("photoflow_backend.transfers._copy_file", side_effect=wait_until_cancelled):
            job = self.manager.start_media_export("selected", str(self.output), media_ids=[self.photo_id])
            self.assertTrue(started.wait(2))
            self.manager.cancel(job["id"])
            result = self.wait(job)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(list(self.output.iterdir()), [])

    def test_interrupted_media_copy_never_publishes_partial_file(self) -> None:
        original_bytes = self.photo_path.read_bytes()
        marker = Path(self.temp.name, "copy-paused")
        worker_code = """
import sys
import threading
from pathlib import Path
from unittest.mock import patch
from photoflow_backend.transfers import _copy_file

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
marker = Path(sys.argv[3])
original_open = Path.open

class PausingReader:
    def __init__(self):
        self.reads = 0
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False
    def read(self, _size):
        if self.reads == 0:
            self.reads += 1
            return b'partial export bytes' * 100000
        marker.write_text('ready', encoding='utf-8')
        threading.Event().wait(60)

def paused_open(path, *args, **kwargs):
    if path == source:
        return PausingReader()
    return original_open(path, *args, **kwargs)

with patch.object(Path, 'open', new=paused_open):
    _copy_file(source, destination, source.name, threading.Event())
"""
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[1])
        process = subprocess.Popen(
            [sys.executable, "-c", worker_code, str(self.photo_path), str(self.output), str(marker)],
            env=environment,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not marker.exists() and process.poll() is None:
                time.sleep(0.01)
            self.assertTrue(marker.exists(), "copy worker did not reach its partial-write point")
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)

        self.assertFalse((self.output / self.photo_path.name).exists())
        self.assertEqual(self.photo_path.read_bytes(), original_bytes)
        temporary_files = list(self.output.glob(".photoflow-transfer-*.tmp"))
        self.assertEqual(len(temporary_files), 1)
        self.assertGreater(temporary_files[0].stat().st_size, 0)

    def test_csv_and_json_metadata_exports_omit_paths_and_gps(self) -> None:
        csv_path = Path(self.temp.name, "library.csv")
        csv_result = self.wait(self.manager.start_metadata_export(str(csv_path), "csv"))
        with csv_path.open(encoding="utf-8", newline="") as stream:
            csv_rows = list(csv.DictReader(stream))
        self.assertEqual(csv_result["status"], "completed")
        self.assertEqual({row["filename"] for row in csv_rows}, {"image.jpg", "movie.mp4"})
        self.assertIn("duration_seconds", csv_rows[0])
        self.assertNotIn("source_path", csv_rows[0])
        self.assertNotIn("gps_latitude", csv_rows[0])

        json_path = Path(self.temp.name, "library.json")
        json_result = self.wait(self.manager.start_metadata_export(str(json_path), "json"))
        json_rows = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(json_result["status"], "completed")
        self.assertEqual(len(json_rows), 2)
        self.assertNotIn("gps_longitude", json_rows[0])
        self.assertNotIn("source_path", json_rows[0])

    def test_failed_metadata_export_does_not_leave_partial_destination(self) -> None:
        output = Path(self.temp.name, "incomplete.csv")
        with patch("photoflow_backend.transfers.list_media", side_effect=RuntimeError("database read failed")):
            result = self.wait(self.manager.start_metadata_export(str(output), "csv"))

        self.assertEqual(result["status"], "failed")
        self.assertFalse(output.exists())
        self.assertEqual(list(Path(self.temp.name).glob(".photoflow-transfer-*.tmp")), [])

    def test_csv_formula_payload_is_neutralized(self) -> None:
        with connect() as connection:
            connection.execute(
                "UPDATE media_items SET camera_make = ? WHERE id = ?",
                ("=HYPERLINK(\"https://example.invalid\")", self.photo_id),
            )
        csv_path = Path(self.temp.name, "formula.csv")
        result = self.wait(self.manager.start_metadata_export(str(csv_path), "csv"))
        self.assertEqual(result["status"], "completed")
        with csv_path.open(encoding="utf-8", newline="") as stream:
            row = next(row for row in csv.DictReader(stream) if row["filename"] == "image.jpg")
        self.assertTrue(row["camera_make"].startswith("'"))

    def test_abandoned_restore_temporary_file_cleanup_is_application_owned(self) -> None:
        abandoned = self.state / ".photoflow-restore-abandoned.tmp"
        abandoned.write_bytes(b"temporary restore data")
        abandoned_export = self.state / ".photoflow-transfer-abandoned.tmp"
        abandoned_export.write_bytes(b"temporary export data")
        backups = self.state / "backups"
        backups.mkdir()
        abandoned_safety_copy = backups / ".photoflow-transfer-abandoned.tmp"
        abandoned_safety_copy.write_bytes(b"temporary backup data")
        outside = Path(self.temp.name, ".photoflow-restore-outside.tmp")
        outside.write_bytes(b"keep")

        removed = cleanup_abandoned_temporary_files()

        self.assertEqual(removed, 3)
        self.assertFalse(abandoned.exists())
        self.assertFalse(abandoned_export.exists())
        self.assertFalse(abandoned_safety_copy.exists())
        self.assertTrue(outside.exists())

    def test_temporary_cleanup_does_not_follow_backups_symlink(self) -> None:
        backups = self.state / "backups"
        outside = Path(self.temp.name, "external-backups")
        outside.mkdir()
        keep = outside / ".photoflow-transfer-not-owned.tmp"
        keep.write_bytes(b"keep")
        try:
            os.symlink(outside, backups, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are not available in this environment")

        cleanup_abandoned_temporary_files()

        self.assertEqual(keep.read_bytes(), b"keep")

    def test_application_data_temporary_cleanup_runs_only_during_startup_recovery(self) -> None:
        from photoflow_backend.app import initialize_database

        active = self.state / ".photoflow-transfer-in-progress.tmp"
        active.write_bytes(b"active operation")

        initialize_database()
        self.assertTrue(active.exists())

        initialize_database(recover_interrupted_scans=True)
        self.assertFalse(active.exists())

    def test_worker_start_failure_marks_transfer_failed_and_releases_maintenance(self) -> None:
        with patch("photoflow_backend.transfers.threading.Thread.start", side_effect=RuntimeError("worker unavailable")):
            with self.assertRaisesRegex(RuntimeError, "worker unavailable"):
                self.manager.start_backup(str(Path(self.temp.name, "worker-failure.db")))

        job = next(iter(self.manager._jobs.values()))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_summary"], "Could not start this operation. Try again.")
        self.assertIsNone(self.manager._active_id)
        self.assertFalse(self.manager.database_maintenance_active)

    def test_running_transfer_progress_is_recovered_as_interrupted(self) -> None:
        job_id, _cancellation = self.manager._create_job("media-export", 5)
        self.manager._last_persisted_at = 0.0
        self.manager._update(job_id, completed=3, succeeded=3)

        recovered = TransferManager().status(job_id)

        self.assertEqual(recovered["status"], "interrupted")
        self.assertEqual((recovered["completed"], recovered["succeeded"], recovered["total"]), (3, 3, 5))
        self.assertIn("not resumed automatically", recovered["error_summary"])
        state = (self.state / "transfer-jobs.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.photo_path), state)
        self.assertNotIn(str(self.output), state)

    def test_transfer_recovery_write_failure_is_reported_without_startup_failure(self) -> None:
        with patch("photoflow_backend.transfers.os.replace", side_effect=PermissionError("denied")):
            job_id, _cancellation = self.manager._create_job("media-export", 1)

        self.assertIn("Recovery status could not be saved", self.manager.status(job_id)["error_summary"])

    def test_backup_is_sqlite_consistent_and_restore_keeps_safety_copy(self) -> None:
        backup = Path(self.temp.name, "library.db")
        result = self.wait(self.manager.start_backup(str(backup)))
        self.assertEqual(result["status"], "completed")
        _validate_backup(backup)

        with connect() as connection:
            connection.execute("DELETE FROM media_items WHERE id = ?", (self.photo_id,))
        restore_result = self.wait(self.manager.start_restore(str(backup)))
        self.assertEqual(restore_result["status"], "completed")
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 2)
        safety_backups = list((self.state / "backups").glob("pre-restore-*.db"))
        self.assertEqual(len(safety_backups), 1)
        _validate_backup(safety_backups[0])

    def test_failed_backup_does_not_publish_partial_destination(self) -> None:
        output = Path(self.temp.name, "partial.db")

        def fail_after_partial_write(_source, target, _cancellation, _progress=None):
            target.write_bytes(b"interrupted backup")
            raise OSError("backup interrupted")

        with patch("photoflow_backend.transfers._sqlite_backup", side_effect=fail_after_partial_write):
            result = self.wait(self.manager.start_backup(str(output)))

        self.assertEqual(result["status"], "failed")
        self.assertFalse(output.exists())
        self.assertEqual(list(Path(self.temp.name).glob(".photoflow-transfer-*.tmp")), [])
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 2)

    def test_failed_restore_keeps_live_database_and_verified_safety_copy(self) -> None:
        from photoflow_backend.database import database_path

        backup = Path(self.temp.name, "valid-before-failure.db")
        self.assertEqual(self.wait(self.manager.start_backup(str(backup)))["status"], "completed")
        with connect() as connection:
            connection.execute("DELETE FROM media_items WHERE id = ?", (self.photo_id,))

        def fail_after_safety_copy(source_path, target_path, cancellation, progress=None):
            if source_path == database_path():
                return _sqlite_backup(source_path, target_path, cancellation, progress)
            target_path.write_bytes(b"interrupted restore")
            raise OSError("restore interrupted")

        with patch("photoflow_backend.transfers._sqlite_backup", side_effect=fail_after_safety_copy):
            restored = self.wait(self.manager.start_restore(str(backup)))

        self.assertEqual(restored["status"], "failed")
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 1)
        safety_backups = list((self.state / "backups").glob("pre-restore-*.db"))
        self.assertEqual(len(safety_backups), 1)
        _validate_backup(safety_backups[0])
        self.assertEqual(list(self.state.glob(".photoflow-restore-*.tmp")), [])
        self.assertEqual(list((self.state / "backups").glob(".photoflow-transfer-*.tmp")), [])

    def test_restore_safety_backup_collision_preserves_existing_file(self) -> None:
        backup = Path(self.temp.name, "valid-before-collision.db")
        self.assertEqual(self.wait(self.manager.start_backup(str(backup)))["status"], "completed")
        safety_bytes = b"pre-existing backup data"
        original_commit = _commit_without_overwrite

        def collide(temporary: Path, destination: Path) -> None:
            if destination.name.startswith("pre-restore-"):
                destination.write_bytes(safety_bytes)
                raise FileExistsError("destination already exists")
            original_commit(temporary, destination)

        with patch("photoflow_backend.transfers._commit_without_overwrite", side_effect=collide):
            restored = self.wait(self.manager.start_restore(str(backup)))

        self.assertEqual(restored["status"], "failed")
        safety_files = list((self.state / "backups").glob("pre-restore-*.db"))
        self.assertEqual(len(safety_files), 1)
        self.assertEqual(safety_files[0].read_bytes(), safety_bytes)
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 2)

    def test_restore_refuses_symlinked_application_safety_folder(self) -> None:
        backup = Path(self.temp.name, "valid-before-symlink.db")
        self.assertEqual(self.wait(self.manager.start_backup(str(backup)))["status"], "completed")
        outside = Path(self.temp.name, "external-backups")
        outside.mkdir()
        try:
            os.symlink(outside, self.state / "backups", target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are not available in this environment")

        restored = self.wait(self.manager.start_restore(str(backup)))

        self.assertEqual(restored["status"], "failed")
        self.assertEqual(list(outside.iterdir()), [])
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 2)

    def test_invalid_backup_does_not_change_live_database(self) -> None:
        invalid = Path(self.temp.name, "not-a-backup.db")
        invalid.write_text("not sqlite", encoding="utf-8")
        result = self.wait(self.manager.start_restore(str(invalid)))
        self.assertEqual(result["status"], "failed")
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 2)

    def test_destination_validation_and_restore_activity_guard(self) -> None:
        with self.assertRaisesRegex(ValueError, "destination"):
            self.manager.start_media_export("selected", str(Path(self.temp.name, "missing")), media_ids=[self.photo_id])
        with self.assertRaisesRegex(ValueError, "CSV or JSON"):
            self.manager.start_metadata_export(str(Path(self.temp.name, "bad.xml")), "xml")
        with self.assertRaisesRegex(ValueError, "Stop scanning"):
            self.manager.start_restore(str(self.photo_path), external_work_running=True)
        existing_backup = Path(self.temp.name, "existing.db")
        existing_backup.write_bytes(b"already here")
        with self.assertRaisesRegex(ValueError, "different filename"):
            self.manager.start_backup(str(existing_backup))

    def test_cache_clear_removes_only_cache_and_settings_report_tools_and_storage(self) -> None:
        cache = thumbnails_dir()
        (cache / "preview.jpg").write_bytes(b"preview")
        result = self.wait(self.manager.start_cache_clear())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(list(cache.iterdir()), [])
        self.assertTrue(self.photo_path.is_file())
        settings = settings_overview()
        self.assertEqual(settings["photo_count"], 1)
        self.assertEqual(settings["video_count"], 1)
        self.assertEqual(settings["thumbnail_cache_files"], 0)
        self.assertIn("ffmpeg_available", settings)
        self.assertIn("database_path", settings)
        self.assertFalse(set_skip_unchanged_files(False))
        self.assertFalse(settings_overview()["skip_unchanged_files"])


if __name__ == "__main__":
    unittest.main()
