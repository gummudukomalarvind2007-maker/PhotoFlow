from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photoflow_backend.app import handle
from photoflow_backend.database import connect
from photoflow_backend.migrations import apply_migrations
from photoflow_backend.smart_collections import list_smart_collection_media, list_smart_collections


class SmartCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = os.environ.get("PHOTOFLOW_DATA_DIR")
        os.environ["PHOTOFLOW_DATA_DIR"] = str(Path(self.temp.name, "state"))
        with connect() as connection:
            apply_migrations(connection)
            self.screenshot = self.insert_media(
                connection, "Screenshot_2024-04-15.png", "photo", "2024-04-15",
                width=4000, height=3000, size_bytes=100 * 1024 * 1024,
                camera_make="Canon", camera_model="EOS R5",
                first_scanned_days_ago=2,
                last_viewed_days_ago=1,
            )
            self.screenshot_second = self.insert_media(
                connection, "Screen Shot 2024-04-16.png", "photo", "2024-04-16",
                width=4000, height=3000, size_bytes=5_000_000, last_viewed_days_ago=31,
            )
            self.portrait = self.insert_media(
                connection, "trip.jpg", "photo", "2023-08-01",
                width=2000, height=3000, size_bytes=4_000_000,
                camera_make="Canon", camera_model="EOS R5",
            )
            self.landscape = self.insert_media(
                connection, "wide.jpg", "photo", "2022-05-01",
                width=3000, height=2000, size_bytes=4_000_000,
            )
            self.unmatched_screenshot_name = self.insert_media(
                connection, "trip-screenshot.jpg", "photo", "2024-04-17",
                width=1200, height=800, size_bytes=2_000_000,
            )
            self.videos = [
                self.insert_media(connection, f"clip-{seconds}.mp4", "video", None, duration=seconds)
                for seconds in (30, 60, 299, 300, 1199, 1200, 1500)
            ]

    def tearDown(self) -> None:
        if self.old_data_dir is None:
            os.environ.pop("PHOTOFLOW_DATA_DIR", None)
        else:
            os.environ["PHOTOFLOW_DATA_DIR"] = self.old_data_dir
        self.temp.cleanup()

    def insert_media(
        self,
        connection,
        filename: str,
        media_type: str,
        capture_date: str | None,
        *,
        width: int | None = None,
        height: int | None = None,
        size_bytes: int = 1_000_000,
        duration: float | None = None,
        camera_make: str | None = None,
        camera_model: str | None = None,
        first_scanned_days_ago: int | None = None,
        last_viewed_days_ago: int | None = None,
    ) -> int:
        cursor = connection.execute(
            """INSERT INTO media_items
               (source_path, media_type, extension, filename, size_bytes, modified_time_ns,
                width, height, duration_seconds, capture_date, camera_make, camera_model,
                first_scanned_at, last_viewed_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?,
                       COALESCE(?, '2000-01-01 00:00:00'), ?)""",
            (
                str(Path(self.temp.name, filename)), media_type, Path(filename).suffix,
                filename, size_bytes, width, height, duration, capture_date,
                camera_make, camera_model, None, None,
            ),
        )
        if first_scanned_days_ago is not None:
            connection.execute(
                "UPDATE media_items SET first_scanned_at = datetime('now', ?) WHERE id = ?",
                (f"-{first_scanned_days_ago} days", cursor.lastrowid),
            )
        if last_viewed_days_ago is not None:
            connection.execute(
                "UPDATE media_items SET last_viewed_at = datetime('now', ?) WHERE id = ?",
                (f"-{last_viewed_days_ago} days", cursor.lastrowid),
            )
        return int(cursor.lastrowid)

    def test_live_collections_match_metadata_and_filename_criteria(self) -> None:
        with connect() as connection:
            collections = list_smart_collections(connection)
            lookup = {(item["kind"], item["value"]): item for item in collections}
            screenshots = list_smart_collection_media(connection, "screenshots", "filename-prefix")
            portrait = list_smart_collection_media(connection, "portrait", "photos")
            landscape = list_smart_collection_media(connection, "landscape", "photos")
            high_resolution = list_smart_collection_media(connection, "high_resolution", "12-megapixels")
            similar_dimensions = list_smart_collection_media(
                connection, "similar_dimensions", "[4000,3000]"
            )
            year_2024 = list_smart_collection_media(connection, "photo_year", "2024")
            month_2024_04 = list_smart_collection_media(connection, "photo_month", "2024-04")
            under_minute = list_smart_collection_media(connection, "video_duration", "under-1-minute")
            duration_group = list_smart_collection_media(connection, "video_duration", "1-to-5-minutes")
            five_to_twenty = list_smart_collection_media(connection, "video_duration", "5-to-20-minutes")
            over_twenty = list_smart_collection_media(connection, "video_duration", "over-20-minutes")

        self.assertEqual(lookup[("recently_added", "30-days")]["media_count"], 1)
        self.assertEqual(lookup[("recently_viewed", "30-days")]["media_count"], 1)
        self.assertEqual([item["id"] for item in screenshots], [self.screenshot_second, self.screenshot])
        self.assertNotIn(self.unmatched_screenshot_name, {item["id"] for item in screenshots})
        self.assertEqual([item["id"] for item in portrait], [self.portrait])
        self.assertIn(self.landscape, {item["id"] for item in landscape})
        self.assertEqual([item["id"] for item in high_resolution], [self.screenshot_second, self.screenshot])
        self.assertEqual({item["id"] for item in similar_dimensions}, {self.screenshot, self.screenshot_second})
        self.assertEqual({item["id"] for item in year_2024}, {self.screenshot, self.screenshot_second, self.unmatched_screenshot_name})
        self.assertEqual(
            {item["id"] for item in month_2024_04},
            {self.screenshot, self.screenshot_second, self.unmatched_screenshot_name},
        )
        self.assertEqual({item["id"] for item in under_minute}, {self.videos[0]})
        self.assertEqual({item["id"] for item in duration_group}, {self.videos[1], self.videos[2]})
        self.assertEqual({item["id"] for item in five_to_twenty}, {self.videos[3], self.videos[4]})
        self.assertEqual({item["id"] for item in over_twenty}, {self.videos[5], self.videos[6]})
        self.assertEqual(lookup[("large_files", "100-mebibytes")]["media_count"], 1)
        self.assertEqual(lookup[("video_duration", "1-to-5-minutes")]["media_count"], 2)
        self.assertEqual(lookup[("video_duration", "5-to-20-minutes")]["media_count"], 2)
        self.assertEqual(lookup[("video_duration", "over-20-minutes")]["media_count"], 2)

    def test_new_and_removed_media_update_live_and_saved_collection_counts(self) -> None:
        created = handle({
            "command": "saved-smart-albums:create",
            "name": "Canon trips",
            "description": "",
            "media_type": "photo",
            "filters": {"search": "Canon", "min_width": 1500},
        })["album"]
        self.assertEqual(created["media_count"], 2)
        saved_items = handle({
            "command": "saved-smart-albums:media",
            "album_id": created["id"],
        })["items"]
        self.assertEqual({item["id"] for item in saved_items}, {self.screenshot, self.portrait})

        with connect() as connection:
            new_id = self.insert_media(
                connection, "Canon-trip-2.jpg", "photo", "2024-05-02",
                width=2400, height=3000, camera_make="Canon", camera_model="R6",
            )
        albums_after_add = handle({"command": "saved-smart-albums:list"})["albums"]
        matching_count = next(album["media_count"] for album in albums_after_add if album["id"] == created["id"])
        with connect() as connection:
            connection.execute("DELETE FROM media_items WHERE id = ?", (new_id,))
        albums_after_remove = handle({"command": "saved-smart-albums:list"})["albums"]
        remaining_count = next(album["media_count"] for album in albums_after_remove if album["id"] == created["id"])

        self.assertEqual(matching_count, 3)
        self.assertEqual(remaining_count, 2)
        handle({"command": "saved-smart-albums:delete", "album_id": created["id"]})
        self.assertEqual(handle({"command": "saved-smart-albums:list"})["albums"], [])
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 12)

    def test_live_collection_count_tracks_media_addition_and_removal(self) -> None:
        with connect() as connection:
            added_id = self.insert_media(
                connection, "Screenshot_new.png", "photo", "2024-06-01",
                width=1600, height=900,
            )
        with connect() as connection:
            added_count = next(
                item["media_count"] for item in list_smart_collections(connection)
                if item["kind"] == "screenshots"
            )
            connection.execute("DELETE FROM media_items WHERE id = ?", (added_id,))
        with connect() as connection:
            removed_count = next(
                item["media_count"] for item in list_smart_collections(connection)
                if item["kind"] == "screenshots"
            )

        self.assertEqual(added_count, 3)
        self.assertEqual(removed_count, 2)

    def test_combined_search_filters_apply_to_list_count_and_video_duration(self) -> None:
        filters = {
            "search": "EOS",
            "extension": ".jpg",
            "date_from": "2023-01-01",
            "date_to": "2023-12-31",
            "camera_make": "Canon",
            "min_width": 1500,
            "max_width": 2500,
            "min_height": 2500,
            "max_height": 3500,
            "min_resolution_px": 5_000_000,
            "max_resolution_px": 7_000_000,
            "min_size_bytes": 1_000_000,
            "max_size_bytes": 10_000_000,
        }
        items = handle({"command": "media:list", "media_type": "photo", **filters})["items"]
        count = handle({"command": "media:count", "media_type": "photo", **filters})["count"]
        videos = handle({
            "command": "media:list", "media_type": "video",
            "min_duration_seconds": 100, "max_duration_seconds": 300,
        })["items"]

        self.assertEqual([item["id"] for item in items], [self.portrait])
        self.assertEqual(count, len(items))
        self.assertEqual({item["id"] for item in videos}, {self.videos[2], self.videos[3]})

    def test_empty_collection_and_filter_validation(self) -> None:
        with connect() as connection:
            self.assertEqual(list_smart_collection_media(connection, "photo_year", "1900", limit=10), [])
            self.assertTrue(any(
                item["kind"] == "recently_viewed" and item["media_count"] == 1
                for item in list_smart_collections(connection)
            ))
        for filters in (
            {"min_width": 3000, "max_width": 1000},
            {"min_duration_seconds": True},
            {"min_resolution_px": 10_000_000_001},
        ):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                handle({"command": "media:count", "media_type": "photo", **filters})

    def test_successful_photo_open_records_view_but_does_not_change_original(self) -> None:
        original = Path(self.temp.name, "Screenshot_2024-04-15.png")
        original.write_bytes(b"source photo bytes")
        with patch("photoflow_backend.app.full_resolution_data", return_value={"data_url": "data:image/jpeg;base64,test"}):
            result = handle({"command": "media:full-resolution", "media_id": self.screenshot})
        self.assertTrue(result["data_url"].startswith("data:image/jpeg"))
        with connect() as connection:
            viewed_at = connection.execute(
                "SELECT last_viewed_at FROM media_items WHERE id = ?", (self.screenshot,)
            ).fetchone()[0]
            source_path = connection.execute(
                "SELECT source_path FROM media_items WHERE id = ?", (self.screenshot,)
            ).fetchone()[0]
        self.assertIsNotNone(viewed_at)
        self.assertEqual(Path(source_path), original)
        self.assertEqual(original.read_bytes(), b"source photo bytes")

    def test_failed_photo_open_does_not_record_a_view(self) -> None:
        with connect() as connection:
            original_viewed_at = connection.execute(
                "SELECT last_viewed_at FROM media_items WHERE id = ?", (self.screenshot_second,)
            ).fetchone()[0]
        with patch("photoflow_backend.app.full_resolution_data", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                handle({"command": "media:full-resolution", "media_id": self.screenshot_second})
        with connect() as connection:
            viewed_at = connection.execute(
                "SELECT last_viewed_at FROM media_items WHERE id = ?", (self.screenshot_second,)
            ).fetchone()[0]
        self.assertEqual(viewed_at, original_viewed_at)


if __name__ == "__main__":
    unittest.main()
