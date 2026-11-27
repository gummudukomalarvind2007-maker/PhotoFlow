CREATE TABLE IF NOT EXISTS scan_jobs (
  id TEXT PRIMARY KEY NOT NULL,
  folders_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'cancelled', 'interrupted', 'failed')),
  discovered_files INTEGER NOT NULL DEFAULT 0,
  processed_files INTEGER NOT NULL DEFAULT 0,
  indexed_files INTEGER NOT NULL DEFAULT 0,
  skipped_files INTEGER NOT NULL DEFAULT 0,
  failed_files INTEGER NOT NULL DEFAULT 0,
  current_path TEXT,
  error_summary TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  resumed_from_id TEXT REFERENCES scan_jobs(id),
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS media_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_path TEXT NOT NULL UNIQUE,
  file_identity TEXT,
  content_hash TEXT,
  media_type TEXT NOT NULL CHECK (media_type IN ('photo', 'video')),
  extension TEXT NOT NULL,
  filename TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  modified_time_ns INTEGER NOT NULL,
  width INTEGER,
  height INTEGER,
  duration_seconds REAL,
  frame_rate REAL,
  capture_date TEXT,
  camera_make TEXT,
  camera_model TEXT,
  gps_latitude REAL,
  gps_longitude REAL,
  thumbnail_path TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  metadata_provenance TEXT NOT NULL DEFAULT '{}',
  last_scan_id TEXT REFERENCES scan_jobs(id),
  first_scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_media_items_type ON media_items(media_type);
CREATE INDEX IF NOT EXISTS idx_media_items_hash ON media_items(content_hash);
CREATE INDEX IF NOT EXISTS idx_scan_jobs_status ON scan_jobs(status);
