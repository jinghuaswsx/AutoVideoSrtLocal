-- Speed up /medias/api/video-materials campaign detail lookup.
-- The page batches current media product ids and product codes against
-- meta_ad_daily_campaign_metrics, then chooses the latest positive-spend row.

SET @idx_exists := (
  SELECT COUNT(1)
  FROM INFORMATION_SCHEMA.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'meta_ad_daily_campaign_metrics'
    AND INDEX_NAME = 'idx_meta_daily_campaign_product_recent'
);
SET @ddl := IF(
  @idx_exists = 0,
  'ALTER TABLE meta_ad_daily_campaign_metrics ADD KEY idx_meta_daily_campaign_product_recent (product_id, meta_business_date, report_date, spend_usd, id)',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @idx_exists := (
  SELECT COUNT(1)
  FROM INFORMATION_SCHEMA.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'meta_ad_daily_campaign_metrics'
    AND INDEX_NAME = 'idx_meta_daily_campaign_code_recent'
);
SET @ddl := IF(
  @idx_exists = 0,
  'ALTER TABLE meta_ad_daily_campaign_metrics ADD KEY idx_meta_daily_campaign_code_recent (normalized_campaign_code, meta_business_date, report_date, spend_usd, id)',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
