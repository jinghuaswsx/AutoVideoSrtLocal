-- Optimization indexes for Meta Hot Posts search and sorting.
-- Docs-anchor: docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_posts'
      AND INDEX_NAME = 'idx_meta_hot_posts_sync_likes_creation'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_posts ADD INDEX idx_meta_hot_posts_sync_likes_creation (sync_period_likes, creation_time, id)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
