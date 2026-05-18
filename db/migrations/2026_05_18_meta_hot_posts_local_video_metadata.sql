-- Persisted duration and first-frame cover metadata for Meta hot-post local videos.
-- Docs-anchor: docs/superpowers/specs/2026-05-14-meta-hot-posts-video-localization-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_posts'
      AND COLUMN_NAME = 'local_video_duration_seconds'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_posts
     ADD COLUMN local_video_duration_seconds DECIMAL(12,3) NULL AFTER local_video_path'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_posts'
      AND COLUMN_NAME = 'local_video_cover_path'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_posts
     ADD COLUMN local_video_cover_path VARCHAR(2048) NULL AFTER local_video_duration_seconds'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
