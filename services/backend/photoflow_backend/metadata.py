from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from photoflow_backend.database import thumbnails_dir

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def thumbnail_cache_file(content_hash: str) -> Path:
    if not re.fullmatch(r"[a-fA-F0-9]{64}", content_hash):
        raise ValueError("Invalid thumbnail cache identifier")
    return thumbnails_dir() / f"{content_hash}.jpg"


def is_cached_thumbnail(path: str | None) -> bool:
    """True only for an existing file inside PhotoFlow's thumbnail directory."""
    if not path:
        return False
    try:
        candidate = Path(path).resolve()
        candidate.relative_to(thumbnails_dir().resolve())
    except (OSError, ValueError):
        return False
    return candidate.is_file()


def ensure_cached_thumbnail(path: Path, media_type: str, content_hash: str) -> str | None:
    """Write a preview into the cache. Source media is never an output path."""
    if path.is_symlink():
        return None
    output = thumbnail_cache_file(content_hash)
    if output.is_symlink():
        return None
    if output.is_file():
        return str(output)
    if media_type == "photo":
        return _write_image_thumbnail(path, output)
    return _write_video_poster(path, output)


def _write_image_thumbnail(path: Path, output: Path) -> str | None:
    try:
        from PIL import Image, ImageOps  # type: ignore[import-not-found]
    except ImportError:
        return None
    with Image.open(path) as image:
        try:
            image.draft("RGB", (640, 640))
        except (AttributeError, ValueError):
            pass
        preview = ImageOps.exif_transpose(image).convert("RGB")
        preview.thumbnail((640, 640))
        preview.save(output, "JPEG", quality=84, optimize=True)
    return str(output)


def _write_video_poster(path: Path, output: Path) -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "00:00:01", "-i", str(path), "-frames:v", "1", "-vf", "scale='min(640,iw)':-2", "-y", str(output)],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    return str(output) if output.is_file() else None


def classify(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in PHOTO_EXTENSIONS:
        return "photo"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fraction(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _gps_value(values: Any, reference: str) -> float | None:
    if not values or len(values) != 3:
        return None
    parts = [_fraction(value) for value in values]
    if any(part is None for part in parts):
        return None
    result = parts[0] + parts[1] / 60 + parts[2] / 3600  # type: ignore[operator]
    return -result if reference in {"S", "W"} else result


def _image_metadata(path: Path, content_hash: str) -> dict[str, Any]:
    record: dict[str, Any] = {"metadata_provenance": {"dimensions": "Pillow", "exif": "Pillow"}}
    try:
        from PIL import Image, ImageOps  # type: ignore[import-not-found]
    except ImportError as error:
        return {"metadata_provenance": {"dimensions": "unavailable", "reason": "Pillow is not installed"}, "metadata_json": {"warning": str(error)}}

    with Image.open(path) as image:
        exif = image.getexif()
        oriented = ImageOps.exif_transpose(image)
        record["width"], record["height"] = oriented.size
        capture_date = exif.get(36867) or exif.get(306)
        record["capture_date"] = str(capture_date) if capture_date else None
        record["camera_make"] = str(exif.get(271)) if exif.get(271) else None
        record["camera_model"] = str(exif.get(272)) if exif.get(272) else None
        gps = exif.get_ifd(34853) if hasattr(exif, "get_ifd") else {}
        record["gps_latitude"] = _gps_value(gps.get(2), str(gps.get(1, "")))
        record["gps_longitude"] = _gps_value(gps.get(4), str(gps.get(3, "")))
        record["metadata_json"] = {"format": image.format, "exif_present": bool(exif)}
        record["metadata_status"] = _metadata_status(
            (record.get(field) for field in ("width", "height", "capture_date", "camera_make", "camera_model")),
            5,
        )
        output = thumbnail_cache_file(content_hash)
        if not output.exists():
            preview = oriented.convert("RGB")
            preview.thumbnail((640, 640))
            preview.save(output, "JPEG", quality=84, optimize=True)
        record["thumbnail_path"] = str(output)
    return record


def _parse_rate(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        numerator, denominator = value.split("/", 1)
        return round(float(numerator) / float(denominator), 3) if float(denominator) else None
    except (ValueError, ZeroDivisionError):
        return None


def _video_metadata(path: Path, content_hash: str) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {
            "metadata_provenance": {"video": "unavailable", "reason": "ffprobe was not found"},
            "metadata_json": {"warning": "Install FFmpeg to read video metadata and posters."},
            "metadata_status": "unavailable",
        }
    command = [ffprobe, "-v", "error", "-show_entries", "format=duration:format_tags=creation_time:stream=codec_type,width,height,avg_frame_rate,r_frame_rate:stream_tags=creation_time", "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe could not inspect this video")
    payload = json.loads(result.stdout)
    stream = next((item for item in payload.get("streams", []) if item.get("codec_type") == "video"), {})
    tags = stream.get("tags", {}) | payload.get("format", {}).get("tags", {})
    record: dict[str, Any] = {
        "width": stream.get("width"), "height": stream.get("height"),
        "duration_seconds": _fraction(payload.get("format", {}).get("duration")),
        "frame_rate": _parse_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
        "capture_date": tags.get("creation_time"),
        "metadata_json": {"format": payload.get("format", {}).get("format_name")},
        "metadata_provenance": {"video": "ffprobe"},
    }
    record["metadata_status"] = _metadata_status(
        (record.get(field) for field in ("width", "height", "capture_date")),
        3,
    )
    output = thumbnail_cache_file(content_hash)
    if not output.exists():
        _write_video_poster(path, output)
    if output.exists():
        record["thumbnail_path"] = str(output)
        record["metadata_provenance"]["poster"] = "ffmpeg"
    return record


def inspect_media(path: Path, media_type: str, content_hash: str) -> dict[str, Any]:
    stat = path.stat()
    identity = f"{stat.st_dev}:{stat.st_ino}" if getattr(stat, "st_ino", 0) else None
    record: dict[str, Any] = {
        "source_path": str(path), "file_identity": identity, "content_hash": content_hash,
        "media_type": media_type, "extension": path.suffix.lower(), "filename": path.name,
        "size_bytes": stat.st_size, "modified_time_ns": stat.st_mtime_ns,
        "metadata_json": {}, "metadata_provenance": {"file": "os.stat"},
        "metadata_status": "unavailable",
    }
    extra = _image_metadata(path, content_hash) if media_type == "photo" else _video_metadata(path, content_hash)
    record.update(extra)
    record["metadata_json"] = json.dumps(record.get("metadata_json", {}))
    record["metadata_provenance"] = json.dumps(record.get("metadata_provenance", {}))
    return record


def _metadata_status(values: Any, expected_fields: int) -> str:
    available = sum(value is not None and value != "" for value in values)
    if available == expected_fields:
        return "extracted"
    if available:
        return "partial"
    return "unavailable"
