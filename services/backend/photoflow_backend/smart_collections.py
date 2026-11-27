from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from photoflow_backend.media_filters import media_filter_arguments, normalize_media_filters
from photoflow_backend.repository import (
    MEDIA_COLUMNS,
    NORMALIZED_CAPTURE_DATE,
    count_media,
    list_media,
    row_to_dict,
)


RECENT_DAYS = 30
HIGH_RESOLUTION_PIXELS = 12_000_000
LARGE_FILE_BYTES = 100 * 1024 * 1024
SCREENSHOT_PREDICATE = """media_type = 'photo' AND (
    filename LIKE 'screenshot%' COLLATE NOCASE OR
    filename LIKE 'screen shot%' COLLATE NOCASE OR
    filename LIKE 'screen capture%' COLLATE NOCASE OR
    filename LIKE 'screencapture%' COLLATE NOCASE
)"""
VIDEO_DURATION_BUCKETS = (
    ("under-1-minute", "Under 1 minute", "duration_seconds < 60"),
    ("1-to-5-minutes", "1–5 minutes", "duration_seconds >= 60 AND duration_seconds < 300"),
    ("5-to-20-minutes", "5–20 minutes", "duration_seconds >= 300 AND duration_seconds < 1200"),
    ("over-20-minutes", "Over 20 minutes", "duration_seconds >= 1200"),
)

_FIXED_COLLECTIONS = (
    (
        "recently_added", "30-days", "Recently Added",
        f"Added to PhotoFlow in the last {RECENT_DAYS} days.",
        "first_scanned_at >= datetime('now', '-30 days')",
    ),
    (
        "recently_viewed", "30-days", "Recently Viewed",
        f"Photos opened in PhotoFlow in the last {RECENT_DAYS} days.",
        f"media_type = 'photo' AND last_viewed_at IS NOT NULL AND last_viewed_at >= datetime('now', '-{RECENT_DAYS} days')",
    ),
    (
        "screenshots", "filename-prefix", "Screenshots",
        "Photos whose filenames begin with a recognized screenshot prefix.",
        SCREENSHOT_PREDICATE,
    ),
    (
        "portrait", "photos", "Portrait Orientation",
        "Photos with known dimensions where height is greater than width.",
        "media_type = 'photo' AND width IS NOT NULL AND height IS NOT NULL AND height > width",
    ),
    (
        "landscape", "photos", "Landscape Orientation",
        "Photos with known dimensions where width is greater than height.",
        "media_type = 'photo' AND width IS NOT NULL AND height IS NOT NULL AND width > height",
    ),
    (
        "high_resolution", "12-megapixels", "High Resolution",
        "Photos with known dimensions of at least 12 megapixels.",
        f"media_type = 'photo' AND width IS NOT NULL AND height IS NOT NULL AND width * height >= {HIGH_RESOLUTION_PIXELS}",
    ),
    (
        "large_files", "100-mebibytes", "Large Files",
        "Media files at least 100 MiB in size.",
        f"size_bytes >= {LARGE_FILE_BYTES}",
    ),
)


def _page(limit: Any, offset: Any) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Page size must be an integer from 1 to 100")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 9_007_199_254_740_991:
        raise ValueError("Page offset must be a non-negative safe integer")
    return limit, offset


def _collection_item(
    kind: str, value: str, name: str, description: str, count: int, cover_media_id: int | None
) -> dict[str, Any]:
    return {
        "kind": kind,
        "value": value,
        "name": name,
        "description": description,
        "media_count": count,
        "cover_media_id": cover_media_id,
    }


def _summary(
    connection: sqlite3.Connection,
    predicate: str,
    parameters: tuple[Any, ...] = (),
    index_name: str | None = None,
) -> tuple[int, int | None]:
    table = f"media_items INDEXED BY {index_name}" if index_name else "media_items"
    row = connection.execute(
        f"SELECT COUNT(*) AS media_count, MAX(id) AS cover_media_id FROM {table} WHERE {predicate}",
        parameters,
    ).fetchone()
    return int(row["media_count"]), row["cover_media_id"]


