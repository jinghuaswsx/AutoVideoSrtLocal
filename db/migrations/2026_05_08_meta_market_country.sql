-- Meta daily campaign/adset/ad market country parsed from ad naming.

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_ad_daily_campaign_metrics'
      AND COLUMN_NAME = 'market_country'
  ),
  'SELECT 1',
  'ALTER TABLE meta_ad_daily_campaign_metrics ADD COLUMN market_country VARCHAR(16) DEFAULT NULL COMMENT ''Parsed market country from campaign/adset/ad name; not Meta geo breakdown'' AFTER product_id'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_ad_daily_adset_metrics'
      AND COLUMN_NAME = 'market_country'
  ),
  'SELECT 1',
  'ALTER TABLE meta_ad_daily_adset_metrics ADD COLUMN market_country VARCHAR(16) DEFAULT NULL COMMENT ''Parsed market country from campaign/adset/ad name; not Meta geo breakdown'' AFTER product_id'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_ad_daily_ad_metrics'
      AND COLUMN_NAME = 'market_country'
  ),
  'SELECT 1',
  'ALTER TABLE meta_ad_daily_ad_metrics ADD COLUMN market_country VARCHAR(16) DEFAULT NULL COMMENT ''Parsed market country from campaign/adset/ad name; not Meta geo breakdown'' AFTER product_id'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_ad_daily_campaign_metrics'
      AND INDEX_NAME = 'idx_meta_daily_campaign_market_country'
  ),
  'SELECT 1',
  'ALTER TABLE meta_ad_daily_campaign_metrics ADD KEY idx_meta_daily_campaign_market_country (market_country, product_id, meta_business_date, report_date)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE meta_ad_daily_campaign_metrics
SET market_country = CASE
  WHEN campaign_name LIKE '%澳大利亚%' OR campaign_name LIKE '%澳洲%' THEN 'AU'
  WHEN campaign_name LIKE '%新西兰%' THEN 'NZ'
  WHEN campaign_name LIKE '%西班牙%' THEN 'ES'
  WHEN campaign_name LIKE '%意大利%' THEN 'IT'
  WHEN campaign_name LIKE '%葡萄牙%' THEN 'PT'
  WHEN campaign_name LIKE '%加拿大%' THEN 'CA'
  WHEN campaign_name LIKE '%英国%' THEN 'GB'
  WHEN campaign_name LIKE '%美国%' THEN 'US'
  WHEN campaign_name LIKE '%法国%' THEN 'FR'
  WHEN campaign_name LIKE '%德国%' THEN 'DE'
  WHEN campaign_name LIKE '%日本%' THEN 'JP'
  WHEN campaign_name LIKE '%荷兰%' THEN 'NL'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])USA([^A-Za-z0-9]|$)' THEN 'US'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])US([^A-Za-z0-9]|$)' THEN 'US'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])UK([^A-Za-z0-9]|$)' THEN 'GB'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])GB([^A-Za-z0-9]|$)' THEN 'GB'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])FR([^A-Za-z0-9]|$)' THEN 'FR'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])DE([^A-Za-z0-9]|$)' THEN 'DE'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])ES([^A-Za-z0-9]|$)' THEN 'ES'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])IT([^A-Za-z0-9]|$)' THEN 'IT'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])JP([^A-Za-z0-9]|$)' THEN 'JP'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])PT([^A-Za-z0-9]|$)' THEN 'PT'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])NL([^A-Za-z0-9]|$)' THEN 'NL'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])CA([^A-Za-z0-9]|$)' THEN 'CA'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])AU([^A-Za-z0-9]|$)' THEN 'AU'
  WHEN campaign_name REGEXP '(^|[^A-Za-z0-9])NZ([^A-Za-z0-9]|$)' THEN 'NZ'
  WHEN campaign_name LIKE '%16国%' OR campaign_name LIKE '%多国%' OR campaign_name LIKE '%欧洲%' OR campaign_name LIKE '%澳新%' OR campaign_name REGEXP '(^|[^A-Za-z0-9])E5([^A-Za-z0-9]|$)' THEN 'MULTI'
  ELSE NULL
END
WHERE market_country IS NULL;

