CREATE TABLE IF NOT EXISTS photo_analysis (
  media_id INTEGER PRIMARY KEY NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
  source_hash TEXT NOT NULL,
  perceptual_hash TEXT,
  laplacian_variance REAL,
  mean_luminance REAL,
  dark_pixel_percent REAL,
  bright_pixel_percent REAL,
  issues_json TEXT NOT NULL DEFAULT '[]',
  analysis_error TEXT,
  analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_photo_analysis_source_hash ON photo_analysis(source_hash);
CREATE INDEX IF NOT EXISTS idx_photo_analysis_phash ON photo_analysis(perceptual_hash);

CREATE TABLE IF NOT EXISTS photo_hash_bands (
  media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
  band_index INTEGER NOT NULL,
  band_value INTEGER NOT NULL,
  PRIMARY KEY (media_id, band_index)
);

CREATE INDEX IF NOT EXISTS idx_photo_hash_bands_lookup
  ON photo_hash_bands(band_index, band_value, media_id);

CREATE TABLE IF NOT EXISTS similar_photo_pairs (
  left_media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
  right_media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
  hamming_distance INTEGER NOT NULL,
  PRIMARY KEY (left_media_id, right_media_id),
  CHECK (left_media_id < right_media_id)
);

CREATE INDEX IF NOT EXISTS idx_similar_photo_pairs_distance
  ON similar_photo_pairs(hamming_distance, left_media_id, right_media_id);

CREATE TABLE IF NOT EXISTS photo_review_state (
  media_id INTEGER PRIMARY KEY NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending', 'later', 'reviewed')),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS photo_analysis_jobs (
  id TEXT PRIMARY KEY NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'cancelled', 'interrupted', 'failed')),
  similarity_threshold INTEGER NOT NULL DEFAULT 6 CHECK (similarity_threshold BETWEEN 0 AND 7),
  total_files INTEGER NOT NULL DEFAULT 0,
  processed_files INTEGER NOT NULL DEFAULT 0,
  analyzed_files INTEGER NOT NULL DEFAULT 0,
  failed_files INTEGER NOT NULL DEFAULT 0,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  error_summary TEXT,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_photo_analysis_jobs_status
  ON photo_analysis_jobs(status, started_at);
