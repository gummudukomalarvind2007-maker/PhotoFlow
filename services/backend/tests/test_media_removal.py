from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from photoflow_backend.albums import add_album_media, create_album, list_occasions
from photoflow_backend.app import handle
from photoflow_backend.database import connect, thumbnails_dir
from photoflow_backend.migrations import apply_migrations


class MediaRemovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = os.environ.get("PHOTOFLOW_DATA_DIR")
        self.root = Path(self.temp.name)
        os.environ["PHOTOFLOW_DATA_DIR"] = str(self.root / "state")
        self.original = self.root / "original.jpg"
        self.original.write_bytes(b"original media bytes")
        self.thumbnail = thumbnails_dir() / "cached.jpg"
        self.thumbnail.write_bytes(b"thumbnail cache bytes")
        with connect() as connection:
            apply_migrations(connection)
            self.photo_id = self.insert_media(
                connection, self.original, "photo", "2024-01-02", str(self.thumbnail)
            )
            self.shared_id = self.insert_media(
                connection, self.root / "shared.jpg", "photo", "2024-01-02", str(self.thumbnail)
            )
            self.album = create_album(connection, "Keep album")
            add_album_media(connection, self.album["id"], [self.photo_id, self.shared_id])

    def tearDown(self) -> None:
        if self.old_data_dir is None:
            os.environ.pop("PHOTOFLOW_DATA_DIR", None)
        else:
            os.environ["PHOTOFLOW_DATA_DIR"] = self.old_data_dir
        self.temp.cleanup()

    def insert_media(
        self, connection, source: Path, media_type: str, capture_date: str | None,
        thumbnail_path: str | None = None,
    ) -> int:
        if not source.exists():
            source.write_bytes(b"media")
        cursor = connection.execute(
            """INSERT INTO media_items
               (source_path, media_type, extension, filename, size_bytes, modified_time_ns,
                capture_date, thumbnail_path)
               VALUES (?, ?, ?, ?, 5, 1, ?, ?)""",
            (str(source), media_type, source.suffix, source.name, capture_date, thumbnail_path),
        )
        return int(cursor.lastrowid)

    def test_removal_deletes_only_database_record_and_unshared_cached_thumbnail(self) -> None:
        # Give this record a cache unique to it; the shared cache must remain for the other row.
        unique_thumbnail = thumbnails_dir() / "unique.jpg"
        unique_thumbnail.write_bytes(b"unique cache")
        with connect() as connection:
            unique_id = self.insert_media(
                connection, self.root / "unique-source.jpg", "photo", "2024-02-03", str(unique_thumbnail)
            )
            add_album_media(connection, self.album["id"], [unique_id])

        before = self.original.read_bytes()
        result = handle({"command": "media:remove-selected", "mode": "ids", "media_ids": [self.photo_id]})

        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["thumbnails_deleted"], 0)
        self.assertEqual(self.original.read_bytes(), before)
        self.assertTrue(self.original.exists())
        self.assertTrue(self.thumbnail.exists())
        with connect() as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM media_items WHERE id = ?", (self.photo_id,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM album_members WHERE album_id = ? AND media_id = ?",
                (self.album["id"], self.photo_id),
            ).fetchone()[0], 0)
            album = connection.execute("SELECT id FROM albums WHERE id = ?", (self.album["id"],)).fetchone()
            self.assertIsNotNone(album)
            self.assertEqual(
                sum(item["media_count"] for item in list_occasions(connection, "day") if item["period"] == "2024-01-02"),
                1,
            )

        unique_result = handle({"command": "media:remove-selected", "mode": "ids", "media_ids": [unique_id]})
        self.assertEqual(unique_result["thumbnails_deleted"], 1)
        self.assertFalse(unique_thumbnail.exists())

    def test_select_all_filter_removal_covers_all_pages_and_honors_exclusions(self) -> None:
        with connect() as connection:
            ids = [
                self.insert_media(connection, self.root / f"bulk-{index:03}.jpg", "photo", "2024-03-01")
                for index in range(65)
            ]
        filters = {"date_from": "2024-03-01", "date_to": "2024-03-01", "sort": "newest"}
        count = handle({"command": "media:count", "media_type": "photo", **filters})["count"]
        result = handle({
            "command": "media:remove-selected",
            "mode": "filtered",
            "media_type": "photo",
            "filters": filters,
            "excluded_ids": [ids[0], ids[-1]],
        })

        self.assertEqual(count, 65)
        self.assertEqual(result["removed"], 63)
        with connect() as connection:
            remaining = {
                row[0] for row in connection.execute(
                    "SELECT id FROM media_items WHERE capture_date = '2024-03-01'"
                )
            }
        self.assertEqual(remaining, {ids[0], ids[-1]})


if __name__ == "__main__":
    unittest.main()
