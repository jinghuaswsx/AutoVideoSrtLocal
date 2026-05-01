CREATE TABLE IF NOT EXISTS weekly_roas_report_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  week_start_date DATE NOT NULL COMMENT 'ISO week Monday (meta_business_date)',
  week_end_date DATE NOT NULL COMMENT 'ISO week Sunday (last meta_business_date in scope)',
  generated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  generated_by VARCHAR(64) NOT NULL DEFAULT 'scheduler',
  summary_json JSON NOT NULL,
  rows_json JSON NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_week_start (week_start_date),
  KEY idx_generated_at (generated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Weekly meta-vs-true ROAS comparison snapshots';
