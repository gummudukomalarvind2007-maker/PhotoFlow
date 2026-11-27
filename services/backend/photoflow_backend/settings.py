from __future__ import annotations

import os
import shutil
import sys
from typing import Any

from photoflow_backend.database import application_data_dir, connect, database_path, thumbnails_dir
from photoflow_backend.repository import library_overview
from photoflow_backend.transfers import _thumbnail_root


def settings_overview() -> dict[str, Any]:
    with connect() as connection:
        overview = library_overview(connection)
        preference = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'skip_unchanged_files'"
        ).fetchone()
    cache_root = _thumbnail_root()
    cache_files = 0
    cache_bytes = 0
    for current, _, filenames in os.walk(cache_root, followlinks=False):
        for filename in filenames:
            path = os.path.join(current, filename)
            try:
                stat = os.stat(path, follow_symlinks=False)
            except OSError:
                continue
            if not os.path.islink(path):
                cache_files += 1
                cache_bytes += stat.st_size
    return {
        "application_data_path": str(application_data_dir().resolve()),
        "database_path": str(database_path().resolve()),
        "thumbnail_cache_path": str(thumbnails_dir().resolve()),
        "photo_count": overview["photos"],
        "video_count": overview["videos"],
        "library_size_bytes": overview["total_bytes"],
        "thumbnail_cache_files": cache_files,
        "thumbnail_cache_bytes": cache_bytes,
        "python_version": sys.version.split()[0],
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "ffprobe_available": shutil.which("ffprobe") is not None,
        "theme": "dark",
        "theme_customization_supported": False,
        "skip_unchanged_files": preference is None or preference[0] != "false",
    }


def set_skip_unchanged_files(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Skip unchanged files must be a boolean")
    with connect() as connection:
        connection.execute(
            """INSERT INTO app_metadata(key, value) VALUES ('skip_unchanged_files', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            ("true" if value else "false",),
        )
    return value
