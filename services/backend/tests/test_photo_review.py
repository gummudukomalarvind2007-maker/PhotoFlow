from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFilter = None  # type: ignore[assignment]

from photoflow_backend.database import connect
from photoflow_backend.app import handle
from photoflow_backend.migrations import apply_migrations
from photoflow_backend.photo_review import (
    PhotoReviewManager,
    analyze_photo,
    list_review,
    set_review_status,
)


@unittest.skipIf(Image is None, "Pillow is required for photo-review tests")
class PhotoReviewTests(unittest.TestCase):
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

    def image(self, name: str, mode: str = "pattern", extension: str = ".png") -> Path:
        path = Path(self.temp.name, name + extension)
        if mode == "pattern":
            image = Image.new("RGB", (800, 600), "white")
            draw = ImageDraw.Draw(image)
            for x in range(0, 800, 24):
                draw.rectangle((x, 0, x + 12, 600), fill=(x % 255, (x * 3) % 255, (x * 7) % 255))
            draw.ellipse((230, 120, 570, 460), fill=(30, 170, 80))
        else:
            image = Image.new("RGB", (800, 600), mode)
        image.save(path, format="JPEG" if extension in {".jpg", ".jpeg"} else "PNG", quality=90)
        return path

    def index_photo(self, path: Path) -> int:
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            with Image.open(path) as image:
                width, height = image.size
        except OSError:
            width = height = None
        with connect() as connection:
            cursor = connection.execute(
                """INSERT INTO media_items
                   (source_path, content_hash, media_type, extension, filename, size_bytes, modified_time_ns, width, height)
                   VALUES (?, ?, 'photo', ?, ?, ?, ?, ?, ?)""",
                (str(path), digest, path.suffix.lower(), path.name, stat.st_size, stat.st_mtime_ns, width, height),
            )
            return int(cursor.lastrowid)

    def finish_job(self, manager: PhotoReviewManager, job_id: str) -> dict[str, object]:
        for _ in range(500):
            job = manager.status(job_id)
            if job and job["status"] != "running":
                return job
            time.sleep(0.01)
        self.fail("photo analysis job did not finish")

    def test_exact_duplicates_use_cached_sha256_and_select_representative(self) -> None:
        first_path = self.image("first")
        second_path = Path(self.temp.name, "second.png")
        second_path.write_bytes(first_path.read_bytes())
        first_id = self.index_photo(first_path)
        self.index_photo(second_path)

        with patch("photoflow_backend.photo_review.sha256_file", side_effect=AssertionError("cached hash should be reused")):
            manager = PhotoReviewManager()
            job = manager.start()
            self.assertEqual(self.finish_job(manager, job["id"])["status"], "completed")

        groups = list_review("exact", 20, 0)["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["item_count"], 2)
        self.assertEqual(groups[0]["representative_id"], first_id)
        self.assertEqual(sum(item["is_representative"] for item in groups[0]["items"]), 1)
        self.assertNotIn("source_path", groups[0]["items"][0])
        self.assertIn("location", groups[0]["items"][0])

    def test_similar_images_are_grouped_across_png_and_jpeg(self) -> None:
        png_path = self.image("scene-png")
        jpeg_path = self.image("scene-jpeg", extension=".jpg")
        first_id = self.index_photo(png_path)
        second_id = self.index_photo(jpeg_path)

        manager = PhotoReviewManager()
        job = manager.start(similarity_threshold=7)
        self.assertEqual(self.finish_job(manager, job["id"])["analyzed_files"], 2)

        pairs = list_review("similar", 20, 0, similarity_threshold=7)["groups"]
        self.assertTrue(any({item["id"] for item in group["items"]} == {first_id, second_id} for group in pairs))
        self.assertNotEqual(
            hashlib.sha256(png_path.read_bytes()).hexdigest(),
            hashlib.sha256(jpeg_path.read_bytes()).hexdigest(),
        )

    def test_blur_low_resolution_and_exposure_metrics_are_reported(self) -> None:
        sharp = Image.new("L", (800, 600), 0)
        draw = ImageDraw.Draw(sharp)
        for x in range(0, 800, 8):
            draw.rectangle((x, 0, x + 3, 600), fill=255)
        sharp_path = Path(self.temp.name, "sharp.png")
        sharp.save(sharp_path)
        blurred_path = Path(self.temp.name, "blurred.png")
        sharp.filter(ImageFilter.GaussianBlur(radius=12)).save(blurred_path)
        low_path = Path(self.temp.name, "low.png")
        Image.new("RGB", (320, 240), (120, 120, 120)).save(low_path)
        dark_path = Path(self.temp.name, "dark.png")
        Image.new("RGB", (640, 480), (10, 10, 10)).save(dark_path)
        bright_path = Path(self.temp.name, "bright.png")
        Image.new("RGB", (640, 480), (250, 250, 250)).save(bright_path)

        self.assertNotIn("blurry", analyze_photo(sharp_path)["issues"])
        self.assertIn("blurry", analyze_photo(blurred_path)["issues"])
        self.assertIn("low_resolution", analyze_photo(low_path)["issues"])
        self.assertIn("dark", analyze_photo(dark_path)["issues"])
        self.assertIn("overexposed", analyze_photo(bright_path)["issues"])

    def test_corrupted_and_unsupported_images_are_handled_without_crashing_job(self) -> None:
        bad_path = Path(self.temp.name, "broken.jpg")
        bad_path.write_bytes(b"not an image")
        self.index_photo(bad_path)

        manager = PhotoReviewManager()
        job = manager.start()
        final = self.finish_job(manager, job["id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["failed_files"], 1)
        with connect() as connection:
            stored_error = connection.execute("SELECT analysis_error FROM photo_analysis").fetchone()[0]
        self.assertEqual(stored_error, "This image is corrupted or its format is unsupported.")

    def test_failed_only_retry_does_not_reprocess_successful_analysis(self) -> None:
        good = self.image("already-analyzed")
        broken = Path(self.temp.name, "retry-broken.jpg")
        Image.new("RGB", (800, 600), (90, 110, 130)).save(broken, format="JPEG")
        good_id = self.index_photo(good)
        failed_id = self.index_photo(broken)
        manager = PhotoReviewManager()
        original_analyze = analyze_photo
        def fail_only_broken(path: Path) -> dict[str, object]:
            if path == broken:
                raise ValueError("This image is corrupted or its format is unsupported.")
            return original_analyze(path)
        with patch("photoflow_backend.photo_review.analyze_photo", side_effect=fail_only_broken):
            initial = manager.start()
            self.finish_job(manager, initial["id"])

        retry = manager.start(retry_failed_only=True)
        result = self.finish_job(manager, retry["id"])
        self.assertEqual(result["total_files"], 1)
        self.assertEqual(result["analyzed_files"], 1)
        self.assertEqual(result["failed_files"], 0)
        with connect() as connection:
            errors = connection.execute(
                "SELECT media_id FROM photo_analysis WHERE analysis_error IS NOT NULL"
            ).fetchall()
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM photo_analysis WHERE media_id = ? AND analysis_error IS NULL",
                (good_id,),
            ).fetchone()[0], 1)
        self.assertEqual(errors, [])
        self.assertNotEqual(good_id, failed_id)

    def test_interrupted_analysis_job_is_listed_and_resumes_only_pending_images(self) -> None:
        image = self.image("interrupted-pending")
        self.index_photo(image)
        with connect() as connection:
            connection.execute(
                """INSERT INTO photo_analysis_jobs(id, status, similarity_threshold, total_files)
                   VALUES ('interrupted-job', 'interrupted', 6, 1)"""
            )

        failures = handle({"command": "retry:failed-list"})["analysis_failures"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["kind"], "photo-analysis-job")
        manager = PhotoReviewManager()
        retry = manager.retry_failed()
        completed = self.finish_job(manager, retry["id"])
        self.assertEqual(completed["analyzed_files"], 1)
        self.assertEqual(handle({"command": "retry:failed-list"})["analysis_failures"], [])

    def test_worker_start_failure_marks_analysis_failed_and_releases_manager(self) -> None:
        manager = PhotoReviewManager()

        with patch("photoflow_backend.photo_review.threading.Thread.start", side_effect=RuntimeError("worker unavailable")):
            with self.assertRaisesRegex(RuntimeError, "worker unavailable"):
                manager.start()

        with connect() as connection:
            job = connection.execute(
                "SELECT status, error_summary FROM photo_analysis_jobs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(job[0], "failed")
        self.assertEqual(job[1], "Photo analysis could not be started.")
        self.assertFalse(manager.is_running)

    def test_orphaned_running_analysis_is_recovered_when_status_is_requested(self) -> None:
        with connect() as connection:
            connection.execute(
                """INSERT INTO photo_analysis_jobs(id, status, similarity_threshold, total_files)
                   VALUES ('orphaned-analysis', 'running', 6, 1)"""
            )

        status = PhotoReviewManager().status("orphaned-analysis")

        self.assertEqual(status["status"], "interrupted")
        with connect() as connection:
            stored = connection.execute(
                "SELECT status FROM photo_analysis_jobs WHERE id = 'orphaned-analysis'"
            ).fetchone()[0]
        self.assertEqual(stored, "interrupted")

    def test_manager_is_released_when_final_status_write_fails(self) -> None:
        manager = PhotoReviewManager()
        manager._active_job_id = "failed-final-write"
        manager._cancel = threading.Event()

        with patch("photoflow_backend.photo_review.connect", side_effect=sqlite3.OperationalError("database unavailable")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "database unavailable"):
                manager._run("failed-final-write", 6, threading.Event())

        self.assertFalse(manager.is_running)

    def test_cancellation_stops_after_current_image_and_persists_partial_progress(self) -> None:
        paths = [self.image(f"cancel-{index}") for index in range(3)]
        for path in paths:
            self.index_photo(path)
        entered = threading.Event()
        release = threading.Event()
        original = analyze_photo
        def delayed(path: Path) -> dict[str, object]:
            entered.set()
            release.wait(3)
            return original(path)

        with patch("photoflow_backend.photo_review.analyze_photo", side_effect=delayed):
            manager = PhotoReviewManager()
            job = manager.start()
            self.assertTrue(entered.wait(2))
            manager.cancel(job["id"])
            release.set()
            final = self.finish_job(manager, job["id"])
        self.assertEqual(final["status"], "cancelled")
        self.assertLess(final["processed_files"], final["total_files"])

    def test_review_status_is_persisted_and_filters_lists(self) -> None:
        path = Path(self.temp.name, "review-state.png")
        Image.new("RGB", (320, 240), "gray").save(path)
        media_id = self.index_photo(path)
        manager = PhotoReviewManager()
        job = manager.start()
        self.finish_job(manager, job["id"])

        set_review_status(media_id, "later")
        self.assertEqual(len(list_review("low_resolution", 20, 0, status="later")["items"]), 1)
        self.assertEqual(len(list_review("low_resolution", 20, 0, status="pending")["items"]), 0)
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT review_status FROM photo_review_state WHERE media_id = ?", (media_id,)).fetchone()[0], "later")


if __name__ == "__main__":
    unittest.main()
