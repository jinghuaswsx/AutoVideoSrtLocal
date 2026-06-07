CREATE TABLE IF NOT EXISTS weekly_ai_analysis_reports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  week_start_date DATE NOT NULL COMMENT 'Business week Sunday (meta_business_date)',
  week_end_date DATE NOT NULL COMMENT 'Business week Saturday (last meta_business_date in scope)',
  generated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  generated_by VARCHAR(64) NOT NULL DEFAULT 'manual',
  status VARCHAR(32) NOT NULL DEFAULT 'success',
  data_snapshot_json JSON NOT NULL,
  ai_report_json JSON NULL,
  raw_text MEDIUMTEXT NULL,
  data_quality_json JSON NULL,
  usage_log_id BIGINT UNSIGNED NULL,
  error_message MEDIUMTEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_weekly_ai_week_start (week_start_date),
  KEY idx_weekly_ai_generated_at (generated_at),
  KEY idx_weekly_ai_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Weekly AI business analysis reports';
