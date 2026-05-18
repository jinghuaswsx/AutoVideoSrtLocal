-- Local cover cache fields for the archived Mingkong Top300 material library.
-- Docs-anchor: docs/superpowers/specs/2026-05-18-mingkong-video-material-local-index-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'mingkong_material_daily_snapshots'
      AND COLUMN_NAME = 'local_cover_object_key'
  ),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots
     ADD COLUMN local_cover_object_key VARCHAR(512) NULL AFTER video_image_path'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'mingkong_material_daily_snapshots'
      AND COLUMN_NAME = 'cover_cached_at'
  ),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots
     ADD COLUMN cover_cached_at DATETIME NULL AFTER local_cover_object_key'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'mingkong_material_daily_snapshots'
      AND COLUMN_NAME = 'cover_cache_error'
  ),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots
     ADD COLUMN cover_cache_error VARCHAR(1000) NULL AFTER cover_cached_at'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'mingkong_material_daily_top100'
      AND COLUMN_NAME = 'local_cover_object_key'
  ),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_top100
     ADD COLUMN local_cover_object_key VARCHAR(512) NULL AFTER video_image_path'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'mingkong_material_daily_top100'
      AND COLUMN_NAME = 'cover_cached_at'
  ),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_top100
     ADD COLUMN cover_cached_at DATETIME NULL AFTER local_cover_object_key'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'mingkong_material_daily_top100'
      AND COLUMN_NAME = 'cover_cache_error'
  ),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_top100
     ADD COLUMN cover_cache_error VARCHAR(1000) NULL AFTER cover_cached_at'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
