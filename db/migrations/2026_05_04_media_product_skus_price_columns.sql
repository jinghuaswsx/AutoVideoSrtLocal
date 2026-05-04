-- 2026-05-04: media_product_skus 加 Shopify variant 售价 / 划线价 / 币种字段

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND COLUMN_NAME = 'shopify_price'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD COLUMN shopify_price DECIMAL(12,2) NULL AFTER shopify_sku'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND COLUMN_NAME = 'shopify_compare_at_price'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD COLUMN shopify_compare_at_price DECIMAL(12,2) NULL AFTER shopify_price'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND COLUMN_NAME = 'shopify_currency'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD COLUMN shopify_currency VARCHAR(8) NULL AFTER shopify_compare_at_price'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND COLUMN_NAME = 'shopify_inventory_quantity'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD COLUMN shopify_inventory_quantity INT NULL AFTER shopify_currency'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND COLUMN_NAME = 'shopify_weight_grams'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD COLUMN shopify_weight_grams DECIMAL(10,2) NULL AFTER shopify_inventory_quantity'
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
