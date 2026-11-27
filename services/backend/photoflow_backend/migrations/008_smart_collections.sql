ALTER TABLE media_items ADD COLUMN last_viewed_at TEXT;

CREATE INDEX idx_media_items_last_viewed
  ON media_items(last_viewed_at DESC, id DESC)
  WHERE last_viewed_at IS NOT NULL;

CREATE INDEX idx_media_items_photo_dimensions
  ON media_items(width, height)
  WHERE media_type = 'photo' AND width IS NOT NULL AND height IS NOT NULL;

CREATE INDEX idx_media_items_photo_pixel_count
  ON media_items(width * height)
  WHERE media_type = 'photo' AND width IS NOT NULL AND height IS NOT NULL;

CREATE INDEX idx_media_items_video_duration
  ON media_items(duration_seconds)
  WHERE media_type = 'video' AND duration_seconds IS NOT NULL;

CREATE TABLE saved_smart_albums (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  media_type TEXT NOT NULL CHECK (media_type IN ('all', 'photo', 'video')),
  filters_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (length(trim(name)) BETWEEN 1 AND 100),
  CHECK (length(description) <= 500)
);
