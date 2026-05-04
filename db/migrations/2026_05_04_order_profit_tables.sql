-- 2026-05-04: 订单利润核算 + Shopify Payments CSV 校验
--
-- 详细 plan: docs/superpowers/plans/2026-05-04-order-profit-calculation.md
-- 规则文档:  docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md

-- =====================================================================
-- 1. order_profit_lines: SKU 行级利润核算结果
-- =====================================================================
CREATE TABLE IF NOT EXISTS order_profit_lines (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  dxm_order_line_id BIGINT NOT NULL,
  product_id INT,
  business_date DATE NOT NULL,
  paid_at DATETIME,

  -- 上下文
  buyer_country VARCHAR(8),
  presentment_currency VARCHAR(8),
  shopify_tier VARCHAR(16),

  -- 收入侧
  line_amount_usd DECIMAL(12,4),
  shipping_allocated_usd DECIMAL(12,4),
  revenue_usd DECIMAL(12,4),

  -- 成本侧
  shopify_fee_usd DECIMAL(12,4),
  ad_cost_usd DECIMAL(12,4),
  purchase_usd DECIMAL(12,4),
  shipping_cost_usd DECIMAL(12,4),
  return_reserve_usd DECIMAL(12,4),

  -- 结果
  profit_usd DECIMAL(12,4),
  status VARCHAR(16) NOT NULL,                   -- 'ok' | 'incomplete' | 'error'
  missing_fields JSON,
  cost_basis JSON,                                -- 计算时的快照（汇率、采购价、用 actual/estimated 等）

  computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source_run_id BIGINT,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  UNIQUE KEY uk_profit_line_dxm (dxm_order_line_id),
  KEY idx_profit_business_date (business_date),
  KEY idx_profit_product_status (product_id, status),
  KEY idx_profit_buyer_country (buyer_country),
  KEY idx_profit_status (status),
  KEY idx_profit_paid_at (paid_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SKU 行级订单利润核算结果';

-- =====================================================================
-- 2. order_profit_runs: 利润核算任务运行记录
-- =====================================================================
CREATE TABLE IF NOT EXISTS order_profit_runs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_code VARCHAR(64) NOT NULL,                -- 'backfill' | 'incremental' | 'manual'
  status ENUM('running','success','failed','partial') NOT NULL DEFAULT 'running',
  window_start_at DATETIME,
  window_end_at DATETIME,
  rmb_per_usd DECIMAL(10,4),
  return_reserve_rate DECIMAL(6,4),

  lines_total INT DEFAULT 0,
  lines_ok INT DEFAULT 0,
  lines_incomplete INT DEFAULT 0,
  lines_error INT DEFAULT 0,
  unallocated_ad_spend_usd DECIMAL(14,4) DEFAULT 0,

  error_message MEDIUMTEXT,
  summary_json JSON,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME,

  KEY idx_profit_runs_started (started_at),
  KEY idx_profit_runs_status (status),
  KEY idx_profit_runs_task (task_code, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Order profit calculation task runs';

-- =====================================================================
-- 3. shopify_payments_transactions: 阶段 9 校验回路（CSV 导入）
-- =====================================================================
CREATE TABLE IF NOT EXISTS shopify_payments_transactions (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  payout_id VARCHAR(64),
  transaction_id VARCHAR(64) NOT NULL,
  type VARCHAR(32),                               -- 'charge' | 'refund' | 'chargeback' | ...
  order_name VARCHAR(64),                         -- Shopify order name (#1234)
  presentment_currency VARCHAR(8),
  amount_usd DECIMAL(12,4),
  fee_usd DECIMAL(12,4),
  net_usd DECIMAL(12,4),
  card_brand VARCHAR(32),

  -- 反推结果（verify_fee 计算）
  inferred_card_origin VARCHAR(16),               -- 'domestic' | 'international' | 'unknown'
  inferred_tier VARCHAR(8),                       -- A/B/C/D
  matches_standard TINYINT,

  imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source_csv VARCHAR(255),
  raw_row_json JSON,

  UNIQUE KEY uk_shopify_payments_txn (transaction_id),
  KEY idx_shopify_payments_order (order_name),
  KEY idx_shopify_payments_imported (imported_at),
  KEY idx_shopify_payments_payout (payout_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Shopify Payments transactions imported from CSV for fee reconciliation';

-- =====================================================================
-- 4. order_profit_recompute_queue: 完备性变化触发重算队列
-- =====================================================================
CREATE TABLE IF NOT EXISTS order_profit_recompute_queue (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  reason VARCHAR(128) NOT NULL,                   -- 'cost_updated' | 'manual'
  lookback_days INT NOT NULL DEFAULT 90,
  enqueued_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  processed_at DATETIME,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- 'pending' | 'processing' | 'done' | 'failed'

  KEY idx_recompute_status (status, enqueued_at),
  KEY idx_recompute_product (product_id, enqueued_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Trigger queue: recompute profit for product after cost field update';
