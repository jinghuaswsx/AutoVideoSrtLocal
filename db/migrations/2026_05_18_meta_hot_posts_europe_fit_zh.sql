-- Cached Chinese interpretation for Meta hot-post Europe fit assessments.
-- Docs-anchor: docs/superpowers/specs/2026-05-18-meta-hot-posts-europe-analysis-zh-backfill-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_post_europe_assessments'
      AND COLUMN_NAME = 'strengths_zh_json'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_post_europe_assessments
     ADD COLUMN strengths_zh_json JSON NULL AFTER strengths_json,
     ADD COLUMN risks_zh_json JSON NULL AFTER risks_json,
     ADD COLUMN required_changes_zh_json JSON NULL AFTER required_changes_json,
     ADD COLUMN reasoning_zh TEXT NULL AFTER reasoning,
     ADD COLUMN zh_status VARCHAR(16) NOT NULL DEFAULT ''pending'' AFTER reasoning_zh,
     ADD COLUMN zh_attempts INT UNSIGNED NOT NULL DEFAULT 0 AFTER zh_status,
     ADD COLUMN zh_error MEDIUMTEXT NULL AFTER zh_attempts,
     ADD COLUMN zh_translated_at DATETIME NULL AFTER zh_error,
     ADD KEY idx_meta_hot_post_europe_assessments_zh_status (zh_status, zh_attempts, updated_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE meta_hot_post_europe_assessments
SET zh_status = 'pending'
WHERE status = 'done'
  AND (
    strengths_json IS NOT NULL
    OR risks_json IS NOT NULL
    OR required_changes_json IS NOT NULL
    OR reasoning IS NOT NULL
  )
  AND (
    strengths_zh_json IS NULL
    OR risks_zh_json IS NULL
    OR required_changes_zh_json IS NULL
    OR reasoning_zh IS NULL
    OR reasoning_zh = ''
  );

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES (
  'meta_hot_posts.europe_fit_translate',
  'gemini_vertex_adc',
  'gemini-3.1-flash-lite',
  NULL,
  1,
  NULL
)
ON DUPLICATE KEY UPDATE
  provider_code = VALUES(provider_code),
  model_id = VALUES(model_id),
  enabled = VALUES(enabled);
