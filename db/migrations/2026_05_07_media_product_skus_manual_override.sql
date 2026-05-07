-- 2026-05-07: media_product_skus 行级人工编辑锁定字段

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND COLUMN_NAME = 'manual_override'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD COLUMN manual_override TINYINT(1) NOT NULL DEFAULT 0 AFTER source'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND COLUMN_NAME = 'manual_unit_price_rmb'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD COLUMN manual_unit_price_rmb DECIMAL(12,2) NULL AFTER manual_override'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND COLUMN_NAME = 'manual_goods_name'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD COLUMN manual_goods_name VARCHAR(512) NULL AFTER manual_unit_price_rmb'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND COLUMN_NAME = 'manual_edited_by'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD COLUMN manual_edited_by BIGINT UNSIGNED NULL AFTER manual_goods_name'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND COLUMN_NAME = 'manual_edited_at'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD COLUMN manual_edited_at DATETIME NULL AFTER manual_edited_by'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
