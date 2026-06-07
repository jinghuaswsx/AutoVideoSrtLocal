-- Product stability classification cache for media product list and weekly AI report.
-- Docs anchor: docs/superpowers/specs/2026-06-07-weekly-ai-analysis-report-design.md#产品稳定分级2026-06-07-追加

CREATE TABLE IF NOT EXISTS media_product_stability_snapshots (
  product_id BIGINT NOT NULL PRIMARY KEY,
  product_code VARCHAR(255) NOT NULL DEFAULT '',
  product_name VARCHAR(255) NOT NULL DEFAULT '',
  status VARCHAR(32) NOT NULL DEFAULT 'never',
  display_label VARCHAR(64) NOT NULL DEFAULT '未投放',
  stable_7d TINYINT(1) NOT NULL DEFAULT 0,
  stable_30d TINYINT(1) NOT NULL DEFAULT 0,
  last_7d_orders INT NOT NULL DEFAULT 0,
  last_30d_orders INT NOT NULL DEFAULT 0,
  avg_7d_orders DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  avg_30d_orders DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  min_daily_orders_7d INT NOT NULL DEFAULT 0,
  min_daily_orders_30d INT NOT NULL DEFAULT 0,
  active_7d_ad_spend_usd DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  total_ad_spend_usd DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  overall_roas DECIMAL(10,4) DEFAULT NULL,
  delivery_status VARCHAR(32) NOT NULL DEFAULT 'never',
  computed_for_date DATE NOT NULL,
  computed_at DATETIME NOT NULL,
  details_json JSON DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_media_product_stability_status (status),
  KEY idx_media_product_stability_7d (stable_7d),
  KEY idx_media_product_stability_30d (stable_30d),
  KEY idx_media_product_stability_computed_at (computed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
