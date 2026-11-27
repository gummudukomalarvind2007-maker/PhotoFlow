from __future__ import annotations

from datetime import date, timedelta
import os
import tempfile
import unittest
from pathlib import Path

from photoflow_backend.database import connect
from photoflow_backend.migrations import apply_migrations
from photoflow_backend.repository import NORMALIZED_CAPTURE_DATE, count_media, list_media, media_where_clause
from photoflow_backend.smart_collections import list_smart_collection_media, list_smart_collections


class GalleryPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = os.environ.get("PHOTOFLOW_DATA_DIR")
        os.environ["PHOTOFLOW_DATA_DIR"] = str(Path(self.temp.name, "state"))
        with connect() as connection:
            apply_migrations(connection)
            self._insert_synthetic_media(connection, 12_000)

    def tearDown(self) -> None:
        if self.old_data_dir is None:
            os.environ.pop("PHOTOFLOW_DATA_DIR", None)
        else:
            os.environ["PHOTOFLOW_DATA_DIR"] = self.old_data_dir
        self.temp.cleanup()

    @staticmethod
    def _insert_synthetic_media(connection, count: int) -> None:
        first_date = date(2023, 1, 1)
        records = []
        for index in range(count):
            media_type = "video" if index % 5 == 0 else "photo"
            records.append((
                f"C:/synthetic/{index:06d}{'.mp4' if media_type == 'video' else '.jpg'}",
                media_type,
                ".mp4" if media_type == "video" else ".jpg",
                f"IMG_{index:06d}",
                1_000_000 + index,
                1_700_000_000_000_000_000 + index,
                (first_date + timedelta(days=index % 800)).isoformat() if index % 7 else None,
                2_500 + (index % 8) * 350 if media_type == "photo" else None,
                1_800 + (index % 5) * 400 if media_type == "photo" else None,
                float(index % 3_600) if media_type == "video" else None,
            ))
        connection.executemany(
            """INSERT INTO media_items(
                 source_path, media_type, extension, filename, size_bytes, modified_time_ns, capture_date,
                 width, height, duration_seconds
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            records,
        )

    def test_filtered_gallery_pages_are_complete_and_use_the_order_index(self) -> None:
        filters = {"extension": ".jpg", "date_from": "2024-01-01", "date_to": "2024-12-31"}
        with connect() as connection:
            total = count_media(connection, "photo", **filters)
            self.assertGreater(total, 1_000)
            ids: list[int] = []
            offset = 0
            while offset < total:
                page = list_media(connection, "photo", 100, offset, **filters, sort="newest", display_only=True)
                self.assertTrue(page)
                self.assertNotIn("source_path", page[0])
                self.assertNotIn("metadata_json", page[0])
                ids.extend(int(item["id"]) for item in page)
                offset += len(page)

            where_clause, parameters = media_where_clause("photo", **filters)
            plan = connection.execute(
                f"""EXPLAIN QUERY PLAN SELECT id FROM media_items{where_clause}
                    ORDER BY {NORMALIZED_CAPTURE_DATE} IS NULL,
                             {NORMALIZED_CAPTURE_DATE} DESC, id
                    LIMIT ? OFFSET ?""",
                (*parameters, 100, 0),
            ).fetchall()
            plan_details = " ".join(str(row[3]) for row in plan)
            oldest_plan = connection.execute(
                f"""EXPLAIN QUERY PLAN SELECT id FROM media_items WHERE media_type = 'photo'
                    ORDER BY {NORMALIZED_CAPTURE_DATE} IS NULL,
                             {NORMALIZED_CAPTURE_DATE} ASC, id
                    LIMIT 100 OFFSET 500""",
            ).fetchall()
            oldest_details = " ".join(str(row[3]) for row in oldest_plan)
            all_media_ids = [
                int(item["id"])
                for offset in range(0, 500, 100)
                for item in list_media(connection, "all", 100, offset, sort="newest", display_only=True)
            ]

        self.assertEqual(len(ids), total)
        self.assertEqual(len(set(ids)), total)
        self.assertIn("idx_media_items_type_capture_newest", plan_details)
        self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", plan_details)
        self.assertEqual(len(all_media_ids), 500)
        self.assertEqual(len(set(all_media_ids)), 500)
        self.assertIn("idx_media_items_type_capture_oldest", oldest_details)
        self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", oldest_details)

    def test_smart_collection_pages_are_complete_and_use_metadata_indexes(self) -> None:
        with connect() as connection:
            collections = list_smart_collections(connection)
            high_resolution = next(
                item for item in collections
                if item["kind"] == "high_resolution" and item["value"] == "12-megapixels"
            )
            paged_ids: list[int] = []
            offset = 0
            while offset < high_resolution["media_count"]:
                page = list_smart_collection_media(
                    connection, "high_resolution", "12-megapixels", 100, offset
                )
                self.assertTrue(page)
                paged_ids.extend(int(item["id"]) for item in page)
                offset += len(page)

            pixel_plan = connection.execute(
                """EXPLAIN QUERY PLAN SELECT id FROM media_items INDEXED BY idx_media_items_photo_pixel_count
                   WHERE media_type = 'photo' AND width IS NOT NULL AND height IS NOT NULL
                     AND width * height >= 12000000"""
            ).fetchall()
            dimensions_plan = connection.execute(
                """EXPLAIN QUERY PLAN SELECT width, height, COUNT(*) FROM media_items
                   INDEXED BY idx_media_items_photo_dimensions
                   WHERE media_type = 'photo' AND width IS NOT NULL AND height IS NOT NULL
                   GROUP BY width, height"""
            ).fetchall()
            duration_plan = connection.execute(
                """EXPLAIN QUERY PLAN SELECT id FROM media_items INDEXED BY idx_media_items_video_duration
                   WHERE media_type = 'video' AND duration_seconds IS NOT NULL
                     AND duration_seconds >= 60 AND duration_seconds < 300"""
            ).fetchall()
            viewed_plan = connection.execute(
                """EXPLAIN QUERY PLAN SELECT id FROM media_items INDEXED BY idx_media_items_last_viewed
                   WHERE media_type = 'photo' AND last_viewed_at IS NOT NULL
                     AND last_viewed_at >= datetime('now', '-30 days')"""
            ).fetchall()

        self.assertEqual(len(paged_ids), high_resolution["media_count"])
        self.assertEqual(len(set(paged_ids)), high_resolution["media_count"])
        self.assertIn("idx_media_items_photo_pixel_count", " ".join(str(row[3]) for row in pixel_plan))
        self.assertIn("idx_media_items_photo_dimensions", " ".join(str(row[3]) for row in dimensions_plan))
        self.assertIn("idx_media_items_video_duration", " ".join(str(row[3]) for row in duration_plan))
        self.assertIn("idx_media_items_last_viewed", " ".join(str(row[3]) for row in viewed_plan))


if __name__ == "__main__":
    unittest.main()
