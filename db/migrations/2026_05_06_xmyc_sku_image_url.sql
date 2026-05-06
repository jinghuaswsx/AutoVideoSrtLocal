-- 2026-05-06: Cache XMYC storage SKU image URLs for ROAS matching.

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='xmyc_storage_skus' AND COLUMN_NAME='image_url'),
  'SELECT 1',
  'ALTER TABLE xmyc_storage_skus ADD COLUMN image_url VARCHAR(1000) NULL AFTER goods_name'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
