-- 2026-04-28: 素材管理产品 ROAS 成本维护字段

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND COLUMN_NAME = 'purchase_1688_url'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN purchase_1688_url VARCHAR(2048) NULL AFTER product_link'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'media_products' AND COLUMN_NAME = 'purchase_price'),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN purchase_price DECIMAL(10,2) NULL AFTER purchase_1688_url'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'media_products' AND COLUMN_NAME = 'packet_cost_estimated'),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN packet_cost_estimated DECIMAL(10,2) NULL AFTER purchase_price'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'media_products' AND COLUMN_NAME = 'packet_cost_actual'),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN packet_cost_actual DECIMAL(10,2) NULL AFTER packet_cost_estimated'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'media_products' AND COLUMN_NAME = 'package_length_cm'),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN package_length_cm DECIMAL(8,2) NULL AFTER packet_cost_actual'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'media_products' AND COLUMN_NAME = 'package_width_cm'),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN package_width_cm DECIMAL(8,2) NULL AFTER package_length_cm'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'media_products' AND COLUMN_NAME = 'package_height_cm'),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN package_height_cm DECIMAL(8,2) NULL AFTER package_width_cm'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'media_products' AND COLUMN_NAME = 'tk_sea_cost'),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN tk_sea_cost DECIMAL(10,2) NULL AFTER package_height_cm'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'media_products' AND COLUMN_NAME = 'tk_air_cost'),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN tk_air_cost DECIMAL(10,2) NULL AFTER tk_sea_cost'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'media_products' AND COLUMN_NAME = 'tk_sale_price'),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN tk_sale_price DECIMAL(10,2) NULL AFTER tk_air_cost'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'media_products' AND COLUMN_NAME = 'standalone_price'),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN standalone_price DECIMAL(10,2) NULL AFTER tk_sale_price'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
