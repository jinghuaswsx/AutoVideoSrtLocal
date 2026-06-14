-- Add cached Chinese video information for Tabcut videos.
-- Docs-anchor: docs/superpowers/specs/2026-06-14-tabcut-video-translation-task-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tabcut_videos'
      AND COLUMN_NAME = 'video_desc_zh'
  ),
  'SELECT 1',
  'ALTER TABLE tabcut_videos
     ADD COLUMN video_desc_zh MEDIUMTEXT NULL AFTER video_desc,
     ADD COLUMN primary_item_name_zh TEXT NULL AFTER primary_item_name,
     ADD COLUMN zh_translation_status VARCHAR(16) NOT NULL DEFAULT ''pending'' AFTER primary_item_name_zh,
     ADD COLUMN zh_translation_attempts INT UNSIGNED NOT NULL DEFAULT 0 AFTER zh_translation_status,
     ADD COLUMN zh_translation_error MEDIUMTEXT NULL AFTER zh_translation_attempts,
     ADD COLUMN zh_translated_at DATETIME NULL AFTER zh_translation_error,
     ADD KEY idx_tabcut_videos_zh_translation_status (zh_translation_status, zh_translation_attempts, last_seen_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE tabcut_videos
SET zh_translation_status = 'pending'
WHERE (
    (video_desc IS NOT NULL AND TRIM(video_desc) <> '')
    OR (primary_item_name IS NOT NULL AND TRIM(primary_item_name) <> '')
  )
  AND (
    video_desc_zh IS NULL OR video_desc_zh = ''
    OR primary_item_name_zh IS NULL OR primary_item_name_zh = ''
  );

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES (
  'tabcut.translate_video_info',
  'openrouter',
  'google/gemini-2.5-flash',
  NULL,
  1,
  NULL
)
ON DUPLICATE KEY UPDATE
  provider_code = VALUES(provider_code),
  model_id = VALUES(model_id),
  enabled = VALUES(enabled);
