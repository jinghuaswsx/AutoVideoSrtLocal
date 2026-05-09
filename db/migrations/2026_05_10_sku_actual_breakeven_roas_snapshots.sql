-- 2026-05-10: SKU actual breakeven ROAS daily snapshots
-- Docs-anchor: docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md

CREATE TABLE IF NOT EXISTS sku_actual_breakeven_roas_snapshots (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  sku VARCHAR(128) NOT NULL,
  window_start DATE NOT NULL,
  window_end DATE NOT NULL,
  orders_count INT NOT NULL DEFAULT 0,
  units INT NOT NULL DEFAULT 0,
  revenue_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  purchase_cost_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  shipping_cost_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  shopify_fee_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  fee_source ENUM('real','estimated_7pct','mixed') NOT NULL DEFAULT 'estimated_7pct',
  actual_breakeven_roas DECIMAL(12,4) NULL,
  computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source_run_id BIGINT NULL,
  summary_json JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_sku_actual_roas_window (sku, window_start, window_end),
  KEY idx_sku_actual_roas_latest (sku, computed_at),
  KEY idx_sku_actual_roas_window (window_start, window_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SKU rolling-window actual breakeven ROAS snapshots';
