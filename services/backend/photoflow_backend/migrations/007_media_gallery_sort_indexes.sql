CREATE INDEX IF NOT EXISTS idx_media_items_type_capture_newest
  ON media_items(
    media_type,
    (date(CASE WHEN substr(capture_date, 5, 1) = ':' THEN substr(capture_date, 1, 4) || '-' || substr(capture_date, 6, 2) || '-' || substr(capture_date, 9, 2) ELSE substr(capture_date, 1, 10) END) IS NULL),
    date(CASE WHEN substr(capture_date, 5, 1) = ':' THEN substr(capture_date, 1, 4) || '-' || substr(capture_date, 6, 2) || '-' || substr(capture_date, 9, 2) ELSE substr(capture_date, 1, 10) END) DESC,
    id ASC
  );

CREATE INDEX IF NOT EXISTS idx_media_items_type_capture_oldest
  ON media_items(
    media_type,
    (date(CASE WHEN substr(capture_date, 5, 1) = ':' THEN substr(capture_date, 1, 4) || '-' || substr(capture_date, 6, 2) || '-' || substr(capture_date, 9, 2) ELSE substr(capture_date, 1, 10) END) IS NULL),
    date(CASE WHEN substr(capture_date, 5, 1) = ':' THEN substr(capture_date, 1, 4) || '-' || substr(capture_date, 6, 2) || '-' || substr(capture_date, 9, 2) ELSE substr(capture_date, 1, 10) END) ASC,
    id ASC
  );

