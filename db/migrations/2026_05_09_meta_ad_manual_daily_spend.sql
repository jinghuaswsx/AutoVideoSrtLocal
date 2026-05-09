-- Meta 广告费人工录入兜底表（admin 在「广告分析 → 人工录入」sub-tab 录入）。
--
-- 详细设计：docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md
--
-- 兜底语义：当某 (business_date, ad_account_id) 的 sync ad spend sum == 0 时，
-- 把这里的 spend_usd 加到 order_profit_aggregation 的 `unallocated` 桶，
-- 让"总利润"KPI 兜底不虚高。任何 sync > 0 时本表数据完全不参与计算。

CREATE TABLE IF NOT EXISTS meta_ad_manual_daily_spend (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  business_date DATE          NOT NULL,
  account_code  VARCHAR(64)   NOT NULL,
  ad_account_id VARCHAR(32)   NOT NULL,
  spend_usd     DECIMAL(14,4) NOT NULL,
  updated_by    INT NULL,
  updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_date_account (business_date, account_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
