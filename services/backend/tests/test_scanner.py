from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from photoflow_backend.app import handle, initialize_database, public_job
from photoflow_backend.database import connect
from photoflow_backend.metadata import inspect_media
from photoflow_backend.migrations import apply_migrations
from photoflow_backend.repository import create_job, get_job, latest_resumable_job, library_overview, update_job
from photoflow_backend.scanner import ScanManager


def fake_record(path: Path, media_type: str, content_hash: str) -> dict[str, object]:
    stat = path.stat()
    return {
        "source_path": str(path), "file_identity": f"{stat.st_dev}:{stat.st_ino}", "content_hash": content_hash,
        "media_type": media_type, "extension": path.suffix.lower(), "filename": path.name,
        "size_bytes": stat.st_size, "modified_time_ns": stat.st_mtime_ns, "metadata_json": "{}",
        "metadata_provenance": '{"dimensions": "Pillow"}', "metadata_status": "partial",
    }


class ScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = os.environ.get("PHOTOFLOW_DATA_DIR")
        os.environ["PHOTOFLOW_DATA_DIR"] = str(Path(self.temp.name, "state"))
        with connect() as connection:
            apply_migrations(connection)

    def tearDown(self) -> None:
        if self.old_data_dir is None:
            os.environ.pop("PHOTOFLOW_DATA_DIR", None)
        else:
            os.environ["PHOTOFLOW_DATA_DIR"] = self.old_data_dir
        self.temp.cleanup()

    def wait_for_job(self, scanner: ScanManager, job_id: str) -> dict[str, object]:
        for _ in range(100):
            job = scanner.status(job_id)
            if job["status"] != "running":
                with scanner._lock:
                    if not scanner._cancellations:
                        return job
            time.sleep(0.02)
        self.fail("scan did not finish")

    def insert_media(self, rows: list[dict[str, object]]) -> None:
        with connect() as connection:
            for row in rows:
                connection.execute(
                    """INSERT INTO media_items
                       (source_path, media_type, extension, filename, size_bytes, modified_time_ns,
                        capture_date, camera_make, camera_model)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                    (
                        str(Path(self.temp.name, str(row["filename"]))),
                        row.get("media_type", "photo"), row["extension"], row["filename"],
                        row.get("size_bytes", 100), row.get("capture_date"),
                        row.get("camera_make"), row.get("camera_model"),
                    ),
                )

    def list_items(self, **filters: object) -> list[dict[str, object]]:
        return handle({"command": "media:list", "media_type": "all", **filters})["items"]

    def test_scan_skips_symbolic_links_to_media_outside_selected_folder(self) -> None:
        root = Path(self.temp.name, "safe-root")
        root.mkdir()
        outside = Path(self.temp.name, "outside.jpg")
        outside.write_bytes(b"outside photo")
        link = root / "linked.jpg"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links are not available in this environment")

        scanner = ScanManager()
        with patch("photoflow_backend.scanner.inspect_media", side_effect=fake_record):
            job = self.wait_for_job(scanner, scanner.start([str(root)])["id"])

        self.assertEqual(job["discovered_files"], 0)
        self.assertTrue(outside.read_bytes() == b"outside photo")

    def test_sql_search_input_is_treated_as_literal_text(self) -> None:
        self.insert_media([{
            "filename": "ordinary.jpg",
            "extension": ".jpg",
            "camera_make": "Literal",
        }])
        count = handle({
            "command": "media:count",
            "media_type": "all",
            "search": "' OR 1=1 --",
        })["count"]
        self.assertEqual(count, 0)

    def test_recurses_mixed_media_and_rescan_does_not_duplicate_rows(self) -> None:
        root = Path(self.temp.name, "media")
        nested = root / "nested" / "deep"
        nested.mkdir(parents=True)
        (root / "one.JPG").write_bytes(b"photo-a")
        (nested / "two.mp4").write_bytes(b"video-b")
        scanner = ScanManager()
        with patch("photoflow_backend.scanner.inspect_media", side_effect=fake_record):
            first = self.wait_for_job(scanner, scanner.start([str(root)])["id"])
            second = self.wait_for_job(scanner, scanner.start([str(root)])["id"])
        self.assertEqual(first["indexed_files"], 2)
        self.assertEqual(second["skipped_files"], 2)
        with connect() as connection:
            self.assertEqual(library_overview(connection), {"photos": 1, "videos": 1, "albums": 0, "total_bytes": 14})

    def test_scan_preference_can_reprocess_unchanged_files_without_duplicate_records(self) -> None:
        root = Path(self.temp.name, "preference")
        root.mkdir()
        (root / "one.jpg").write_bytes(b"photo-data")
        with connect() as connection:
            connection.execute(
                "INSERT INTO app_metadata(key, value) VALUES ('skip_unchanged_files', 'false')"
            )
        scanner = ScanManager()
        with patch("photoflow_backend.scanner.inspect_media", side_effect=fake_record) as inspect:
            first = self.wait_for_job(scanner, scanner.start([str(root)])["id"])
            second = self.wait_for_job(scanner, scanner.start([str(root)])["id"])
        self.assertEqual(first["indexed_files"], 1)
        self.assertEqual(second["indexed_files"], 1)
        self.assertEqual(inspect.call_count, 2)
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 1)

    def test_cancellation_preserves_completed_rows_and_marks_job(self) -> None:
        root = Path(self.temp.name, "cancel")
        root.mkdir()
        for number in range(30):
            (root / f"{number}.jpg").write_bytes(b"content")
        scanner = ScanManager()
        def slow_record(path: Path, media_type: str, content_hash: str) -> dict[str, object]:
            time.sleep(0.02)
            return fake_record(path, media_type, content_hash)
        with patch("photoflow_backend.scanner.inspect_media", side_effect=slow_record):
            scan = scanner.start([str(root)])
            time.sleep(0.06)
            scanner.cancel(scan["id"])
            job = self.wait_for_job(scanner, scan["id"])
        self.assertEqual(job["status"], "cancelled")
        self.assertLess(job["processed_files"], job["discovered_files"])

    def test_inaccessible_directory_is_recorded_without_failing_the_service(self) -> None:
        root = Path(self.temp.name, "restricted")
        root.mkdir()
        scanner = ScanManager()
        def denied(*_args: object, **kwargs: object) -> list[object]:
            onerror = kwargs.get("onerror")
            if callable(onerror):
                onerror(PermissionError("access denied"))
            return []
        with patch("photoflow_backend.scanner.os.walk", side_effect=denied):
            job = self.wait_for_job(scanner, scanner.start([str(root)])["id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["failed_files"], 1)

    def test_running_scan_is_marked_interrupted_and_recoverable_on_startup(self) -> None:
        root = Path(self.temp.name, "recover")
        root.mkdir()
        with connect() as connection:
            create_job(connection, "interrupted-job", [str(root)])

        initialize_database(recover_interrupted_scans=True)

        with connect() as connection:
            job = get_job(connection, "interrupted-job")
            recoverable = latest_resumable_job(connection)
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(recoverable["id"], "interrupted-job")

    def test_worker_start_failure_marks_scan_failed_and_releases_folder(self) -> None:
        root = Path(self.temp.name, "worker-start-failure")
        root.mkdir()
        scanner = ScanManager()

        with patch("photoflow_backend.scanner.threading.Thread.start", side_effect=RuntimeError("worker unavailable")):
            with self.assertRaisesRegex(RuntimeError, "worker unavailable"):
                scanner.start([str(root)])

        with connect() as connection:
            job = connection.execute(
                "SELECT status, error_summary FROM scan_jobs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(job[0], "failed")
        self.assertIn("worker unavailable", job[1])
        self.assertFalse(scanner.is_running)
        restarted = scanner.start([str(root)])
        self.assertEqual(self.wait_for_job(scanner, restarted["id"])["status"], "completed")

    def test_orphaned_running_scan_is_recovered_when_status_is_requested(self) -> None:
        root = Path(self.temp.name, "orphaned-scan")
        root.mkdir()
        with connect() as connection:
            create_job(connection, "orphaned-scan", [str(root)])

        status = ScanManager().status("orphaned-scan")

        self.assertEqual(status["status"], "interrupted")
        with connect() as connection:
            self.assertEqual(latest_resumable_job(connection)["id"], "orphaned-scan")

    def test_scan_error_details_do_not_expose_source_paths_to_the_ui(self) -> None:
        root = Path(self.temp.name, "private-folder")
        root.mkdir()
        with connect() as connection:
            create_job(connection, "failed-job", [str(root)])
            update_job(connection, "failed-job", status="failed", error_summary=f"Could not read {root / 'private.jpg'}")
            job = get_job(connection, "failed-job")

        visible_job = public_job(job)
        self.assertIn("Some files could not be processed", visible_job["error_summary"])
        self.assertNotIn(str(root), str(visible_job))

    def test_scan_history_does_not_expose_source_folders(self) -> None:
        root = Path(self.temp.name, "private-history-folder")
        root.mkdir()
        with connect() as connection:
            create_job(connection, "history-job", [str(root)])
            update_job(connection, "history-job", status="completed", discovered_files=2, indexed_files=2)

        history = handle({"command": "scan:history"})["scans"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["id"], "history-job")
        self.assertEqual(history[0]["indexed_files"], 2)
        self.assertNotIn(str(root), str(history))
        self.assertNotIn("folders", history[0])

    def test_resume_reprocesses_existing_media_without_duplicate_rows(self) -> None:
        root = Path(self.temp.name, "resume")
        root.mkdir()
        (root / "already-indexed.jpg").write_bytes(b"existing-photo")
        scanner = ScanManager()
        with patch("photoflow_backend.scanner.inspect_media", side_effect=fake_record):
            initial = self.wait_for_job(scanner, scanner.start([str(root)])["id"])
            with connect() as connection:
                create_job(connection, "cancelled-job", [str(root)])
                update_job(connection, "cancelled-job", status="cancelled")
            resumed = self.wait_for_job(scanner, scanner.resume("cancelled-job")["id"])

        self.assertEqual(initial["indexed_files"], 1)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["skipped_files"], 1)
        self.assertEqual(resumed["resumed_from_id"], "cancelled-job")
        with connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]
            self.assertIsNone(latest_resumable_job(connection))
        self.assertEqual(count, 1)

    def test_only_one_scan_can_run_at_a_time(self) -> None:
        root = Path(self.temp.name, "single-scan")
        root.mkdir()
        (root / "one.jpg").write_bytes(b"photo")
        (root / "two.jpg").write_bytes(b"photo")
        scanner = ScanManager()
        entered = threading.Event()
        release = threading.Event()
        def slow_record(path: Path, media_type: str, content_hash: str) -> dict[str, object]:
            entered.set()
            release.wait(2)
            return fake_record(path, media_type, content_hash)
        with patch("photoflow_backend.scanner.inspect_media", side_effect=slow_record):
            scan = scanner.start([str(root)])
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError, "already running"):
                scanner.start([str(root)])
            scanner.cancel(scan["id"])
            release.set()
            self.assertEqual(self.wait_for_job(scanner, scan["id"])["status"], "cancelled")

    def test_media_pagination_returns_stable_non_overlapping_pages(self) -> None:
        root = Path(self.temp.name, "pages")
        root.mkdir()
        for name in ("a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"):
            (root / name).write_bytes(name.encode())
        scanner = ScanManager()
        with patch("photoflow_backend.scanner.inspect_media", side_effect=fake_record):
            self.wait_for_job(scanner, scanner.start([str(root)])["id"])

        first = handle({"command": "media:list", "media_type": "photo", "limit": 2, "offset": 0})["items"]
        second = handle({"command": "media:list", "media_type": "photo", "limit": 2, "offset": 2})["items"]
        third = handle({"command": "media:list", "media_type": "photo", "limit": 2, "offset": 4})["items"]
        ids = [item["id"] for page in (first, second, third) for item in page]

        self.assertEqual([item["filename"] for item in first + second + third], ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(third), 1)

    def test_media_pagination_returns_empty_results_and_rejects_invalid_parameters(self) -> None:
        self.assertEqual(handle({"command": "media:list", "media_type": "video", "limit": 2, "offset": 0})["items"], [])
        for limit in (0, 101, 1.5, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                handle({"command": "media:list", "media_type": "photo", "limit": limit, "offset": 0})
        for offset in (-1, 1.5, True, 9_007_199_254_740_992):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                handle({"command": "media:list", "media_type": "photo", "limit": 10, "offset": offset})

    def test_search_matches_filenames_case_insensitively(self) -> None:
        self.insert_media([
            {"filename": "Summer-Trip.JPG", "extension": ".jpg"},
            {"filename": "winter.png", "extension": ".png"},
        ])

        self.assertEqual([item["filename"] for item in self.list_items(search="SUMMER")], ["Summer-Trip.JPG"])

    def test_search_matches_extensions_and_camera_make_or_model(self) -> None:
        self.insert_media([
            {"filename": "one.jpg", "extension": ".jpg", "camera_make": "Canon", "camera_model": "EOS R5"},
            {"filename": "clip.mp4", "extension": ".mp4", "media_type": "video"},
        ])

        self.assertEqual([item["filename"] for item in self.list_items(search="MP4")], ["clip.mp4"])
        self.assertEqual([item["filename"] for item in self.list_items(search="canon")], ["one.jpg"])
        self.assertEqual([item["filename"] for item in self.list_items(camera_make="CAN")], ["one.jpg"])
        self.assertEqual([item["filename"] for item in self.list_items(camera_model="r5")], ["one.jpg"])

    def test_extension_and_capture_date_filters(self) -> None:
        self.insert_media([
            {"filename": "early.jpg", "extension": ".jpg", "capture_date": "2023:01:02 12:00:00"},
            {"filename": "middle.jpg", "extension": ".jpg", "capture_date": "2024-06-15T10:00:00"},
            {"filename": "late.png", "extension": ".png", "capture_date": "2025:11:20 10:00:00"},
        ])

        filtered = self.list_items(extension=".jpg", date_from="2024-01-01", date_to="2024-12-31")
        self.assertEqual([item["filename"] for item in filtered], ["middle.jpg"])

    def test_sorting_supports_dates_names_and_file_sizes(self) -> None:
        self.insert_media([
            {"filename": "zulu.jpg", "extension": ".jpg", "capture_date": "2024:01:02 12:00:00", "size_bytes": 100},
            {"filename": "alpha.jpg", "extension": ".jpg", "capture_date": "2025:01:02 12:00:00", "size_bytes": 300},
            {"filename": "bravo.jpg", "extension": ".jpg", "capture_date": "2023:01:02 12:00:00", "size_bytes": 200},
        ])

        self.assertEqual([item["filename"] for item in self.list_items(sort="newest")], ["alpha.jpg", "zulu.jpg", "bravo.jpg"])
        self.assertEqual([item["filename"] for item in self.list_items(sort="oldest")], ["bravo.jpg", "zulu.jpg", "alpha.jpg"])
        self.assertEqual([item["filename"] for item in self.list_items(sort="filename-asc")], ["alpha.jpg", "bravo.jpg", "zulu.jpg"])
        self.assertEqual([item["filename"] for item in self.list_items(sort="filename-desc")], ["zulu.jpg", "bravo.jpg", "alpha.jpg"])
        self.assertEqual([item["filename"] for item in self.list_items(sort="size-desc")], ["alpha.jpg", "bravo.jpg", "zulu.jpg"])
        self.assertEqual([item["filename"] for item in self.list_items(sort="size-asc")], ["zulu.jpg", "bravo.jpg", "alpha.jpg"])

    def test_search_filters_sort_and_pagination_are_combined(self) -> None:
        self.insert_media([
            {"filename": "Trip-a.jpg", "extension": ".jpg", "capture_date": "2024:01:01 10:00:00", "camera_make": "Canon"},
            {"filename": "Trip-b.jpg", "extension": ".jpg", "capture_date": "2024:02:01 10:00:00", "camera_make": "Canon"},
            {"filename": "Trip-c.png", "extension": ".png", "capture_date": "2024:03:01 10:00:00", "camera_make": "Canon"},
            {"filename": "Trip-other.jpg", "extension": ".jpg", "capture_date": "2024:04:01 10:00:00", "camera_make": "Nikon"},
        ])

        first = self.list_items(search="trip", extension=".jpg", camera_make="canon", sort="oldest", limit=1, offset=0)
        second = self.list_items(search="trip", extension=".jpg", camera_make="canon", sort="oldest", limit=1, offset=1)
        self.assertEqual([item["filename"] for item in first + second], ["Trip-a.jpg", "Trip-b.jpg"])

    def test_media_query_rejects_invalid_filter_values_and_returns_no_match(self) -> None:
        self.insert_media([{"filename": "known.jpg", "extension": ".jpg"}])
        self.assertEqual(self.list_items(search="not-found"), [])
        invalid_queries = [
            {"extension": ".exe"},
            {"date_from": "2024-02-30"},
            {"date_to": 20240101},
            {"date_from": "2025-01-01", "date_to": "2024-01-01"},
            {"camera_make": "x" * 101},
            {"search": "x" * 201},
            {"sort": "random"},
        ]
        for filters in invalid_queries:
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.list_items(**filters)

    def test_pillow_thumbnail_generation_when_dependency_is_available(self) -> None:
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is optional until services/backend requirements are installed")
        # A valid small PNG. The thumbnail target is PhotoFlow-owned test state, never this source file.
        source = Path(self.temp.name, "image.png")
        from PIL import Image
        Image.new("RGB", (1, 1), (64, 128, 192)).save(source, format="PNG")
        record = inspect_media(source, "photo", "a" * 64)
        self.assertEqual((record["width"], record["height"]), (1, 1))
        self.assertTrue(Path(str(record["thumbnail_path"])).is_file())

    def test_metadata_fields_and_status_are_persisted_and_returned(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is required for metadata extraction tests")
        root = Path(self.temp.name, "metadata")
        root.mkdir()
        source = root / "camera.jpg"
        exif = Image.Exif()
        exif[306] = "2024:05:03 12:45:00"
        exif[271] = "Canon"
        exif[272] = "EOS Test"
        Image.new("RGB", (40, 30), (32, 64, 96)).save(source, exif=exif)
        scanner = ScanManager()
        job = self.wait_for_job(scanner, scanner.start([str(root)])["id"])

        with connect() as connection:
            row = connection.execute(
                """SELECT width, height, capture_date, camera_make, camera_model, metadata_status
                   FROM media_items WHERE filename = ?""",
                (source.name,),
            ).fetchone()
        public = handle({"command": "media:list", "media_type": "photo"})["items"][0]
        self.assertEqual(job["indexed_files"], 1)
        self.assertEqual(tuple(row), (40, 30, "2024:05:03 12:45:00", "Canon", "EOS Test", "extracted"))
        self.assertEqual((public["width"], public["height"]), (40, 30))
        self.assertEqual(public["capture_date"], "2024:05:03 12:45:00")
        self.assertEqual((public["camera_make"], public["camera_model"]), ("Canon", "EOS Test"))
        self.assertEqual(public["metadata_status"], "extracted")
        self.assertNotIn("source_path", public)
        self.assertNotIn("gps_latitude", public)

    def test_unchanged_legacy_record_without_metadata_is_reprocessed(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is required for metadata extraction tests")
        root = Path(self.temp.name, "legacy-metadata")
        root.mkdir()
        source = root / "legacy.png"
        Image.new("RGB", (27, 19), (120, 80, 40)).save(source)
        stat = source.stat()
        with connect() as connection:
            connection.execute(
                """INSERT INTO media_items
                   (source_path, media_type, extension, filename, size_bytes, modified_time_ns)
                   VALUES (?, 'photo', '.png', ?, ?, ?)""",
                (str(source), source.name, stat.st_size, stat.st_mtime_ns),
            )

        scanner = ScanManager()
        job = self.wait_for_job(scanner, scanner.start([str(root)])["id"])
        with connect() as connection:
            row = connection.execute(
                "SELECT width, height, metadata_status FROM media_items WHERE source_path = ?",
                (str(source),),
            ).fetchone()
        self.assertEqual(job["indexed_files"], 1)
        self.assertEqual(tuple(row), (27, 19, "partial"))

    def test_failed_items_are_listed_without_paths_and_resolved_after_safe_retry(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is required for image scan tests")
        root = Path(self.temp.name, "failed-items")
        root.mkdir()
        source = root / "broken.jpg"
        source.write_bytes(b"not an image")
        scanner = ScanManager()
        first = self.wait_for_job(scanner, scanner.start([str(root)])["id"])

        failures = handle({"command": "retry:failed-list"})["scan_failures"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["filename"], "broken.jpg")
        self.assertNotIn(str(root), str(failures))
        self.assertIn("corrupt", failures[0]["error_summary"].lower())

        Image.new("RGB", (12, 9), (200, 150, 100)).save(source, format="JPEG")
        retry = self.wait_for_job(scanner, scanner.retry_failed(first["id"])["id"])
        self.assertEqual(retry["indexed_files"], 1)
        self.assertEqual(handle({"command": "retry:failed-list"})["scan_failures"], [])

    def test_full_resolution_view_is_oriented_and_does_not_expose_exif(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is required for full-resolution viewing tests")
        import base64
        root = Path(self.temp.name, "full-resolution")
        root.mkdir()
        source = root / "private.jpg"
        exif = Image.Exif()
        exif[271] = "Camera"
        exif[272] = "Private model"
        Image.new("RGB", (51, 37), (100, 120, 140)).save(source, exif=exif)
        original = source.read_bytes()
        scanner = ScanManager()
        self.wait_for_job(scanner, scanner.start([str(root)])["id"])
        media_id = handle({"command": "media:list", "media_type": "photo"})["items"][0]["id"]

        result = handle({"command": "media:full-resolution", "media_id": media_id})
        image_bytes = base64.b64decode(result["data_url"].split(",", 1)[1])
        from PIL import Image
        import io
        with Image.open(io.BytesIO(image_bytes)) as rendered:
            self.assertEqual(rendered.size, (51, 37))
            self.assertEqual(dict(rendered.getexif()), {})
        self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