def list_smart_collections(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    collections: list[dict[str, Any]] = []

    for kind, value, name, description, predicate in _FIXED_COLLECTIONS:
        index_name = {
            "recently_viewed": "idx_media_items_last_viewed",
            "high_resolution": "idx_media_items_photo_pixel_count",
        }.get(kind)
        count, cover_id = _summary(connection, predicate, index_name=index_name)
        collections.append(_collection_item(kind, value, name, description, count, cover_id))

    for kind, format_string, description in (
        ("photo_year", "%Y", "Photos grouped by capture year."),
        ("photo_month", "%Y-%m", "Photos grouped by capture month."),
    ):
        rows = connection.execute(
            f"""SELECT strftime(?, {NORMALIZED_CAPTURE_DATE}) AS value,
                       COUNT(*) AS media_count, MAX(id) AS cover_media_id
                  FROM media_items
                 WHERE media_type = 'photo' AND {NORMALIZED_CAPTURE_DATE} IS NOT NULL
                 GROUP BY value ORDER BY value DESC""",
            (format_string,),
        ).fetchall()
        for row in rows:
            name = row["value"]
            collections.append(_collection_item(kind, name, name, description, row["media_count"], row["cover_media_id"]))

    dimensions = connection.execute(
        """SELECT width, height, COUNT(*) AS media_count, MAX(id) AS cover_media_id
             FROM media_items INDEXED BY idx_media_items_photo_dimensions
            WHERE media_type = 'photo' AND width IS NOT NULL AND height IS NOT NULL
            GROUP BY width, height HAVING COUNT(*) > 1
            ORDER BY media_count DESC, width DESC, height DESC"""
    ).fetchall()
    for row in dimensions:
        value = json.dumps([row["width"], row["height"]], separators=(",", ":"))
        name = f"{row['width']} × {row['height']}"
        collections.append(_collection_item(
            "similar_dimensions", value, name,
            "Photos with the same recorded pixel dimensions.", row["media_count"], row["cover_media_id"]
        ))

    for value, name, predicate in VIDEO_DURATION_BUCKETS:
        count, cover_id = _summary(
            connection,
            f"media_type = 'video' AND duration_seconds IS NOT NULL AND ({predicate})",
            index_name="idx_media_items_video_duration",
        )
        collections.append(_collection_item(
            "video_duration", value, name,
            "Videos grouped by recorded duration.", count, cover_id
        ))

    return collections


def _collection_predicate(kind: Any, value: Any) -> tuple[str, tuple[Any, ...]]:
    if not isinstance(kind, str) or not isinstance(value, str) or len(value) > 250:
        raise ValueError("Invalid smart collection")
    for collection_kind, collection_value, _, _, predicate in _FIXED_COLLECTIONS:
        if kind == collection_kind and value == collection_value:
            return predicate, ()
    if kind == "photo_year" and re.fullmatch(r"\d{4}", value):
        return f"media_type = 'photo' AND strftime('%Y', {NORMALIZED_CAPTURE_DATE}) = ?", (value,)
    if kind == "photo_month" and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        return f"media_type = 'photo' AND strftime('%Y-%m', {NORMALIZED_CAPTURE_DATE}) = ?", (value,)
    if kind == "similar_dimensions":
        try:
            dimensions = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Invalid smart collection dimensions") from error
        if (
            not isinstance(dimensions, list) or len(dimensions) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 100_000 for item in dimensions)
        ):
            raise ValueError("Invalid smart collection dimensions")
        return "media_type = 'photo' AND width = ? AND height = ?", tuple(dimensions)
    if kind == "video_duration":
        for bucket, _, predicate in VIDEO_DURATION_BUCKETS:
            if value == bucket:
                return f"media_type = 'video' AND duration_seconds IS NOT NULL AND ({predicate})", ()
    raise ValueError("Invalid smart collection")


