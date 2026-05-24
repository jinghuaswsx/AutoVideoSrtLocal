-- Add local media import binding columns to meta_hot_posts table.
-- Docs-anchor: docs/superpowers/specs/2026-05-24-meta-hot-posts-import-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_posts'
      AND COLUMN_NAME = 'local_product_id'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_posts
     ADD COLUMN local_product_id INT UNSIGNED NULL AFTER message_html,
     ADD COLUMN local_media_item_id INT UNSIGNED NULL AFTER local_product_id,
     ADD KEY idx_meta_hot_posts_local_product_id (local_product_id),
     ADD KEY idx_meta_hot_posts_local_media_item_id (local_media_item_id)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
