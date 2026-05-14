-- Local annotation checkbox for Meta hot post cards.
-- Docs-anchor: docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md#后台页面

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_posts'
      AND COLUMN_NAME = 'is_marked'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_posts
     ADD COLUMN is_marked TINYINT(1) NOT NULL DEFAULT 0 AFTER select_json,
     ADD COLUMN marked_at DATETIME DEFAULT NULL AFTER is_marked,
     ADD COLUMN marked_by INT DEFAULT NULL AFTER marked_at,
     ADD KEY idx_meta_hot_posts_is_marked (is_marked, updated_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
