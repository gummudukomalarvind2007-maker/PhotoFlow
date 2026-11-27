from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

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
from photoflow_backend.app import handle
from photoflow_backend.database import connect
from photoflow_backend.migrations import apply_migrations


class AlbumTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = os.environ.get("PHOTOFLOW_DATA_DIR")
        os.environ["PHOTOFLOW_DATA_DIR"] = str(Path(self.temp.name, "state"))
        with connect() as connection:
            apply_migrations(connection)
            self.photo = self.insert_media(connection, "photo.jpg", "photo", "2023-04-15", "Canon", "R5")
            self.video = self.insert_media(connection, "clip.mp4", "video", None, None, None)
            self.later = self.insert_media(connection, "later.png", "photo", "2024-02-01", "Nikon", "Z8")

    def tearDown(self) -> None:
        if self.old_data_dir is None:
            os.environ.pop("PHOTOFLOW_DATA_DIR", None)
        else:
            os.environ["PHOTOFLOW_DATA_DIR"] = self.old_data_dir
        self.temp.cleanup()

    def insert_media(
        self, connection, filename: str, media_type: str, capture_date: str | None,
        camera_make: str | None, camera_model: str | None,
    ) -> int:
        cursor = connection.execute(
            """INSERT INTO media_items
               (source_path, media_type, extension, filename, size_bytes, modified_time_ns,
                capture_date, camera_make, camera_model)
               VALUES (?, ?, ?, ?, 123, 1, ?, ?, ?)""",
            (str(Path(self.temp.name, filename)), media_type, Path(filename).suffix, filename,
             capture_date, camera_make, camera_model),
        )
        return int(cursor.lastrowid)

    def test_create_rename_delete_and_migration(self) -> None:
        with connect() as connection:
            album = create_album(connection, "  Travel  ", " Summer trip ")
            renamed = rename_album(connection, album["id"], "Journeys", "Updated")
            self.assertEqual((renamed["name"], renamed["description"]), ("Journeys", "Updated"))
            add_album_media(connection, album["id"], [self.photo])
            delete_album(connection, album["id"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM album_members").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 3)
            self.assertIn("005_albums.sql", {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            })
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'album_members'"
            ).fetchone())

    def test_add_remove_duplicate_membership_counts_and_cover(self) -> None:
        with connect() as connection:
            album = create_album(connection, "Favorites")
            first = add_album_media(connection, album["id"], [self.photo, self.later, self.photo])
            duplicate = add_album_media(connection, album["id"], [self.photo])
            listed = list_albums(connection)[0]
            members = list_album_media(connection, album["id"])
            removed = remove_album_media(connection, album["id"], [self.later])
            remaining = list_album_media(connection, album["id"])

        self.assertEqual(first["added"], 2)
        self.assertEqual(duplicate["added"], 0)
        self.assertEqual(listed["media_count"], 2)
        self.assertEqual(listed["cover_media_id"], self.later)
        self.assertEqual({item["id"] for item in members}, {self.photo, self.later})
        self.assertEqual(removed["removed"], 1)
        self.assertEqual([item["id"] for item in remaining], [self.photo])
        with connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 3)

    def test_duplicate_album_names_and_invalid_ids_are_rejected(self) -> None:
        with connect() as connection:
            album = create_album(connection, "Trips")
            with self.assertRaisesRegex(ValueError, "already exists"):
                create_album(connection, " trips ")
            with self.assertRaisesRegex(ValueError, "Invalid album"):
                delete_album(connection, True)
            with self.assertRaisesRegex(ValueError, "not found"):
                rename_album(connection, 999, "Missing", "")
            with self.assertRaisesRegex(ValueError, "not found"):
                add_album_media(connection, album["id"], [999])

    def test_multi_item_add_rolls_back_when_any_media_id_is_invalid(self) -> None:
        with connect() as connection:
            album = create_album(connection, "Atomic")
            with self.assertRaisesRegex(ValueError, "not found"):
                add_album_media(connection, album["id"], [self.photo, 999_999])
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM album_members WHERE album_id = ?", (album["id"],)
            ).fetchone()[0], 0)

    def test_smart_albums_reflect_current_date_camera_type_and_recent_media(self) -> None:
        with connect() as connection:
            smart = list_smart_albums(connection)
            years = [item for item in smart if item["kind"] == "year"]
            months = [item for item in smart if item["kind"] == "month"]
            cameras = [item for item in smart if item["kind"] == "camera"]
            photos = list_smart_album_media(connection, "media_type", "photo")
            camera_items = list_smart_album_media(connection, "camera", cameras[0]["value"])
            year_items = list_smart_album_media(connection, "year", years[0]["value"])
            video_items = list_smart_album_media(connection, "videos", "all")

        self.assertEqual([item["value"] for item in years], ["2024", "2023"])
        self.assertIn("2024-02", [item["value"] for item in months])
        self.assertTrue(any("Canon R5" == item["name"] for item in cameras))
        self.assertEqual({item["id"] for item in photos}, {self.photo, self.later})
        self.assertEqual(len(camera_items), 1)
        self.assertEqual([item["id"] for item in year_items], [self.later])
        self.assertEqual([item["id"] for item in video_items], [self.video])
        self.assertTrue(any(item["kind"] == "videos" for item in smart))
        self.assertTrue(any(item["kind"] == "recent" for item in smart))

    def test_smart_album_filters_and_pagination_validate_user_input(self) -> None:
        with connect() as connection:
            with self.assertRaisesRegex(ValueError, "Invalid smart album"):
                list_smart_album_media(connection, "camera", '["Canon"]')
            with self.assertRaisesRegex(ValueError, "Invalid smart album"):
                list_smart_album_media(connection, "year", "20XX")
            with self.assertRaisesRegex(ValueError, "Page size"):
                list_smart_album_media(connection, "videos", "all", 0)

    def test_occasions_group_capture_and_index_dates_and_open_media(self) -> None:
        with connect() as connection:
            connection.execute(
                "UPDATE media_items SET first_scanned_at = '2024-02-12 10:00:00' WHERE id = ?",
                (self.video,),
            )
            occasions = list_occasions(connection, "month")
            indexed = [item for item in occasions if item["date_source"] == "indexed"]
            captured = [item for item in occasions if item["date_source"] == "captured"]
            captured_items = list_occasion_media(connection, "month", "2024-02", "captured")
            indexed_items = list_occasion_media(connection, "month", "2024-02", "indexed")

        self.assertTrue(any(item["period"] == "2023-04" for item in captured))
        self.assertTrue(any(item["period"] == "2024-02" for item in indexed))
        self.assertEqual([item["id"] for item in captured_items], [self.later])
        self.assertEqual([item["id"] for item in indexed_items], [self.video])

    def test_occasion_date_range_limits_and_validation(self) -> None:
        with connect() as connection:
            result = list_occasions(connection, "day", "2024-01-01", "2024-03-01")
            self.assertEqual([row["period"] for row in result], ["2024-02-01"])
            with self.assertRaisesRegex(ValueError, "must not be later"):
                list_occasions(connection, "month", "2024-03-01", "2024-01-01")
            with self.assertRaisesRegex(ValueError, "Invalid occasion start date"):
                list_occasions(connection, "month", "2024-02-31")
            with self.assertRaisesRegex(ValueError, "grouping"):
                list_occasions(connection, "week")
            with self.assertRaisesRegex(ValueError, "Invalid occasion"):
                list_occasion_media(connection, "year", "2023", "unknown")

    def test_album_ipc_backend_commands_return_renderer_safe_media(self) -> None:
        album = handle({"command": "albums:create", "name": "Private paths"})
        added = handle({
            "command": "albums:add-media", "album_id": album["album"]["id"], "media_ids": [self.photo],
        })
        items = handle({"command": "albums:media", "album_id": album["album"]["id"]})["items"]
        smart = handle({"command": "smart-albums:list"})["albums"]
        occasions = handle({"command": "occasions:list", "group_by": "month"})["occasions"]
        self.assertEqual(added["added"], 1)
        self.assertEqual(items[0]["filename"], "photo.jpg")
        self.assertNotIn("source_path", items[0])
        self.assertNotIn("thumbnail_path", items[0])
        self.assertTrue(smart)
        self.assertTrue(occasions)


if __name__ == "__main__":
    unittest.main()
