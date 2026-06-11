-- Add cached Chinese product information for Tabcut goods.
-- Docs-anchor: docs/superpowers/specs/2026-06-11-tabcut-product-chinese-info-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tabcut_goods'
      AND COLUMN_NAME = 'item_name_zh'
  ),
  'SELECT 1',
  'ALTER TABLE tabcut_goods
     ADD COLUMN item_name_zh TEXT NULL AFTER raw_json,
     ADD COLUMN item_name_zh_short VARCHAR(255) NULL AFTER item_name_zh,
     ADD COLUMN category_name_zh VARCHAR(255) NULL AFTER item_name_zh_short,
     ADD COLUMN category_l1_name_zh VARCHAR(255) NULL AFTER category_name_zh,
     ADD COLUMN category_l2_name_zh VARCHAR(255) NULL AFTER category_l1_name_zh,
     ADD COLUMN category_l3_name_zh VARCHAR(255) NULL AFTER category_l2_name_zh,
     ADD COLUMN zh_translation_status VARCHAR(16) NOT NULL DEFAULT ''pending'' AFTER category_l3_name_zh,
     ADD COLUMN zh_translation_attempts INT UNSIGNED NOT NULL DEFAULT 0 AFTER zh_translation_status,
     ADD COLUMN zh_translation_error MEDIUMTEXT NULL AFTER zh_translation_attempts,
     ADD COLUMN zh_translated_at DATETIME NULL AFTER zh_translation_error,
     ADD KEY idx_tabcut_goods_zh_translation_status (zh_translation_status, zh_translation_attempts, last_seen_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE tabcut_goods
SET zh_translation_status = 'pending'
WHERE item_name IS NOT NULL
  AND TRIM(item_name) <> ''
  AND (item_name_zh IS NULL OR item_name_zh = '');

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES (
  'tabcut.translate_goods_info',
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
