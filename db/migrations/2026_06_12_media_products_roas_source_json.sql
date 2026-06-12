-- 2026-06-12: 产品级 ROAS 输入来源标注
-- Docs-anchor: docs/superpowers/specs/2026-06-12-product-roas-completion-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND COLUMN_NAME = 'roas_inputs_source_json'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN roas_inputs_source_json JSON NULL AFTER standalone_shipping_fee'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
