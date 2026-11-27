# PhotoFlow

An offline-first Windows desktop application for private photo and video management. It uses Electron, React, TypeScript, a Python JSON Lines backend, and SQLite. The Windows release packages the backend so end users do not need to install Python. PhotoFlow never modifies original media.

## Development setup

### Required tools

- Node.js 22 or newer (Node 24 is supported)
- npm 10 or newer
- **Python 3.12** available as `python` (or set `PHOTOFLOW_PYTHON` to its executable path)
- Windows PowerShell for Windows packaging
- FFmpeg and FFprobe are optional. Install them separately or place licensed Windows builds in `services/backend/bin` as described below.

Python is required for the local backend and its health check. SQLite ships with CPython; no database server is required. Photo scanning uses Pillow for local image metadata and thumbnail generation.

## Install and run

```powershell
npm install
npm run backend:health
```

Python is used by development runs and backend tests. SQLite is included with CPython; no database server is required. Install the backend's local image dependency once:

```powershell
python -m pip install -r services/backend/requirements.txt
npm run backend:health
npm run dev
```

The health command writes PhotoFlow-owned SQLite state to `%LOCALAPPDATA%\\PhotoFlow\\photoflow.db`. To use a non-default interpreter for the current PowerShell session:

```powershell
$env:PHOTOFLOW_PYTHON = 'C:\\Path\\To\\Python312\\python.exe'
npm run backend:health
npm run dev
```

Other commands:

```powershell
npm run typecheck
npm test
npm run build
npm start
```

`npm run build` creates the Electron main, preload, and renderer bundles. `npm start` previews the Electron build; it still uses the development Python backend. Backend tests:

```powershell
$env:PYTHONPATH = 'services\backend'
python -m unittest discover -s services\backend\tests -v
```

## Windows release build and installation

Build the per-user Windows x64 NSIS installer from Windows:

```powershell
npm install
npm run build:win
```

The build requires Node.js 22+, npm 10+, Python 3.12, an internet connection for the declared npm/Python build packages, and Windows x64. `build:win` bundles the renderer and Electron main/preload code, creates an isolated Python build environment, packages the backend with PyInstaller, runs the packaged-backend smoke workflow, and builds the installer. It does not download FFmpeg or FFprobe. The installer is written to `apps/desktop/release/PhotoFlow-0.1.0-Windows-x64.exe`; install it per user and use its Start Menu or optional desktop shortcut. The installer allows choosing its program directory.

The installer contains the PhotoFlow backend executable and Python dependencies, including Pillow; SQLite comes from the embedded CPython runtime. SQL migration files are included in the executable. The installed app launches this backend directly from its Electron resources and speaks the same JSON Lines protocol. Python is not required on users' computers. Development runs continue to launch `services/backend/run_backend.py` using `python` or `PHOTOFLOW_PYTHON`.

The installer is currently unsigned. Windows SmartScreen may show an unknown-publisher warning; verify the installer source before proceeding. The build targets x64 Windows only.

## FFmpeg and FFprobe

FFmpeg and FFprobe are optional external tools; PhotoFlow does not fetch them. In development, install them yourself and put `ffmpeg.exe` and `ffprobe.exe` on `PATH`. To include them in a release, place legally distributable Windows x64 binaries at:

```text
services/backend/bin/ffmpeg.exe
services/backend/bin/ffprobe.exe
```

The packaging script copies only those explicitly provided files into the app's backend resources and adds that directory to the backend process `PATH`. Review and comply with the binaries' license and distribution requirements. Without them, scanning and photo management continue to work; video metadata and poster thumbnails report unavailable where appropriate.

## Application data, upgrades, and backups

