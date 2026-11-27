from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any


MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif",
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
}
MEDIA_SORTS = {"newest", "oldest", "filename-asc", "filename-desc", "size-desc", "size-asc"}
TEXT_LIMITS = {"search": 200, "camera_make": 100, "camera_model": 100}
INTEGER_FILTER_LIMITS = {
    "min_width": (1, 100_000),
    "max_width": (1, 100_000),
    "min_height": (1, 100_000),
    "max_height": (1, 100_000),
    "min_resolution_px": (1, 10_000_000_000),
    "max_resolution_px": (1, 10_000_000_000),
    "min_size_bytes": (0, 9_007_199_254_740_991),
    "max_size_bytes": (0, 9_007_199_254_740_991),
}
DURATION_FILTER_LIMITS = {
    "min_duration_seconds": (0, 10_000_000),
    "max_duration_seconds": (0, 10_000_000),
}
FILTER_PAIRS = (
    ("min_width", "max_width"),
    ("min_height", "max_height"),
    ("min_resolution_px", "max_resolution_px"),
    ("min_duration_seconds", "max_duration_seconds"),
    ("min_size_bytes", "max_size_bytes"),
)
MEDIA_FILTER_KEYS = frozenset({
    *TEXT_LIMITS, "extension", "date_from", "date_to", "sort",
    *INTEGER_FILTER_LIMITS, *DURATION_FILTER_LIMITS,
})


def normalize_media_filters(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Invalid media filters")
    if set(value) - MEDIA_FILTER_KEYS:
        raise ValueError("Invalid media filters")

    normalized: dict[str, Any] = {}
    for key, maximum in TEXT_LIMITS.items():
        text = value.get(key, "")
        if text is not None and (not isinstance(text, str) or len(text) > maximum):
            raise ValueError(f"Invalid {key} filter")
        normalized[key] = (text or "").strip()

    extension = value.get("extension")
    if extension not in (None, ""):
        if not isinstance(extension, str) or extension.lower() not in MEDIA_EXTENSIONS:
            raise ValueError("Unsupported media extension")
        normalized["extension"] = extension.lower()
    else:
        normalized["extension"] = None

    for key in ("date_from", "date_to"):
        text = value.get(key)
        if text in (None, ""):
            normalized[key] = None
        elif not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError(f"{key} must be a date in YYYY-MM-DD format")
        else:
            try:
                normalized[key] = datetime.strptime(text, "%Y-%m-%d").date().isoformat()
            except ValueError as error:
                raise ValueError(f"{key} must be a valid date in YYYY-MM-DD format") from error
    if normalized["date_from"] and normalized["date_to"] and normalized["date_from"] > normalized["date_to"]:
        raise ValueError("date_from must not be later than date_to")

    sort = value.get("sort", "newest")
    if not isinstance(sort, str) or sort not in MEDIA_SORTS:
        raise ValueError("Unsupported media sort order")
    normalized["sort"] = sort

    for key, (minimum, maximum) in INTEGER_FILTER_LIMITS.items():
        number = value.get(key)
        if number in (None, ""):
            continue
        if isinstance(number, bool) or not isinstance(number, int) or not minimum <= number <= maximum:
            raise ValueError(f"Invalid {key} filter")
        normalized[key] = number

    for key, (minimum, maximum) in DURATION_FILTER_LIMITS.items():
        number = value.get(key)
        if number in (None, ""):
            continue
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"Invalid {key} filter")
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f"Invalid {key} filter")
        normalized[key] = float(number)

    for minimum_key, maximum_key in FILTER_PAIRS:
        if minimum_key in normalized and maximum_key in normalized:
            if normalized[minimum_key] > normalized[maximum_key]:
                raise ValueError(f"{minimum_key} must not be greater than {maximum_key}")
    return normalized


def media_filter_arguments(filters: dict[str, Any], *, include_sort: bool = True) -> dict[str, Any]:
    numeric_filters = {
        key: value
        for key in (*INTEGER_FILTER_LIMITS, *DURATION_FILTER_LIMITS)
        if (value := filters.get(key)) is not None
    }
    arguments: dict[str, Any] = {
        "search": filters["search"],
        "extension": filters["extension"],
        "date_from": filters["date_from"],
        "date_to": filters["date_to"],
        "camera_make": filters["camera_make"],
        "camera_model": filters["camera_model"],
        "numeric_filters": numeric_filters,
    }
    if include_sort:
        arguments["sort"] = filters["sort"]
    return arguments
