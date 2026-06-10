-- Add local media import binding columns to tabcut_videos table.
-- Docs-anchor: docs/superpowers/specs/2026-06-10-tabcut-video-new-material-task-integration.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tabcut_videos'
      AND COLUMN_NAME = 'local_product_id'
  ),
  'SELECT 1',
  'ALTER TABLE tabcut_videos
     ADD COLUMN local_product_id INT UNSIGNED NULL AFTER local_video_error,
     ADD COLUMN local_media_item_id INT UNSIGNED NULL AFTER local_product_id,
     ADD KEY idx_tabcut_videos_local_product_id (local_product_id),
     ADD KEY idx_tabcut_videos_local_media_item_id (local_media_item_id)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
