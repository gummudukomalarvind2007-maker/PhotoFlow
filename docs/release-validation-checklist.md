# PhotoFlow release validation checklist

Use a disposable Windows account or a dedicated test data directory and fixture media. Never use a personal photo library for removal, restore, cache-clearing, crash, or uninstall tests. Record results for the exact release candidate; automated backend coverage does not substitute for native-dialog, Electron-window, installer, or uninstall checks.

See [data-safety-and-recovery.md](./data-safety-and-recovery.md) for storage paths, cleanup boundaries, crash behavior, and safe practices.

## Release record

- Candidate version / commit:
- Windows edition and build:
- Architecture:
- Tester and date:
- Test data directory:
- Application data directory:
- Installer SHA-256 (if tested):

## Automated validation evidence

Current run: Windows 11 x64 (build 10.0.26300), 2026-11-26. Tests used temporary directories and disposable databases/media. No personal media, database, or backup was used.

- [x] `PYTHONPATH=services\backend python -m unittest discover -s services\backend\tests -v` — 81 passed; 3 symlink tests skipped because symlinks are unavailable in this environment.
- [x] `npm test` — 25 passed across 7 test files.
- [x] `npm run typecheck` — passed.
- [x] `npm run build` — passed; production Electron main, preload, and renderer bundles generated.
- [x] `npm run build:win` — passed on Windows; PyInstaller 6.16.0 rebuilt the backend from current source with Python 3.12.2, ran the packaged workflow smoke successfully, then built the x64 NSIS installer.
- [x] SHA-256 comparison confirmed `services\backend\distribution\photoflow-backend.exe` and `apps\desktop\release\win-unpacked\resources\backend\photoflow-backend.exe` match: `471C6AAE36F0B044F4B8C1B239E39C54D2365A7A32707FCF9C0F5C8401B6642A`.
- Installer artifact: `apps\desktop\release\PhotoFlow-0.1.0-Windows-x64.exe` (138,888,856 bytes), SHA-256 `34BE3DC1C954044D667D783C9AEE868A89CFFC9C5C861F1C4FFDD10EC96D4EEA`.
- NSIS install/uninstall was not run. No code-signing certificate was configured; Windows trust/SmartScreen behavior is unverified.

## End-user workflow checks

Automated coverage below describes existing tests; it does not mean the interactive workflow was manually verified. Complete the manual column on the exact candidate build.

