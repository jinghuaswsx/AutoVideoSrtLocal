-- 2026-05-07 产品采购价快照
-- 在 dianxiaomi_order_lines 加两列：订单付款时的采购价快照（CNY）+ 填充时间。
-- 业务方需求：media_products.purchase_price 会变，已有订单要"冻结"成本，避免历史利润被回溯重算。
-- 取数策略：calculate_line_profit / order_profit_backfill 用 COALESCE(d.purchase_price_cny, m.purchase_price)，
--           snapshot 列已填值就用快照，NULL 就 fallback 到 media_products 当前值。
-- 填充时机：upsert_dianxiaomi_order_lines 末尾自动跑一次 UPDATE，把当前 batch 涉及的 NULL 行填上。
--           额外提供 scripts/backfill_dianxiaomi_purchase_price_snapshot.py 给管理员一次性把存量订单 NULL 列填上。

ALTER TABLE dianxiaomi_order_lines
  ADD COLUMN purchase_price_cny DECIMAL(10,2) NULL
    COMMENT '订单付款时的采购价快照（CNY），从 media_products.purchase_price 复制；NULL 时 fallback 到 media_products 当前值',
  ADD COLUMN purchase_price_at DATETIME NULL
    COMMENT '采购价快照填充时间';

-- 索引：方便 backfill 任务定位"还没填充"的行
CREATE INDEX idx_dianxiaomi_order_lines_purchase_price_at
  ON dianxiaomi_order_lines (purchase_price_at);
