-- 2026-06-07: USD/CNY dynamic fallback exchange-rate archive.
-- Docs-anchor: docs/superpowers/specs/2026-06-06-usd-cny-daily-exchange-rate-design.md

CREATE TABLE IF NOT EXISTS usd_cny_fallback_exchange_rates (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  fallback_date DATE NOT NULL,
  usd_to_cny DECIMAL(12,6) NOT NULL,

  window_start DATE NOT NULL,
  window_end DATE NOT NULL,
  sample_count INT NOT NULL,
  source_rate_ids_json JSON NOT NULL,
  calculation_method VARCHAR(64) NOT NULL DEFAULT 'daily_archive_30d_average',
  source_run_id BIGINT DEFAULT NULL,

  synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  UNIQUE KEY uk_usd_cny_fallback_date (fallback_date),
  KEY idx_usd_cny_fallback_synced_at (synced_at),
  KEY idx_usd_cny_fallback_source_run (source_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Dynamic USD/CNY fallback rates from recent daily archives';
