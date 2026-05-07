-- 2026-05-07: media_product_skus 增加店小秘商品 SKU 字段

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND COLUMN_NAME = 'dianxiaomi_product_sku'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD COLUMN dianxiaomi_product_sku VARCHAR(128) NULL AFTER dianxiaomi_sku'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_product_skus'
      AND INDEX_NAME = 'idx_media_product_skus_dxm_product_sku'
  ),
  'SELECT 1',
  'ALTER TABLE media_product_skus ADD KEY idx_media_product_skus_dxm_product_sku (dianxiaomi_product_sku)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
