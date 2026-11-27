CREATE INDEX IF NOT EXISTS idx_media_items_extension ON media_items(extension);
CREATE INDEX IF NOT EXISTS idx_media_items_capture_date ON media_items(capture_date);
CREATE INDEX IF NOT EXISTS idx_media_items_capture_date_normalized
  ON media_items(date(CASE WHEN substr(capture_date, 5, 1) = ':' THEN substr(capture_date, 1, 4) || '-' || substr(capture_date, 6, 2) || '-' || substr(capture_date, 9, 2) ELSE substr(capture_date, 1, 10) END));
CREATE INDEX IF NOT EXISTS idx_media_items_camera_make ON media_items(camera_make);
CREATE INDEX IF NOT EXISTS idx_media_items_camera_model ON media_items(camera_model);
CREATE INDEX IF NOT EXISTS idx_media_items_filename_nocase ON media_items(filename COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_media_items_size ON media_items(size_bytes);
