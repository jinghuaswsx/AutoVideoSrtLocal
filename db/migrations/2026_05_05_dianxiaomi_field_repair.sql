-- 2026-05-05: dianxiaomi_order_lines 字段修复（订单利润核算前置依赖）
--
-- 修复背景：店小秘 profit API 100% 返回 {"profit": "--"}，导致历史 15,244
-- 条订单的 logistic_fee / amount_cny 100% NULL。同步脚本已修为 fallback 到
-- raw_order_json 顶层（appcore/order_analytics/dianxiaomi.py:_compute_amount_cny
-- + _resolve_logistic_fee_cny），本 SQL 把历史数据一次性补上。
--
-- 幂等：UPDATE 用 WHERE IS NULL 过滤，重复执行不会覆盖新写入的值。
-- 详细 plan 见 docs/superpowers/plans/2026-05-04-order-profit-calculation.md 阶段 0.2。

-- Step 1: 回填 logistic_fee（CNY），从 raw_order_json 顶层 logisticFee 提取。
-- 预期 ~12,834 行有值，~16% 为 logisticFeeErr 跳过。
UPDATE dianxiaomi_order_lines
SET logistic_fee = ROUND(
    CAST(JSON_EXTRACT(raw_order_json, '$.logisticFee') AS DECIMAL(14,4)),
    2
)
WHERE logistic_fee IS NULL
  AND JSON_EXTRACT(raw_order_json, '$.logisticFee') IS NOT NULL
  AND JSON_TYPE(JSON_EXTRACT(raw_order_json, '$.logisticFee')) IN ('INTEGER', 'DOUBLE', 'DECIMAL');

-- Step 2: 回填 amount_cny，用 orderAmount(USD) × system_settings.material_roas_rmb_per_usd。
-- 取当前配置的汇率；缺失兜底 6.83。
SET @rmb_per_usd := IFNULL(
    (SELECT CAST(value AS DECIMAL(10,4)) FROM system_settings WHERE `key` = 'material_roas_rmb_per_usd' LIMIT 1),
    6.83
);

UPDATE dianxiaomi_order_lines
SET amount_cny = ROUND(order_amount * @rmb_per_usd, 2)
WHERE amount_cny IS NULL
  AND order_amount IS NOT NULL;
