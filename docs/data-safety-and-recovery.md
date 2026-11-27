# PhotoFlow data safety and recovery

This guide describes application-owned state and recovery behavior. PhotoFlow's library database and thumbnail cache are separate from the original photos and videos selected for indexing.

## Storage locations

- **Library database:** `%LOCALAPPDATA%\PhotoFlow\photoflow.db` on Windows by default. If `PHOTOFLOW_DATA_DIR` is set in the environment inherited by the backend, that directory is used instead. SQLite may also create `photoflow.db-wal` and `photoflow.db-shm` while the database is open.
- **Thumbnail cache:** the `thumbnails` directory under the same application data directory. Clearing the thumbnail cache does not delete original media.
- **Restore safety backups:** `backups\pre-restore-*.db` under the application data directory. PhotoFlow creates one before replacing the current database during restore. User-created backups are saved to the destination selected in the export dialog; keep those somewhere independent of the application data directory.
- **Transfer recovery state:** `transfer-jobs.json` under the application data directory. It records recent operation status and progress, not filesystem paths or media contents. Updates are atomically replaced; progress is written periodically and at most the 20 most recent operations are retained.
- **Application logs:** `logs\photoflow.log` under Electron's `app.getPath('userData')` directory. On a typical Windows account this is under `%APPDATA%\PhotoFlow\logs`. The exact path can depend on the Electron environment. Logs are appended and are not automatically rotated or removed.
- **Original media:** stays in the folders chosen by the user. The database stores library records and source paths; thumbnail generation and export open source files for reading. Removing an item from the library does not delete the original.

The backend reports the database path in its health response, and the Electron log records that path when health is checked.

## Temporary files and cleanup

- Media, metadata, and user-requested database exports are staged as `.photoflow-transfer-<id>.tmp` in the selected destination folder. PhotoFlow publishes the final file only after it has finished writing and validating the staged file.
- Restore work is staged as `.photoflow-restore-<id>.tmp` in the application data directory. The pre-restore safety backup is staged in the application `backups` folder before it is committed.
- Transfer-state writes use temporary files in the application data directory and replace `transfer-jobs.json` atomically.
- A normal cancellation or handled failure removes that operation's staging files. At backend startup, PhotoFlow removes stale restore/transfer staging files only from its application data directory and its own `backups` folder. It does not search arbitrary export destinations.
- Logs, the library database, user-created backups, and thumbnails are not removed automatically at startup or uninstall. The user can explicitly clear the thumbnail cache from the application.

Do not manually remove a `.photoflow-transfer-*.tmp` file while its destination folder is part of an active operation. After a forced termination, first confirm PhotoFlow is closed and no transfer is active before removing a leftover hidden staging file from a user-selected destination.

## Shutdown, crashes, and recovery

- During normal shutdown, Electron closes the backend input stream. The backend requests cancellation of its active transfer and waits briefly for the transfer worker to stop; Electron also has a bounded wait before terminating an unresponsive backend.
- A scan commits indexed records incrementally. On backend startup, scans left running are marked interrupted and can be resumed or retried. Photo-analysis job state is persisted in SQLite and running work is recovered as interrupted.
- Transfer progress is saved in `transfer-jobs.json`. If the backend exits while an operation is running, PhotoFlow marks that operation interrupted on the next status request. It does not automatically resume exports, backups, or restores; check the destination and current library before retrying. Progress writes are periodic, so the very latest increment at the instant of a hard crash may not be present.
- If transfer recovery state cannot be read or written, PhotoFlow logs a warning; the active operation can continue, but the previous or latest recovery status may be unavailable.
- Export and backup destinations are not published until a complete staged file is ready. A crash before publication leaves the final filename absent and may leave a hidden staging file in the user-selected folder. Source media is not changed. A crash after publication may leave the complete final file.
- Restore validates the incoming backup and creates a verified safety copy before replacing the live database. It checkpoints SQLite and checks that the database is not still in use before the atomic replacement. A failure before replacement keeps the current database and safety copy. A successfully replaced database remains recoverable from that safety copy.
- SQLite connections use WAL mode, a 30-second busy timeout, foreign-key checks, and transactions that commit on success and roll back on exceptions. If the database is busy, stop maintenance work and retry rather than deleting SQLite files.

## Multiple application launches

PhotoFlow requests Electron's single-instance lock before creating its window or starting the backend. A second launch should quit and focus the first window instead of starting another backend. SQLite WAL and the busy timeout provide additional database coordination, but do not treat them as a substitute for the single-instance lock.

This behavior has automated unit coverage; actual window focus and simultaneous-launch behavior still need manual verification on the release machine.

## Safe user practices and limitations

1. Keep a verified backup outside the PhotoFlow application data directory before restoring or performing manual database maintenance.
2. Do not edit, move, or delete `photoflow.db`, its SQLite sidecars, or safety backups while PhotoFlow is running.
3. Use disposable folders and databases for crash, restore, uninstall, or cache-clearing tests. Never use a personal library for destructive validation.
4. A library removal removes the database entry (and an unshared cached thumbnail), not the source photo or video. Manage originals in their own folders.
5. Uninstall is configured to retain application data, but installer installation, upgrade, and uninstall have not yet been manually exercised on a disposable Windows profile. Back up important state independently.
6. Logs are not rotated automatically and may contain local diagnostic details. Review them before sharing and remove them manually only after PhotoFlow is closed.
7. Large-library performance tests currently use synthetic database records and query-plan checks; they do not establish interactive performance for every real Windows library or storage device.
8. A forced termination can leave a hidden temporary export file in a user-selected destination. PhotoFlow intentionally avoids scanning arbitrary folders for cleanup.
9. Transfer recovery history is a small app-data JSON file; if it is unavailable or malformed, prior transfer status may be lost even though the library database remains separate.
