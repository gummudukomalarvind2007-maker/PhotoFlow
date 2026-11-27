from __future__ import annotations

import json
import base64
import io
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from PIL import Image


class BackendProcess:
    def __init__(self, executable: Path, data_directory: Path) -> None:
        environment = os.environ.copy()
        environment["PHOTOFLOW_DATA_DIR"] = str(data_directory)
        environment["PATH"] = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
        self.process = subprocess.Popen(
            [str(executable), "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=environment,
        )
        self.responses: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.request_id = 0
        self.reader = threading.Thread(target=self._read_responses, daemon=True)
        self.reader.start()

    def _read_responses(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                self.responses.put(json.loads(line))
            except json.JSONDecodeError:
                self.responses.put({"ok": False, "error": "Backend emitted a non-JSON protocol line"})
        self.responses.put(None)

    def call(self, command: str, **payload: Any) -> dict[str, Any]:
        if self.process.stdin is None:
            raise RuntimeError("Backend stdin is unavailable")
        self.request_id += 1
        request_id = str(self.request_id)
        request = {"id": request_id, "command": command, **payload}
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        response = self.responses.get(timeout=30)
        if response is None:
            raise RuntimeError(f"Backend exited during {command} (code {self.process.poll()})")
        if str(response.get("id")) != request_id or response.get("ok") is not True:
            raise RuntimeError(f"{command} failed: {response.get('error', 'invalid backend response')}")
        return response

    def wait_for_job(self, command: str, job_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = self.call(command, job_id=job_id)["job"]
            if job["status"] != "running":
                if job["status"] != "completed":
                    raise RuntimeError(f"Job {job_id} ended as {job['status']}")
                return job
            time.sleep(0.1)
        raise TimeoutError(f"Job {job_id} did not finish")

    def close(self) -> None:
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=15)
        self.reader.join(timeout=2)
        if self.process.returncode != 0:
            raise RuntimeError(f"Backend stopped with exit code {self.process.returncode}")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: smoke-packaged-backend.py <photoflow-backend.exe>")
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"Packaged backend not found: {executable}")

    with tempfile.TemporaryDirectory(prefix="photoflow-package-smoke-") as temporary:
        root = Path(temporary)
        media = root / "test-media"
        media.mkdir()
        first_photo = media / "memory.jpg"
        Image.new("RGB", (96, 72), (45, 100, 180)).save(first_photo, "JPEG")
        duplicate_photo = media / "memory-copy.jpg"
        shutil.copyfile(first_photo, duplicate_photo)
        video = media / "sample.mp4"
        video.write_bytes(b"temporary scanner integration fixture")
        export_directory = root / "export"
        export_directory.mkdir()
        data_directory = root / "app-data"

        backend = BackendProcess(executable, data_directory)
        try:
            health = backend.call("health")
            health_data = health["database_path"]

            scan = backend.call("scan:start", folders=[str(media)])["scan"]
            deadline = time.monotonic() + 60
            while scan["status"] == "running" and time.monotonic() < deadline:
                time.sleep(0.1)
                scan = backend.call("scan:status", scan_id=scan["id"])["scan"]
            if scan["status"] != "completed" or scan["indexed_files"] != 3:
                raise RuntimeError(f"Media scan did not index the test set: {scan}")

            listed = backend.call("media:list", media_type="all", limit=20, offset=0)["items"]
            if len(listed) != 3:
                raise RuntimeError("Gallery query did not return the scanned test set")
            photos = [item for item in listed if item["media_type"] == "photo"]
            videos = [item for item in listed if item["media_type"] == "video"]
            if len(photos) != 2 or len(videos) != 1:
                raise RuntimeError("Photo/video type filtering returned unexpected items")
            if (photos[0]["width"], photos[0]["height"]) != (96, 72):
                raise RuntimeError("Photo metadata extraction returned unexpected dimensions")
            if photos[0]["metadata_status"] != "partial":
                raise RuntimeError("Photo metadata availability was not reported accurately")
            if len(backend.call("media:list", media_type="all", search="memory")["items"]) != 2:
                raise RuntimeError("Filename search did not return both matching photos")
            thumbnail = backend.call("media:thumbnail", media_id=photos[0]["id"])["data_url"]
            if not thumbnail or not thumbnail.startswith("data:image/"):
                raise RuntimeError("Photo thumbnail generation failed")
            original_bytes = first_photo.read_bytes()
            full_resolution = backend.call("media:full-resolution", media_id=photos[0]["id"])["data_url"]
            decoded = base64.b64decode(full_resolution.split(",", 1)[1])
            with Image.open(io.BytesIO(decoded)) as rendered:
                if rendered.size != (96, 72) or dict(rendered.getexif()):
                    raise RuntimeError("Full-resolution viewing did not preserve pixels and remove EXIF")
            if first_photo.read_bytes() != original_bytes:
                raise RuntimeError("Full-resolution viewing modified original media")

            review = backend.call("photo-review:start", similarity_threshold=6)["job"]
            completed_review = backend.wait_for_job("photo-review:status", review["id"])
            if completed_review["status"] != "completed":
                raise RuntimeError("Photo review did not complete")
            duplicate_groups = backend.call(
                "photo-review:list", category="exact", status="pending", limit=20, offset=0,
                similarity_threshold=6,
            )["result"]["groups"]
            if not any(group["item_count"] == 2 for group in duplicate_groups):
                raise RuntimeError("Exact duplicate review did not find the identical test images")
            low_resolution = backend.call(
                "photo-review:list", category="low_resolution", status="pending", limit=20, offset=0,
                similarity_threshold=6,
            )["result"]["items"]
            if not low_resolution:
                raise RuntimeError("Image quality review did not flag the low-resolution test images")

            retry_media = root / "retry-media"
            retry_media.mkdir()
            failed_photo = retry_media / "retry.jpg"
            failed_photo.write_bytes(b"temporary corrupt image")
            failed_scan = backend.call("scan:start", folders=[str(retry_media)])["scan"]
            deadline = time.monotonic() + 60
            while failed_scan["status"] == "running" and time.monotonic() < deadline:
                time.sleep(0.1)
                failed_scan = backend.call("scan:status", scan_id=failed_scan["id"])["scan"]
            if failed_scan["status"] != "completed" or failed_scan["failed_files"] != 1:
                raise RuntimeError("Packaged scan did not persist the expected corrupt-media failure")
            failed_records = backend.call("retry:failed-list")["scan_failures"]
            if len(failed_records) != 1 or str(retry_media) in str(failed_records):
                raise RuntimeError("Retry Failed exposed an incorrect record or a source path")
            Image.new("RGB", (48, 36), (140, 80, 60)).save(failed_photo, "JPEG")
            retry_scan = backend.call("scan:retry-failed", scan_id=failed_scan["id"])["scan"]
            deadline = time.monotonic() + 60
            while retry_scan["status"] == "running" and time.monotonic() < deadline:
                time.sleep(0.1)
                retry_scan = backend.call("scan:status", scan_id=retry_scan["id"])["scan"]
            if retry_scan["status"] != "completed" or retry_scan["indexed_files"] != 1:
                raise RuntimeError("Retry Failed did not safely reprocess the corrupt-media fixture")
            if backend.call("retry:failed-list")["scan_failures"]:
                raise RuntimeError("Successfully retried scan failure was not cleared")

            album = backend.call("albums:create", name="Smoke test")["album"]
            backend.call("albums:add-media", album_id=album["id"], media_ids=[photos[0]["id"], videos[0]["id"]])
            album_items = backend.call("albums:media", album_id=album["id"], limit=20, offset=0)["items"]
            if len(album_items) != 2:
                raise RuntimeError("Album membership was not persisted")
            if not backend.call("occasions:list", group_by="day", limit=20, offset=0)["occasions"]:
                raise RuntimeError("Occasion listing returned no indexed-date group")

            media_export = backend.call(
                "export:media", mode="selected", destination=str(export_directory),
                media_ids=[photos[0]["id"], videos[0]["id"]], organize_by_type=True,
            )["job"]
            backend.wait_for_job("transfer:status", media_export["id"])
            exported_photo = export_directory / "Photos" / photos[0]["filename"]
            if not exported_photo.is_file():
                raise RuntimeError("Photo export did not create its destination file")
            if exported_photo.read_bytes() != (media / photos[0]["filename"]).read_bytes():
                raise RuntimeError("Photo export changed the original file bytes")
            exported_video = export_directory / "Videos" / videos[0]["filename"]
            if exported_video.read_bytes() != video.read_bytes():
                raise RuntimeError("Video export changed the original file bytes")

            metadata_file = root / "library.csv"
            metadata_export = backend.call(
                "export:metadata", destination=str(metadata_file), format="csv"
            )["job"]
            backend.wait_for_job("transfer:status", metadata_export["id"])
            metadata_text = metadata_file.read_text(encoding="utf-8")
            if "source_path" in metadata_text or "gps_" in metadata_text:
                raise RuntimeError("Metadata export included a private field")

            backend.call("settings:set-scan-preference", skip_unchanged_files=False)
            backup = root / "library-backup.db"
            backup_job = backend.call("backup:create", destination=str(backup))["job"]
            backend.wait_for_job("transfer:status", backup_job["id"])
            restore_job = backend.call("backup:restore", source=str(backup))["job"]
            backend.wait_for_job("transfer:status", restore_job["id"])
            safety_backups = list((data_directory / "backups").glob("pre-restore-*.db"))
            if len(safety_backups) != 1:
                raise RuntimeError("Database restore did not create its safety backup")
        finally:
            backend.close()

        restarted = BackendProcess(executable, data_directory)
        try:
            restarted_health = restarted.call("health")
            if restarted_health["database_path"] != health_data:
                raise RuntimeError("Database location changed after restart")
            persisted = restarted.call("settings:overview")["settings"]
            if persisted["skip_unchanged_files"] is not False:
                raise RuntimeError("Application preference did not persist across restart/restore")
            if persisted["thumbnail_cache_files"] < 1:
                raise RuntimeError("Thumbnail cache did not persist across backend restart")
            if len(restarted.call("albums:list")["albums"]) != 1:
                raise RuntimeError("Album data did not persist across restart/restore")
            restored_media = restarted.call("media:list", media_type="all", limit=20, offset=0)["items"]
            if len(restored_media) != 4:
                raise RuntimeError("Indexed media did not persist across restart/restore")
            if not restarted.call("media:thumbnail", media_id=restored_media[0]["id"])["data_url"]:
                raise RuntimeError("Thumbnail cache did not survive backend restart")
            history = restarted.call("scan:history")["scans"]
            if len(history) < 3 or any("folders" in item for item in history):
                raise RuntimeError("Scan history did not persist safely across restart")
        finally:
            restarted.close()

    print("Packaged backend workflow smoke test passed (temporary media, database, cache, exports, and backup).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
