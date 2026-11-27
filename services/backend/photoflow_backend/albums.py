from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Any

from photoflow_backend.repository import MEDIA_COLUMNS, NORMALIZED_CAPTURE_DATE, row_to_dict


def _positive_id(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Invalid {label} identifier")
    return value


def _page(limit: Any, offset: Any) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Page size must be an integer from 1 to 100")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 9_007_199_254_740_991:
        raise ValueError("Page offset must be a non-negative safe integer")
    return limit, offset


def _validate_date(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"Invalid {label} date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise ValueError(f"Invalid {label} date") from error


def _media_record(row: sqlite3.Row) -> dict[str, Any]:
    return row_to_dict(row)


def _album_record(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


@contextmanager
def _membership_transaction(connection: sqlite3.Connection):
    connection.execute("SAVEPOINT album_membership_operation")
    try:
        yield
    except BaseException:
        connection.execute("ROLLBACK TO album_membership_operation")
        connection.execute("RELEASE album_membership_operation")
        raise
    else:
        connection.execute("RELEASE album_membership_operation")


def list_albums(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    normalized_cover_date = NORMALIZED_CAPTURE_DATE.replace("capture_date", "cover.capture_date")
    rows = connection.execute(
        f"""SELECT a.id, a.name, a.description, a.created_at, a.updated_at,
                  COUNT(m.media_id) AS media_count,
                  (SELECT am.media_id
                     FROM album_members am JOIN media_items cover ON cover.id = am.media_id
                    WHERE am.album_id = a.id
                    ORDER BY COALESCE({normalized_cover_date}, date(cover.first_scanned_at)) DESC,
                             cover.id DESC LIMIT 1) AS cover_media_id
             FROM albums a LEFT JOIN album_members m ON m.album_id = a.id
            GROUP BY a.id ORDER BY a.name COLLATE NOCASE, a.id"""
    ).fetchall()
    return [_album_record(row) for row in rows]


def create_album(connection: sqlite3.Connection, name: Any, description: Any = "") -> dict[str, Any]:
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("Album name must contain 1 to 100 characters")
    if not isinstance(description, str) or len(description) > 500:
        raise ValueError("Album description must be no longer than 500 characters")
    try:
        cursor = connection.execute(
            "INSERT INTO albums(name, description) VALUES (?, ?)",
            (name.strip(), description.strip()),
        )
    except sqlite3.IntegrityError as error:
        if "albums.name" in str(error).lower() or "unique constraint" in str(error).lower():
            raise ValueError("An album with that name already exists") from error
        raise
    return get_album(connection, int(cursor.lastrowid))


def get_album(connection: sqlite3.Connection, album_id: Any) -> dict[str, Any]:
    album_id = _positive_id(album_id, "album")
    connection.row_factory = sqlite3.Row
    normalized_cover_date = NORMALIZED_CAPTURE_DATE.replace("capture_date", "cover.capture_date")
    row = connection.execute(
        f"""SELECT a.id, a.name, a.description, a.created_at, a.updated_at,
                  COUNT(m.media_id) AS media_count,
                  (SELECT am.media_id
                     FROM album_members am JOIN media_items cover ON cover.id = am.media_id
                    WHERE am.album_id = a.id
                    ORDER BY COALESCE({normalized_cover_date}, date(cover.first_scanned_at)) DESC,
                             cover.id DESC LIMIT 1) AS cover_media_id
             FROM albums a LEFT JOIN album_members m ON m.album_id = a.id
            WHERE a.id = ? GROUP BY a.id""",
        (album_id,),
    ).fetchone()
    if not row:
        raise ValueError("Album not found")
    return _album_record(row)


def rename_album(connection: sqlite3.Connection, album_id: Any, name: Any, description: Any) -> dict[str, Any]:
    album_id = _positive_id(album_id, "album")
    get_album(connection, album_id)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("Album name must contain 1 to 100 characters")
    if not isinstance(description, str) or len(description) > 500:
        raise ValueError("Album description must be no longer than 500 characters")
    try:
        connection.execute(
            "UPDATE albums SET name = ?, description = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name.strip(), description.strip(), album_id),
        )
    except sqlite3.IntegrityError as error:
        if "unique constraint" in str(error).lower():
            raise ValueError("An album with that name already exists") from error
        raise
    return get_album(connection, album_id)


def delete_album(connection: sqlite3.Connection, album_id: Any) -> None:
    album_id = _positive_id(album_id, "album")
    cursor = connection.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    if cursor.rowcount == 0:
        raise ValueError("Album not found")


def _validate_media_ids(connection: sqlite3.Connection, media_ids: Any) -> list[int]:
    if not isinstance(media_ids, list) or not 1 <= len(media_ids) <= 500:
        raise ValueError("Select between 1 and 500 media items")
    normalized = list(dict.fromkeys(_positive_id(value, "media") for value in media_ids))
    placeholders = ",".join("?" for _ in normalized)
    rows = connection.execute(f"SELECT id FROM media_items WHERE id IN ({placeholders})", normalized).fetchall()
    if len(rows) != len(normalized):
        raise ValueError("One or more media items were not found")
    return normalized


def add_album_media(connection: sqlite3.Connection, album_id: Any, media_ids: Any) -> dict[str, int]:
    album_id = _positive_id(album_id, "album")
    with _membership_transaction(connection):
        get_album(connection, album_id)
        normalized = _validate_media_ids(connection, media_ids)
        cursor = connection.executemany(
            "INSERT INTO album_members(album_id, media_id) VALUES (?, ?) ON CONFLICT(album_id, media_id) DO NOTHING",
            [(album_id, media_id) for media_id in normalized],
        )
    return {"added": cursor.rowcount}


def remove_album_media(connection: sqlite3.Connection, album_id: Any, media_ids: Any) -> dict[str, int]:
    album_id = _positive_id(album_id, "album")
    with _membership_transaction(connection):
        get_album(connection, album_id)
        normalized = _validate_media_ids(connection, media_ids)
        cursor = connection.executemany(
            "DELETE FROM album_members WHERE album_id = ? AND media_id = ?",
            [(album_id, media_id) for media_id in normalized],
        )
    return {"removed": cursor.rowcount}


def list_album_media(
    connection: sqlite3.Connection, album_id: Any, limit: Any = 60, offset: Any = 0
) -> list[dict[str, Any]]:
    album_id = _positive_id(album_id, "album")
    limit, offset = _page(limit, offset)
    get_album(connection, album_id)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        f"""SELECT {', '.join('m.' + column.strip() for column in MEDIA_COLUMNS.split(','))}
              FROM album_members am JOIN media_items m ON m.id = am.media_id
             WHERE am.album_id = ?
             ORDER BY COALESCE({NORMALIZED_CAPTURE_DATE.replace('capture_date', 'm.capture_date')},
                               date(m.first_scanned_at)) DESC, m.id DESC
             LIMIT ? OFFSET ?""",
        (album_id, limit, offset),
    ).fetchall()
    return [_media_record(row) for row in rows]


def _smart_album_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    albums: list[dict[str, Any]] = []

    def append_group(kind: str, value: str, name: str, count: int, cover_media_id: int | None) -> None:
        albums.append({
            "kind": kind, "value": value, "name": name,
            "media_count": count, "cover_media_id": cover_media_id,
        })

    groups = connection.execute(
        f"""SELECT strftime('%Y', {NORMALIZED_CAPTURE_DATE}) AS value, COUNT(*) AS media_count, MAX(id) AS cover_media_id
              FROM media_items WHERE {NORMALIZED_CAPTURE_DATE} IS NOT NULL
             GROUP BY value ORDER BY value DESC"""
    ).fetchall()
    for row in groups:
        append_group("year", row["value"], row["value"], row["media_count"], row["cover_media_id"])

    groups = connection.execute(
        f"""SELECT strftime('%Y-%m', {NORMALIZED_CAPTURE_DATE}) AS value, COUNT(*) AS media_count, MAX(id) AS cover_media_id
              FROM media_items WHERE {NORMALIZED_CAPTURE_DATE} IS NOT NULL
             GROUP BY value ORDER BY value DESC"""
    ).fetchall()
    for row in groups:
        append_group("month", row["value"], row["value"], row["media_count"], row["cover_media_id"])

    groups = connection.execute(
        """SELECT camera_make, camera_model, COUNT(*) AS media_count, MAX(id) AS cover_media_id
             FROM media_items WHERE camera_make IS NOT NULL OR camera_model IS NOT NULL
            GROUP BY camera_make, camera_model ORDER BY camera_make COLLATE NOCASE, camera_model COLLATE NOCASE"""
    ).fetchall()
    for row in groups:
        make, model = row["camera_make"] or "Unknown make", row["camera_model"] or "Unknown model"
        append_group("camera", json.dumps([row["camera_make"], row["camera_model"]]), f"{make} {model}", row["media_count"], row["cover_media_id"])

    groups = connection.execute(
        "SELECT media_type, COUNT(*) AS media_count, MAX(id) AS cover_media_id FROM media_items GROUP BY media_type"
    ).fetchall()
    for row in groups:
        kind = "media_type" if row["media_type"] == "photo" else "videos"
        value = row["media_type"] if row["media_type"] == "photo" else "all"
        append_group(kind, value, "Photos" if row["media_type"] == "photo" else "Videos", row["media_count"], row["cover_media_id"])

    recent = connection.execute(
        """SELECT COUNT(*) AS media_count, MAX(id) AS cover_media_id FROM media_items
            WHERE first_scanned_at >= datetime('now', '-30 days')"""
    ).fetchone()
    if recent["media_count"]:
        append_group("recent", "30-days", "Recently added", recent["media_count"], recent["cover_media_id"])
    return albums


def list_smart_albums(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return _smart_album_rows(connection)


def _smart_predicate(kind: Any, value: Any) -> tuple[str, list[Any]]:
    if not isinstance(kind, str) or not isinstance(value, str) or len(value) > 250:
        raise ValueError("Invalid smart album")
    if kind == "year" and re.fullmatch(r"\d{4}", value):
        return f"strftime('%Y', {NORMALIZED_CAPTURE_DATE}) = ?", [value]
    if kind == "month" and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        return f"strftime('%Y-%m', {NORMALIZED_CAPTURE_DATE}) = ?", [value]
    if kind == "camera":
        try:
            camera = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Invalid smart album") from error
        if not isinstance(camera, list) or len(camera) != 2 or any(item is not None and (not isinstance(item, str) or len(item) > 100) for item in camera):
            raise ValueError("Invalid smart album")
        return "camera_make IS ? AND camera_model IS ?", camera
    if kind == "media_type" and value in {"photo", "video"}:
        return "media_type = ?", [value]
    if kind == "recent" and value == "30-days":
        return "first_scanned_at >= datetime('now', '-30 days')", []
    if kind == "videos" and value == "all":
        return "media_type = 'video'", []
    raise ValueError("Invalid smart album")


def list_smart_album_media(
    connection: sqlite3.Connection, kind: Any, value: Any, limit: Any = 60, offset: Any = 0
) -> list[dict[str, Any]]:
    limit, offset = _page(limit, offset)
    predicate, parameters = _smart_predicate(kind, value)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        f"SELECT {MEDIA_COLUMNS} FROM media_items WHERE {predicate} "
        f"ORDER BY COALESCE({NORMALIZED_CAPTURE_DATE}, date(first_scanned_at)) DESC, id DESC LIMIT ? OFFSET ?",
        (*parameters, limit, offset),
    ).fetchall()
    return [_media_record(row) for row in rows]


def _occasion_date_expression() -> str:
    return f"COALESCE({NORMALIZED_CAPTURE_DATE}, date(first_scanned_at))"


def list_occasions(
    connection: sqlite3.Connection,
    group_by: Any = "month",
    date_from: Any = None,
    date_to: Any = None,
    limit: Any = 100,
    offset: Any = 0,
) -> list[dict[str, Any]]:
    if group_by not in {"year", "month", "day"}:
        raise ValueError("Occasion grouping must be year, month, or day")
    limit, offset = _page(limit, offset)
    if date_from is not None:
        date_from = _validate_date(date_from, "occasion start")
    if date_to is not None:
        date_to = _validate_date(date_to, "occasion end")
    if date_from and date_to and date_from > date_to:
        raise ValueError("Occasion start date must not be later than end date")
    period_format = {"year": "%Y", "month": "%Y-%m", "day": "%Y-%m-%d"}[group_by]
    event_date = _occasion_date_expression()
    predicates = [f"{event_date} IS NOT NULL"]
    parameters: list[Any] = []
    if date_from:
        predicates.append(f"{event_date} >= date(?)")
        parameters.append(date_from)
    if date_to:
        predicates.append(f"{event_date} <= date(?)")
        parameters.append(date_to)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        f"""SELECT strftime(?, {event_date}) AS period,
                   CASE WHEN {NORMALIZED_CAPTURE_DATE} IS NULL THEN 'indexed' ELSE 'captured' END AS date_source,
                   COUNT(*) AS media_count, MAX(id) AS cover_media_id
              FROM media_items WHERE {' AND '.join(predicates)}
             GROUP BY period, date_source ORDER BY period DESC, date_source
             LIMIT ? OFFSET ?""",
        (period_format, *parameters, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def list_occasion_media(
    connection: sqlite3.Connection, group_by: Any, period: Any, date_source: Any,
    limit: Any = 60, offset: Any = 0,
) -> list[dict[str, Any]]:
    if group_by not in {"year", "month", "day"} or date_source not in {"captured", "indexed"} or not isinstance(period, str):
        raise ValueError("Invalid occasion")
    expected = {"year": r"\d{4}", "month": r"\d{4}-\d{2}", "day": r"\d{4}-\d{2}-\d{2}"}[group_by]
    if not re.fullmatch(expected, period):
        raise ValueError("Invalid occasion date")
    if group_by != "year":
        _validate_date(f"{period}-01" if group_by == "month" else period, "occasion")
    limit, offset = _page(limit, offset)
    event_date = _occasion_date_expression()
    predicates = [f"strftime(?, {event_date}) = ?"]
    parameters: list[Any] = [{"year": "%Y", "month": "%Y-%m", "day": "%Y-%m-%d"}[group_by], period]
    if date_source == "captured":
        predicates.append(f"{NORMALIZED_CAPTURE_DATE} IS NOT NULL")
    else:
        predicates.append(f"{NORMALIZED_CAPTURE_DATE} IS NULL")
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        f"SELECT {MEDIA_COLUMNS} FROM media_items WHERE {' AND '.join(predicates)} "
        f"ORDER BY COALESCE({NORMALIZED_CAPTURE_DATE}, date(first_scanned_at)) DESC, id DESC LIMIT ? OFFSET ?",
        (*parameters, limit, offset),
    ).fetchall()
    return [_media_record(row) for row in rows]
