-- Local video cache fields for Meta hot post cards.
-- Docs-anchor: docs/superpowers/specs/2026-05-14-meta-hot-posts-video-localization-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_posts'
      AND COLUMN_NAME = 'local_video_path'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_posts
     ADD COLUMN local_video_path VARCHAR(2048) NULL AFTER image_url,
     ADD COLUMN local_video_status VARCHAR(16) NOT NULL DEFAULT ''pending'' AFTER local_video_path,
     ADD COLUMN local_video_error MEDIUMTEXT NULL AFTER local_video_status,
     ADD COLUMN local_video_downloaded_at DATETIME NULL AFTER local_video_error,
     ADD COLUMN local_video_attempts INT UNSIGNED NOT NULL DEFAULT 0 AFTER local_video_downloaded_at,
     ADD KEY idx_meta_hot_posts_local_video_status (local_video_status, local_video_attempts, updated_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE meta_hot_posts
SET local_video_status = 'pending'
WHERE video_url IS NOT NULL
  AND TRIM(video_url) <> ''
  AND (local_video_status IS NULL OR local_video_status = '');
