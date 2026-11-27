from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from photoflow_backend.albums import create_album
from photoflow_backend.database import connect, database_path
from photoflow_backend.migrations import apply_migrations


class ResourceManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = os.environ.get("PHOTOFLOW_DATA_DIR")
        os.environ["PHOTOFLOW_DATA_DIR"] = str(Path(self.temp.name, "state"))

    def tearDown(self) -> None:
        if self.old_data_dir is None:
            os.environ.pop("PHOTOFLOW_DATA_DIR", None)
        else:
            os.environ["PHOTOFLOW_DATA_DIR"] = self.old_data_dir
        self.temp.cleanup()

    def test_database_uses_wal_busy_timeout_and_preserves_existing_rows(self) -> None:
        with connect() as connection:
            apply_migrations(connection)
            album = create_album(connection, "Preserved album")
            connection.execute(
                "INSERT INTO media_items (source_path, media_type, extension, filename, size_bytes, modified_time_ns) "
                "VALUES (?, 'photo', '.jpg', 'preserved.jpg', 100, 1)",
                (str(Path(self.temp.name, "preserved.jpg")),),
            )
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 30000)

        with connect() as connection:
            apply_migrations(connection)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM media_items").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT name FROM albums WHERE id = ?", (album["id"],)).fetchone()[0],
                "Preserved album",
            )

    def test_related_context_changes_roll_back_when_an_exception_occurs(self) -> None:
        with connect() as connection:
            apply_migrations(connection)

        try:
            with connect() as connection:
                create_album(connection, "Rollback album")
                connection.execute(
                    "INSERT INTO media_items (source_path, media_type, extension, filename, size_bytes, modified_time_ns) "
                    "VALUES (?, 'photo', '.jpg', 'rollback.jpg', 100, 1)",
                    (str(Path(self.temp.name, "rollback.jpg")),),
                )
                raise RuntimeError("stop transaction")
        except RuntimeError:
            pass

        with connect() as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM albums WHERE name = 'Rollback album'"
                ).fetchone()
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM media_items WHERE filename = 'rollback.jpg'"
                ).fetchone()
            )

    def test_database_busy_error_is_supported_by_a_timeout_connection(self) -> None:
        with connect() as connection:
            apply_migrations(connection)

        holder = sqlite3.connect(database_path(), timeout=1, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            connection = sqlite3.connect(database_path(), timeout=0.05)
            connection.execute("PRAGMA busy_timeout = 50")
            with self.assertRaises(sqlite3.OperationalError) as caught:
                connection.execute(
                    "INSERT INTO albums (name, description) VALUES ('Busy album', '')"
                )
            self.assertIn("locked", str(caught.exception).lower())
            connection.close()
        finally:
            holder.rollback()
            holder.close()


if __name__ == "__main__":
    unittest.main()
