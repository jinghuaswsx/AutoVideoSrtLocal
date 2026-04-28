-- True ROAS attribution fields.
-- Order side stores Beijing-time Meta business days (16:00~next-day 16:00).

ALTER TABLE dianxiaomi_order_lines
  ADD COLUMN attribution_time_at DATETIME DEFAULT NULL COMMENT 'Beijing order timestamp used for ad attribution',
  ADD COLUMN attribution_source VARCHAR(32) DEFAULT NULL COMMENT 'order_paid_at / paid_at / order_created_at / shipped_at',
  ADD COLUMN attribution_timezone VARCHAR(64) DEFAULT 'Asia/Shanghai',
  ADD COLUMN meta_business_date DATE DEFAULT NULL COMMENT 'Meta-aligned business date, Beijing 16:00 cutover',
  ADD COLUMN meta_window_start_at DATETIME DEFAULT NULL,
  ADD COLUMN meta_window_end_at DATETIME DEFAULT NULL,
  ADD KEY idx_dxm_lines_meta_business_date (meta_business_date),
  ADD KEY idx_dxm_lines_attr_time (attribution_time_at);

ALTER TABLE meta_ad_campaign_metrics
  ADD COLUMN meta_business_date DATE DEFAULT NULL COMMENT 'Only set for single-day Meta reports',
  ADD COLUMN meta_window_start_at DATETIME DEFAULT NULL COMMENT 'Beijing 16:00 window start',
  ADD COLUMN meta_window_end_at DATETIME DEFAULT NULL COMMENT 'Beijing 16:00 next-day window end',
  ADD COLUMN attribution_timezone VARCHAR(64) DEFAULT 'Asia/Shanghai',
  ADD KEY idx_meta_ad_meta_business_date (meta_business_date);

CREATE TABLE IF NOT EXISTS meta_ad_daily_campaign_metrics (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  import_batch_id BIGINT NOT NULL,
  ad_account_id VARCHAR(32) DEFAULT NULL,
  ad_account_name VARCHAR(128) DEFAULT NULL,
  report_date DATE NOT NULL,
  report_start_date DATE NOT NULL,
  report_end_date DATE NOT NULL,
  campaign_name VARCHAR(255) NOT NULL,
  normalized_campaign_code VARCHAR(255) NOT NULL,
  product_code VARCHAR(255) DEFAULT NULL,
  matched_product_code VARCHAR(128) DEFAULT NULL,
  product_id INT DEFAULT NULL,
  result_count INT NOT NULL DEFAULT 0,
  result_metric VARCHAR(128) DEFAULT NULL,
  spend_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  purchase_value_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  roas_purchase DECIMAL(12,6) DEFAULT NULL,
  raw_json JSON DEFAULT NULL,
  imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_meta_daily_campaign_product (product_id, report_date),
  KEY idx_meta_daily_campaign_report_date (report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Meta daily campaign metrics';

CREATE TABLE IF NOT EXISTS meta_ad_daily_ad_metrics (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  import_batch_id BIGINT NOT NULL,
  ad_account_id VARCHAR(32) DEFAULT NULL,
  ad_account_name VARCHAR(128) DEFAULT NULL,
  report_date DATE NOT NULL,
  report_start_date DATE NOT NULL,
  report_end_date DATE NOT NULL,
  ad_name VARCHAR(512) NOT NULL,
  normalized_ad_code VARCHAR(512) NOT NULL,
  product_code VARCHAR(255) DEFAULT NULL,
  matched_product_code VARCHAR(128) DEFAULT NULL,
  product_id INT DEFAULT NULL,
  result_count INT NOT NULL DEFAULT 0,
  result_metric VARCHAR(128) DEFAULT NULL,
  spend_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  purchase_value_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  roas_purchase DECIMAL(12,6) DEFAULT NULL,
  raw_json JSON DEFAULT NULL,
  imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_meta_daily_ad_product (product_id, report_date),
  KEY idx_meta_daily_ad_report_date (report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Meta daily ad metrics';

ALTER TABLE meta_ad_daily_campaign_metrics
  ADD COLUMN meta_business_date DATE DEFAULT NULL COMMENT 'Meta report day, Beijing 16:00 cutover',
  ADD COLUMN meta_window_start_at DATETIME DEFAULT NULL,
  ADD COLUMN meta_window_end_at DATETIME DEFAULT NULL,
  ADD COLUMN attribution_timezone VARCHAR(64) DEFAULT 'Asia/Shanghai',
  ADD KEY idx_meta_daily_campaign_business_date (meta_business_date);

ALTER TABLE meta_ad_daily_ad_metrics
  ADD COLUMN meta_business_date DATE DEFAULT NULL COMMENT 'Meta report day, Beijing 16:00 cutover',
  ADD COLUMN meta_window_start_at DATETIME DEFAULT NULL,
  ADD COLUMN meta_window_end_at DATETIME DEFAULT NULL,
  ADD COLUMN attribution_timezone VARCHAR(64) DEFAULT 'Asia/Shanghai',
  ADD KEY idx_meta_daily_ad_business_date (meta_business_date);