| # | Workflow and manual acceptance criteria | Existing automated evidence | Manual result |
|---|---|---|---|
| 1 | **First launch and initial setup** — Launch a clean per-user install; confirm the dashboard loads, backend health is shown accurately, and the app creates state only in its application-data directory. Confirm a backend-start failure is understandable and recoverable. | Main-process backend startup tests; backend health smoke coverage. No Electron first-launch UI test. | ☐ Pass ☐ Fail ☐ Not run |
| 2 | **Folder selection and scanning** — Use the native folder picker on a disposable mixed-media tree, cancel it once, then scan it. Confirm progress, nested-folder discovery, unsupported-file handling, and no source changes. | Scanner unit tests and packaged-backend scan smoke. Native picker not covered. | ☐ Pass ☐ Fail ☐ Not run |
| 3 | **Photo and video browsing** — Confirm photo/video cards, metadata labels, pagination, photo viewing, and video thumbnails/posters with and without FFmpeg. | Backend listing/thumbnail tests and packaged-backend smoke. Renderer browsing is not end-to-end tested. | ☐ Pass ☐ Fail ☐ Not run |
| 4 | **Search, sorting, and filters** — Exercise text, format, date, camera, dimensions, resolution, duration, and size filters; combine filters and verify sorting/pagination. | Backend filter/sort/pagination tests; packaged smoke checks filename search. Interactive combinations not covered. | ☐ Pass ☐ Fail ☐ Not run |
| 5 | **Select All and Clear Selection** — Select all filtered matches across pages, exclude/reselect individual items, clear the selection, and verify counts and enabled actions. | Media-selection unit tests cover all-matching selections, exclusions, scope changes, and clearing. No rendered interaction test. | ☐ Pass ☐ Fail ☐ Not run |
| 6 | **Remove from Library without deleting originals** — On disposable files, remove selected and filtered records. Confirm originals still exist byte-for-byte, shared thumbnails are retained, and album/occasion counts update. | Backend media-removal tests verify database/cache cleanup and preserved source files. Confirmation dialog is not tested interactively. | ☐ Pass ☐ Fail ☐ Not run |
| 7 | **Thumbnail and metadata generation** — Confirm image metadata and thumbnails are generated; confirm unavailable/failed metadata is labeled correctly. Test missing/corrupt sources and optional video tools. | Scanner, metadata-status, thumbnail, and packaged-backend smoke tests. | ☐ Pass ☐ Fail ☐ Not run |
| 8 | **Duplicate detection and Quality Review** — Analyze fixture duplicates and distinct similar images; verify review categories, marking items, cancellation, and retry behavior. Confirm no automatic deletion or source edits. | Photo-review tests and packaged-backend smoke cover exact duplicates, low resolution, and analysis. Manual review controls remain unverified. | ☐ Pass ☐ Fail ☐ Not run |
| 9 | **Albums and Occasions** — Create/edit/delete an album, add/remove media, export an album, and browse year/month/day occasions with capture-date and indexed-date groups. | Backend album/occasion tests and packaged-backend smoke. Renderer interactions are not end-to-end tested. | ☐ Pass ☐ Fail ☐ Not run |
| 10 | **Export and backup/restore** — Export selected, filtered, album, and metadata records to disposable destinations. Validate backup; restore it; test invalid backup and confirm the current library remains intact. | Backend and packaged smoke cover exports, backup/restore, invalid restore, restore safety copies, failed backup/restore, and preservation of an existing safety-backup destination. Interrupted-copy process test confirms no partial final output and unchanged source bytes. Native dialogs not covered. | ☐ Pass ☐ Fail ☐ Not run |
| 11 | **Scan cancellation and recovery** — Cancel a long fixture scan; terminate the app during a scan in a disposable data directory; relaunch and resume/retry. Confirm already indexed rows are not duplicated. | Scanner tests cover cancellation, interruption recovery, rescan, and worker-start failure. Transfer progress is saved and recovered as interrupted, but operations are not automatically resumed. A hard-termination export test confirms no partial final destination and unchanged source bytes, but leaves a hidden staging file in the selected destination; app startup cleanup is limited to app-owned directories. | ☐ Pass ☐ Fail ☐ Not run |
| 12 | **Application restart and database persistence** — Restart normally and after a controlled interruption; verify media, albums, settings, and thumbnails persist and the database opens cleanly. | Backend resource/recovery tests and packaged smoke restart with its temporary database. Electron restart UI is unverified. | ☐ Pass ☐ Fail ☐ Not run |
| 13 | **Multiple application launches** — Launch PhotoFlow twice; verify the second launch focuses the first instance and does not create concurrent writers or duplicate scans. | Single-instance lock/focus unit tests only; actual process/window behavior is unverified. | ☐ Pass ☐ Fail ☐ Not run |
| 14 | **Windows installer and uninstall behavior** — On a clean disposable Windows profile, install per-user, launch from shortcuts, upgrade without losing app data, uninstall, and confirm the documented app-data retention and source-media integrity. | x64 NSIS installer built successfully. Static configuration review confirms per-user install, configurable location, shortcuts, and `deleteAppDataOnUninstall: false`. Install, upgrade, shortcuts at runtime, signing/SmartScreen, and uninstall were not tested. | ☐ Pass ☐ Fail ☐ Not run |
| 15 | **Large-library performance** — Use a disposable 10,000+ item library; scan, page, search, sort, filter, and open items while watching responsiveness, memory, and database growth. Compare repeated operations and check that cancelling a long scan remains responsive. | Performance tests insert 12,000 synthetic database records and verify complete pagination and expected query indexes. They do not measure real-media scanning, UI responsiveness, memory, or end-to-end Windows performance. | ☐ Pass ☐ Fail ☐ Not run |

## Release decision

- [ ] Ready
- [ ] Ready with limitations
- [x] Not ready

Blocking failures and follow-up owner:

- The installer has not been installed/upgraded/uninstalled in a disposable Windows profile. Install launch, shortcuts, retained data, and uninstall safety therefore remain unverified.
- Multiple-instance window focus and large-library interactive performance have automated coverage or static evidence only; they still require manual validation on the release machine.
- The installer is unsigned; Windows trust/SmartScreen behavior is unverified.

Known limitations accepted for this release:

- A forced termination can leave a hidden staging file in an arbitrary export destination. The final filename is not published until complete, and original media is unchanged.
- Transfer progress is persisted periodically, but interrupted exports/backups/restores are not resumed automatically.
