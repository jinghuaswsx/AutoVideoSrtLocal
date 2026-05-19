-- Cached Chinese interpretation for Meta hot-post video copyability summaries.
-- Docs-anchor: docs/superpowers/specs/2026-05-18-meta-hot-posts-video-analysis-zh-backfill-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_hot_post_video_copyability_analyses'
      AND COLUMN_NAME = 'summary_zh'
  ),
  'SELECT 1',
  'ALTER TABLE meta_hot_post_video_copyability_analyses
     ADD COLUMN summary_zh TEXT NULL AFTER summary,
     ADD COLUMN summary_zh_status VARCHAR(16) NOT NULL DEFAULT ''pending'' AFTER summary_zh,
     ADD COLUMN summary_zh_attempts INT UNSIGNED NOT NULL DEFAULT 0 AFTER summary_zh_status,
     ADD COLUMN summary_zh_error MEDIUMTEXT NULL AFTER summary_zh_attempts,
     ADD COLUMN summary_zh_translated_at DATETIME NULL AFTER summary_zh_error,
     ADD KEY idx_meta_hot_post_video_copyability_summary_zh_status (summary_zh_status, summary_zh_attempts, updated_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE meta_hot_post_video_copyability_analyses
SET summary_zh_status = 'pending'
WHERE status = 'done'
  AND summary IS NOT NULL
  AND TRIM(summary) <> ''
  AND (summary_zh IS NULL OR summary_zh = '');

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES (
  'meta_hot_posts.video_copyability_translate',
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
