-- 2026-04-29: 素材管理 ROAS 独立站运费字段

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND COLUMN_NAME = 'standalone_shipping_fee'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN standalone_shipping_fee DECIMAL(10,2) NULL AFTER standalone_price'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
