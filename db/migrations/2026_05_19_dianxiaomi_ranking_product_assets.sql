-- Product page assets and Mingkong-derived Chinese names for selection center product library.
-- Docs-anchor: docs/superpowers/specs/2026-05-19-mingkong-product-library-assets-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND COLUMN_NAME = 'product_code'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD COLUMN product_code VARCHAR(255) NULL AFTER media_product_id'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND COLUMN_NAME = 'product_main_image_url'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD COLUMN product_main_image_url VARCHAR(1000) NULL AFTER product_code'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND COLUMN_NAME = 'product_main_image_object_key'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD COLUMN product_main_image_object_key VARCHAR(512) NULL AFTER product_main_image_url'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND COLUMN_NAME = 'product_detail_images_json'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD COLUMN product_detail_images_json JSON NULL AFTER product_main_image_object_key'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND COLUMN_NAME = 'product_assets_error'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD COLUMN product_assets_error VARCHAR(1000) NULL AFTER product_detail_images_json'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND COLUMN_NAME = 'product_cn_name'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD COLUMN product_cn_name VARCHAR(255) NULL AFTER product_assets_error'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND COLUMN_NAME = 'mk_first_material_name'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD COLUMN mk_first_material_name VARCHAR(500) NULL AFTER product_cn_name'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND COLUMN_NAME = 'mk_first_material_path'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD COLUMN mk_first_material_path VARCHAR(1000) NULL AFTER mk_first_material_name'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND COLUMN_NAME = 'mk_first_material_url'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD COLUMN mk_first_material_url VARCHAR(1000) NULL AFTER mk_first_material_path'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND COLUMN_NAME = 'mk_material_error'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD COLUMN mk_material_error VARCHAR(1000) NULL AFTER mk_first_material_url'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND COLUMN_NAME = 'product_assets_synced_at'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD COLUMN product_assets_synced_at DATETIME NULL AFTER mk_material_error'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'dianxiaomi_rankings'
      AND INDEX_NAME = 'idx_dxm_rankings_product_code'
  ),
  'SELECT 1',
  'ALTER TABLE dianxiaomi_rankings
     ADD KEY idx_dxm_rankings_product_code (product_code)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
