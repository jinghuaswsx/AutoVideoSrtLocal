-- Persist upstream Mingkong pushed marker for Meta hot-post cards.
-- Docs-anchor: docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_posts'
      AND COLUMN_NAME = 'is_pushed'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_posts
     ADD COLUMN is_pushed TINYINT(1) NOT NULL DEFAULT 0 AFTER copycat,
     ADD KEY idx_meta_hot_posts_is_pushed (is_pushed, updated_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