- Database, thumbnails, and automatic pre-restore safety backups: `%LOCALAPPDATA%\PhotoFlow\` (`photoflow.db`, `thumbnails\`, and `backups\`).
- Application logs: `%APPDATA%\PhotoFlow\logs\photoflow.log`.
- Scan preferences are stored in the SQLite database.

The application installer and uninstaller operate on program files, not these data directories. Upgrading preserves the existing database and cache. Uninstalling does not remove the library database, cache, source media, or safety backups. Keep independent backups of important libraries. Create a database backup from Export & Backup; restore from there to replace the indexed library. Restore validates the backup, creates a safety copy first, and never modifies source media.

For isolated packaged-backend verification, `scripts/smoke-packaged-backend.py` creates temporary media and app data, exercises scanning, search, thumbnails, review, albums, occasions, exports, backup/restore, and restart persistence, then removes its temporary directory. It does not scan personal folders.

## Known limitations

- PhotoFlow indexes media and stores its own metadata; originals remain at their existing paths.
- HEIC/HEIF support depends on Pillow's available decoder.
- FFmpeg/FFprobe are optional and are not bundled unless the developer supplies licensed binaries.
- Gallery cards use lazy thumbnails and off-screen content visibility, but loaded pages remain in the DOM; very long browsing sessions can still grow renderer memory. All-media date sorts, filename/size sorts, and substring search still have SQLite work proportional to the result set.
- Exact-duplicate review returns every item in each paginated group, so an unusually large duplicate group can still create a large result and DOM subtree.
- The packaged workflow smoke test exercises the frozen backend, not an interactive NSIS installation or every UI action. The installer must still be verified on a clean Windows x64 machine before broad distribution.
- `npm audit` currently reports moderate advisories in transitive, development-only packaging glob dependencies; `npm audit --omit=dev` reports no production dependency advisories. Recheck and update the packaging toolchain before public releases.
- The app currently offers the dark theme only.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `apps/desktop` | Electron + React + TypeScript + Vite desktop client. |
| `apps/desktop/src/main` | Privileged Electron process: window lifecycle, logging, constrained IPC, and Python process manager. |
| `apps/desktop/src/preload` | The only renderer bridge. It offers constrained version, health, scanner, library, album, occasion, and thumbnail calls—not Node or filesystem access. |
| `apps/desktop/src/renderer` | React UI, with no direct filesystem or backend process access. |
| `services/backend` | CPython 3.12 local service using JSON Lines over standard input/output. |
| `services/backend/photoflow_backend/migrations` | Ordered, transactional SQLite migration files. |
| `packages/shared` | Future shared contracts, kept separate from UI and privileged code. |

## Security and lifecycle

The Electron window keeps `contextIsolation: true`, `nodeIntegration: false`, and sandboxing enabled. The preload script exposes a deliberately narrow, typed IPC surface. The main process owns the native folder picker and Python child process; the renderer never receives arbitrary filesystem access. The backend client shares concurrent startup, rejects in-flight calls on process or protocol failures, and closes stdin and waits for process exit before Electron quits. Backend errors are logged locally, with local filesystem paths redacted from renderer-facing errors. Logs are written to `%APPDATA%\\PhotoFlow\\logs\\photoflow.log`.

The Python backend communicates through JSON Lines commands for health, scans, library overview, media pages, and thumbnails. It opens only PhotoFlow's own SQLite database. Scan paths are accepted only from the native folder picker; source folder paths and media metadata remain backend-private.

## Media scanning (Phases 3 and 5)

Use **Add media** in the app to choose one or more folders with the native Windows directory picker. PhotoFlow scans supported files recursively and reads source media only; it never moves, renames, edits, or deletes originals. Records live in SQLite and previews are written under `%LOCALAPPDATA%\\PhotoFlow\\thumbnails`.

Photos: JPG/JPEG, PNG, WebP, BMP, TIFF, HEIC/HEIF when Pillow has a compatible decoder. Videos: MP4, MOV, AVI, MKV, and WebM. Video duration/resolution and poster frames are collected only when local `ffprobe`/`ffmpeg` are available on `PATH`; media remains indexed if those tools are absent. The gallery reports metadata as extracted, partial, unavailable, or failed and never labels absent fields as pending. Opened full-resolution photos are reoriented and re-encoded without EXIF; originals remain unchanged.

Scans and photo-analysis jobs interrupted by application shutdown are marked recoverable at the next startup. If a worker exits without finalizing its job, the next status check marks it interrupted for recovery. Resume re-scans the selected folders and skips unchanged indexed files; retrying analysis processes pending images. If a background worker cannot start, its job is marked failed and its active state is released. Photos and Videos support case-insensitive search across indexed names, formats, and camera metadata, with format/date/camera filters, six sort modes, and stable pages of 60 items. Query filters are applied in SQLite before pagination.

Gallery and collection previews load as cards approach the viewport. The renderer reuses a bounded 120-thumbnail cache, while gallery pages fetch display fields only. SQLite migrations add indexes for capture-date sorting within photo/video galleries. Off-screen cards use browser content visibility; loading more pages still retains their media records.

The Retry Failed workspace lists unresolved scan-file and photo-analysis failures from SQLite. Retrying a scan rechecks its original selected folders while skipping unchanged successful media; retrying analysis targets only failed analysis records. The dashboard history and retry badge use persisted records rather than sample events or fixed counts.

Run backend scanning tests with:

```powershell
Set-Location services\backend
python -m unittest discover -s tests -v
```

## Photo review (Phase 7)

Quality Review runs cached SHA-256 grouping and Pillow-based image analysis on a background worker. It stores perceptual hashes, blur/exposure/resolution measurements, analysis progress, and review status in the local SQLite database. Similar-photo thresholds are configurable; review lists expose only filenames and containing-folder names, never full source paths. Analysis is read-only and never deletes or changes original media.

## Albums and occasions (Phase 8)

Manual albums and their media memberships are stored in SQLite. Album deletion and removing items only affect PhotoFlow's records; original media remain in place. Photos and Videos allow selecting individual items or all items matching the current gallery filters across every page. Bulk removal asks for confirmation, deletes the selected database records and unshared app-owned thumbnails, and leaves original media untouched; album memberships cascade and occasion groups update from the remaining records. Smart albums are generated from indexed capture dates, camera metadata, media type, recent indexing dates, and videos, so they reflect library changes without stored duplicate collections.

Occasions group actual capture dates by year, month, or day and can be filtered to a configurable date range. Items without usable capture metadata are grouped separately by their PhotoFlow indexing date and labeled as such. Album and occasion responses expose display metadata and thumbnail IDs only; full media paths remain private to the backend.

## Smart Collections and search (Phase 14)

Smart Collections are computed from SQLite metadata and update when matching records are added or removed. They include Recently Added and Recently Viewed (30 days), Photos by Year and Month, filename-prefix screenshot matches, portrait and landscape photos, photos at least 12 megapixels, files at least 100 MiB, groups of photos with matching dimensions, and videos in under-1-minute, 1–5-minute, 5–20-minute, and over-20-minute duration bands. Recently Viewed records a photo after PhotoFlow successfully opens its full-resolution view. Screenshot matching recognizes common screenshot filename prefixes only; renamed screenshots may not be recognized. Unknown or absent metadata is not inferred.

The Photos and Videos search filters can be combined for text, media type, extension, capture-date range, camera make/model, dimensions, resolution, video duration, file size, and sorting. Filtering and pagination run in SQLite. Save search stores the validated filter conditions as a Smart Album; its results remain live, and deleting the saved album removes only its filter definition.

Migration 008 adds the local photo-view timestamp, saved Smart Album definitions, and indexes for photo dimensions/resolution and video duration. It is additive and preserves existing media and album records. Future image-understanding, semantic-search, face-grouping, and duplicate-recommendation interfaces are provider protocols only: PhotoFlow does not currently run AI processing or send media to external services. Cloud processing consent defaults to off, and face grouping defaults to disabled.

## Export, backup, and settings (Phase 9)

Media exports copy source bytes to a destination chosen with the native folder picker. Existing destination filenames are retained and collisions receive a numbered suffix; originals are never overwritten or modified. Selected media, filtered library results, and manual albums can be exported, optionally organized into Photos and Videos folders. CSV and JSON metadata exports omit internal paths and GPS fields.

Database backups use SQLite's online backup API so committed WAL data is included. Restores validate the backup and schema, create a pre-restore safety backup, build and verify a temporary database, then atomically replace only PhotoFlow's database. Thumbnail-cache clearing is confined to the app-owned cache directory; missing previews can be regenerated when requested. Settings show local storage locations, library/cache totals, backend and FFmpeg tool availability, and a persisted preference for reprocessing unchanged files on future scans.
