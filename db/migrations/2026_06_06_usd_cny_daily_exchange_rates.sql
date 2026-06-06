-- 2026-06-06: USD/CNY daily baseline exchange-rate archive.
-- Docs-anchor: docs/superpowers/specs/2026-06-06-usd-cny-daily-exchange-rate-design.md

CREATE TABLE IF NOT EXISTS usd_cny_daily_exchange_rates (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  rate_date DATE NOT NULL,
  usd_to_cny DECIMAL(12,6) NOT NULL,

  primary_source VARCHAR(64) NOT NULL,
  primary_rate DECIMAL(12,6) NOT NULL,
  primary_source_date DATE DEFAULT NULL,

  validator_quotes_json JSON NOT NULL,

  max_relative_diff_ratio DECIMAL(12,8) NOT NULL,
  tolerance_ratio DECIMAL(12,8) NOT NULL DEFAULT 0.05000000,
  source_payload_json JSON DEFAULT NULL,
  source_run_id BIGINT DEFAULT NULL,

  synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  UNIQUE KEY uk_usd_cny_rate_date (rate_date),
  KEY idx_usd_cny_synced_at (synced_at),
  KEY idx_usd_cny_source_run (source_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Validated daily USD/CNY baseline exchange rates';