def list_smart_collection_media(
    connection: sqlite3.Connection, kind: Any, value: Any, limit: Any = 60, offset: Any = 0
) -> list[dict[str, Any]]:
    limit, offset = _page(limit, offset)
    predicate, parameters = _collection_predicate(kind, value)
    connection.row_factory = sqlite3.Row
    index_name = {
        "recently_viewed": "idx_media_items_last_viewed",
        "high_resolution": "idx_media_items_photo_pixel_count",
        "similar_dimensions": "idx_media_items_photo_dimensions",
        "video_duration": "idx_media_items_video_duration",
    }.get(kind)
    table = f"media_items INDEXED BY {index_name}" if index_name else "media_items"
    rows = connection.execute(
        f"""SELECT {MEDIA_COLUMNS} FROM {table} WHERE {predicate}
             ORDER BY COALESCE({NORMALIZED_CAPTURE_DATE}, date(first_scanned_at)) DESC, id DESC
             LIMIT ? OFFSET ?""",
        (*parameters, limit, offset),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def list_saved_smart_albums(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT id, name, description, media_type, filters_json, created_at, updated_at
             FROM saved_smart_albums ORDER BY name COLLATE NOCASE, id"""
    ).fetchall()
    albums: list[dict[str, Any]] = []
    for row in rows:
        filters = normalize_media_filters(json.loads(row["filters_json"]))
        arguments = media_filter_arguments(filters)
        count = count_media(connection, row["media_type"], **{key: value for key, value in arguments.items() if key != "sort"})
        covers = list_media(connection, row["media_type"], 1, 0, **arguments)
        albums.append({
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "media_type": row["media_type"],
            "filters": filters,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "media_count": count,
            "cover_media_id": covers[0]["id"] if covers else None,
        })
    return albums


def create_saved_smart_album(
    connection: sqlite3.Connection, name: Any, description: Any, media_type: Any, filters: Any
) -> dict[str, Any]:
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("Smart album name must contain 1 to 100 characters")
    if not isinstance(description, str) or len(description) > 500:
        raise ValueError("Smart album description must be no longer than 500 characters")
    if not isinstance(media_type, str) or media_type not in {"all", "photo", "video"}:
        raise ValueError("Invalid smart album media type")
    normalized = normalize_media_filters(filters)
    try:
        cursor = connection.execute(
            """INSERT INTO saved_smart_albums(name, description, media_type, filters_json)
               VALUES (?, ?, ?, ?)""",
            (name.strip(), description.strip(), media_type, json.dumps(normalized, sort_keys=True)),
        )
    except sqlite3.IntegrityError as error:
        if "unique constraint" in str(error).lower():
            raise ValueError("A smart album with that name already exists") from error
        raise
    album_id = int(cursor.lastrowid)
    return next(album for album in list_saved_smart_albums(connection) if album["id"] == album_id)


def delete_saved_smart_album(connection: sqlite3.Connection, album_id: Any) -> None:
    if isinstance(album_id, bool) or not isinstance(album_id, int) or album_id < 1:
        raise ValueError("Invalid smart album identifier")
    cursor = connection.execute("DELETE FROM saved_smart_albums WHERE id = ?", (album_id,))
    if cursor.rowcount == 0:
        raise ValueError("Smart album not found")


def list_saved_smart_album_media(
    connection: sqlite3.Connection, album_id: Any, limit: Any = 60, offset: Any = 0
) -> list[dict[str, Any]]:
    if isinstance(album_id, bool) or not isinstance(album_id, int) or album_id < 1:
        raise ValueError("Invalid smart album identifier")
    limit, offset = _page(limit, offset)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT media_type, filters_json FROM saved_smart_albums WHERE id = ?",
        (album_id,),
    ).fetchone()
    if not row:
        raise ValueError("Smart album not found")
    filters = normalize_media_filters(json.loads(row["filters_json"]))
    return list_media(connection, row["media_type"], limit, offset, **media_filter_arguments(filters))
