ALTER TABLE media_items ADD COLUMN metadata_status TEXT NOT NULL DEFAULT 'unavailable'
  CHECK (metadata_status IN ('extracted', 'partial', 'unavailable', 'failed'));
ALTER TABLE media_items ADD COLUMN metadata_error TEXT;

UPDATE media_items
SET metadata_status = CASE
  WHEN width IS NOT NULL AND height IS NOT NULL
    AND capture_date IS NOT NULL AND camera_make IS NOT NULL AND camera_model IS NOT NULL
    THEN 'extracted'
  WHEN width IS NOT NULL OR height IS NOT NULL OR capture_date IS NOT NULL
    OR camera_make IS NOT NULL OR camera_model IS NOT NULL
    THEN 'partial'
  ELSE 'unavailable'
END;

CREATE TABLE scan_failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_job_id TEXT NOT NULL REFERENCES scan_jobs(id) ON DELETE CASCADE,
  source_path TEXT NOT NULL,
  media_type TEXT,
  error_summary TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT
);

CREATE INDEX idx_scan_failures_unresolved
  ON scan_failures(resolved_at, source_path, created_at);
