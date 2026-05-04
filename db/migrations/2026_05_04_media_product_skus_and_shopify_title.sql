-- 2026-05-04: 素材产品的 Shopify 英文名 + variant ↔ 店小秘 SKU 配对表

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND COLUMN_NAME = 'shopify_title'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN shopify_title VARCHAR(512) NULL AFTER shopifyid'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
  ),
  'SELECT 1',
  'CREATE TABLE media_product_skus (
     id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
     product_id BIGINT UNSIGNED NOT NULL,
     shopify_product_id VARCHAR(32) NULL,
     shopify_variant_id VARCHAR(32) NULL,
     shopify_sku VARCHAR(128) NULL,
     shopify_variant_title VARCHAR(512) NULL,
     dianxiaomi_sku VARCHAR(128) NULL,
     dianxiaomi_sku_code VARCHAR(64) NULL,
     dianxiaomi_name VARCHAR(512) NULL,
     source VARCHAR(32) NULL,
     created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
     updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
     UNIQUE KEY uk_media_product_skus_pid_variant (product_id, shopify_variant_id),
     KEY idx_media_product_skus_product (product_id),
     KEY idx_media_product_skus_dxm_sku (dianxiaomi_sku),
     KEY idx_media_product_skus_dxm_code (dianxiaomi_sku_code),
     KEY idx_media_product_skus_shopify_sku (shopify_sku)
   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
