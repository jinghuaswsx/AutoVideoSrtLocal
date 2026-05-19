-- Cached Chinese translations for Meta hot post product page titles.
-- Docs-anchor: docs/superpowers/specs/2026-05-19-meta-hot-posts-product-title-translation-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_post_product_analyses'
      AND COLUMN_NAME = 'product_title_zh'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_post_product_analyses
     ADD COLUMN product_title_zh TEXT NULL AFTER product_title,
     ADD COLUMN product_title_zh_status VARCHAR(16) NOT NULL DEFAULT ''pending'' AFTER product_title_zh,
     ADD COLUMN product_title_zh_attempts INT UNSIGNED NOT NULL DEFAULT 0 AFTER product_title_zh_status,
     ADD COLUMN product_title_zh_error MEDIUMTEXT NULL AFTER product_title_zh_attempts,
     ADD COLUMN product_title_zh_translated_at DATETIME NULL AFTER product_title_zh_error,
     ADD KEY idx_meta_hot_post_product_title_zh_status (product_title_zh_status, product_title_zh_attempts, updated_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE meta_hot_post_product_analyses
SET product_title_zh_status = 'pending'
WHERE product_title IS NOT NULL
  AND TRIM(product_title) <> ''
  AND (product_title_zh IS NULL OR product_title_zh = '');

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES (
  'meta_hot_posts.translate_product_title',
  'openrouter',
  'google/gemini-3.1-flash-lite',
  NULL,
  1,
  NULL
)
ON DUPLICATE KEY UPDATE
  provider_code = VALUES(provider_code),
  model_id = VALUES(model_id),
  enabled = VALUES(enabled);
