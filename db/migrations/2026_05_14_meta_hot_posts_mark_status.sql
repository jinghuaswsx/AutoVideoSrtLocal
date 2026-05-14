-- Add two-choice local annotation status for Meta hot post cards.
-- Existing single checked rows are preserved as the negative choice shown in the UI.
-- Docs-anchor: docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md#后台页面

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_posts'
      AND COLUMN_NAME = 'mark_status'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_posts
     ADD COLUMN mark_status VARCHAR(16) NULL AFTER is_marked,
     ADD KEY idx_meta_hot_posts_mark_status (mark_status, updated_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE meta_hot_posts
SET mark_status = 'bad'
WHERE is_marked = 1
  AND mark_status IS NULL;
