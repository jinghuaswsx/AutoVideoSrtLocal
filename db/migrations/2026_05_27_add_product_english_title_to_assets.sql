-- 2026-05-27: Add product_english_title to dianxiaomi_product_assets

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_product_assets'
      AND COLUMN_NAME = 'product_english_title'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_product_assets ADD COLUMN product_english_title VARCHAR(512) NULL AFTER product_cn_name'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
