-- Cached Chinese translations for Meta hot post video captions.
-- Docs-anchor: docs/superpowers/specs/2026-05-14-meta-hot-posts-message-translation-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_posts'
      AND COLUMN_NAME = 'message_zh_html'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_posts
     ADD COLUMN message_zh_html MEDIUMTEXT NULL AFTER message_html,
     ADD COLUMN message_zh_status VARCHAR(16) NOT NULL DEFAULT ''pending'' AFTER message_zh_html,
     ADD COLUMN message_zh_attempts INT UNSIGNED NOT NULL DEFAULT 0 AFTER message_zh_status,
     ADD COLUMN message_zh_error MEDIUMTEXT NULL AFTER message_zh_attempts,
     ADD COLUMN message_zh_translated_at DATETIME NULL AFTER message_zh_error,
     ADD KEY idx_meta_hot_posts_message_zh_status (message_zh_status, message_zh_attempts, updated_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE meta_hot_posts
SET message_zh_status = 'pending'
WHERE message_html IS NOT NULL
  AND TRIM(message_html) <> ''
  AND (message_zh_html IS NULL OR message_zh_html = '');
