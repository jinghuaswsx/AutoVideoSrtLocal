-- 2026-05-04: 给 xmyc_storage_skus 加 SKU 维度的售价 / 运费 / 物流费聚合列
-- 这些列由 tools/sku_aggregates_backfill.py 按 SKU 维度聚合后填入；
-- 让每个 SKU 都能独立算出自己的保本 ROAS。

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='xmyc_storage_skus' AND COLUMN_NAME='standalone_price_sku'),
  'SELECT 1',
  'ALTER TABLE xmyc_storage_skus ADD COLUMN standalone_price_sku DECIMAL(10,2) NULL AFTER unit_price'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='xmyc_storage_skus' AND COLUMN_NAME='standalone_shipping_fee_sku'),
  'SELECT 1',
  'ALTER TABLE xmyc_storage_skus ADD COLUMN standalone_shipping_fee_sku DECIMAL(10,2) NULL AFTER standalone_price_sku'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='xmyc_storage_skus' AND COLUMN_NAME='packet_cost_actual_sku'),
  'SELECT 1',
  'ALTER TABLE xmyc_storage_skus ADD COLUMN packet_cost_actual_sku DECIMAL(12,2) NULL AFTER standalone_shipping_fee_sku'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='xmyc_storage_skus' AND COLUMN_NAME='sku_orders_count'),
  'SELECT 1',
  'ALTER TABLE xmyc_storage_skus ADD COLUMN sku_orders_count INT NULL AFTER packet_cost_actual_sku'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