UPDATE meta_ad_daily_adset_metrics
SET market_country = CASE
  WHEN adset_name LIKE '%澳大利亚%' OR adset_name LIKE '%澳洲%' THEN 'AU'
  WHEN adset_name LIKE '%新西兰%' THEN 'NZ'
  WHEN adset_name LIKE '%西班牙%' THEN 'ES'
  WHEN adset_name LIKE '%意大利%' THEN 'IT'
  WHEN adset_name LIKE '%葡萄牙%' THEN 'PT'
  WHEN adset_name LIKE '%加拿大%' THEN 'CA'
  WHEN adset_name LIKE '%英国%' THEN 'GB'
  WHEN adset_name LIKE '%美国%' THEN 'US'
  WHEN adset_name LIKE '%法国%' THEN 'FR'
  WHEN adset_name LIKE '%德国%' THEN 'DE'
  WHEN adset_name LIKE '%日本%' THEN 'JP'
  WHEN adset_name LIKE '%荷兰%' THEN 'NL'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])USA([^A-Za-z0-9]|$)' THEN 'US'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])US([^A-Za-z0-9]|$)' THEN 'US'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])UK([^A-Za-z0-9]|$)' THEN 'GB'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])GB([^A-Za-z0-9]|$)' THEN 'GB'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])FR([^A-Za-z0-9]|$)' THEN 'FR'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])DE([^A-Za-z0-9]|$)' THEN 'DE'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])ES([^A-Za-z0-9]|$)' THEN 'ES'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])IT([^A-Za-z0-9]|$)' THEN 'IT'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])JP([^A-Za-z0-9]|$)' THEN 'JP'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])PT([^A-Za-z0-9]|$)' THEN 'PT'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])NL([^A-Za-z0-9]|$)' THEN 'NL'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])CA([^A-Za-z0-9]|$)' THEN 'CA'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])AU([^A-Za-z0-9]|$)' THEN 'AU'
  WHEN adset_name REGEXP '(^|[^A-Za-z0-9])NZ([^A-Za-z0-9]|$)' THEN 'NZ'
  WHEN adset_name LIKE '%16国%' OR adset_name LIKE '%多国%' OR adset_name LIKE '%欧洲%' OR adset_name LIKE '%澳新%' OR adset_name REGEXP '(^|[^A-Za-z0-9])E5([^A-Za-z0-9]|$)' THEN 'MULTI'
  ELSE NULL
END
WHERE market_country IS NULL;

UPDATE meta_ad_daily_ad_metrics
SET market_country = CASE
  WHEN ad_name LIKE '%澳大利亚%' OR ad_name LIKE '%澳洲%' THEN 'AU'
  WHEN ad_name LIKE '%新西兰%' THEN 'NZ'
  WHEN ad_name LIKE '%西班牙%' THEN 'ES'
  WHEN ad_name LIKE '%意大利%' THEN 'IT'
  WHEN ad_name LIKE '%葡萄牙%' THEN 'PT'
  WHEN ad_name LIKE '%加拿大%' THEN 'CA'
  WHEN ad_name LIKE '%英国%' THEN 'GB'
  WHEN ad_name LIKE '%美国%' THEN 'US'
  WHEN ad_name LIKE '%法国%' THEN 'FR'
  WHEN ad_name LIKE '%德国%' THEN 'DE'
  WHEN ad_name LIKE '%日本%' THEN 'JP'
  WHEN ad_name LIKE '%荷兰%' THEN 'NL'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])USA([^A-Za-z0-9]|$)' THEN 'US'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])US([^A-Za-z0-9]|$)' THEN 'US'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])UK([^A-Za-z0-9]|$)' THEN 'GB'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])GB([^A-Za-z0-9]|$)' THEN 'GB'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])FR([^A-Za-z0-9]|$)' THEN 'FR'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])DE([^A-Za-z0-9]|$)' THEN 'DE'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])ES([^A-Za-z0-9]|$)' THEN 'ES'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])IT([^A-Za-z0-9]|$)' THEN 'IT'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])JP([^A-Za-z0-9]|$)' THEN 'JP'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])PT([^A-Za-z0-9]|$)' THEN 'PT'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])NL([^A-Za-z0-9]|$)' THEN 'NL'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])CA([^A-Za-z0-9]|$)' THEN 'CA'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])AU([^A-Za-z0-9]|$)' THEN 'AU'
  WHEN ad_name REGEXP '(^|[^A-Za-z0-9])NZ([^A-Za-z0-9]|$)' THEN 'NZ'
  WHEN ad_name LIKE '%16国%' OR ad_name LIKE '%多国%' OR ad_name LIKE '%欧洲%' OR ad_name LIKE '%澳新%' OR ad_name REGEXP '(^|[^A-Za-z0-9])E5([^A-Za-z0-9]|$)' THEN 'MULTI'
  ELSE NULL
END
WHERE market_country IS NULL;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_ad_daily_adset_metrics'
      AND INDEX_NAME = 'idx_meta_daily_adset_market_country'
  ),
  'SELECT 1',
  'ALTER TABLE meta_ad_daily_adset_metrics ADD KEY idx_meta_daily_adset_market_country (market_country, product_id, meta_business_date, report_date)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'meta_ad_daily_ad_metrics'
      AND INDEX_NAME = 'idx_meta_daily_ad_market_country'
  ),
  'SELECT 1',
  'ALTER TABLE meta_ad_daily_ad_metrics ADD KEY idx_meta_daily_ad_market_country (market_country, product_id, meta_business_date, report_date)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
