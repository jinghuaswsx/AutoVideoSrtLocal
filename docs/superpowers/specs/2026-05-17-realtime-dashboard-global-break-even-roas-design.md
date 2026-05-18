# 实时大盘全局保本 ROAS（2026-05-17）

## 背景

数据分析实时大盘已经有 `order_profit_summary` 汇总口径，包含总销售额、退款/退货扣减、采购成本、物流成本、Shopify 手续费、已分摊广告费、未分摊广告费和含估算利润。用户需要在实时大盘增加一个数量项目“全局保本 ROAS”，表示当前所选业务日期或日期范围内，当 ROAS 达到多少时整体不亏不赚。

该指标必须跟随实时大盘的现有日期范围、产品筛选和店铺筛选生效，不新增独立查询入口。

## 指标定义

字段名：

- 后端 JSON：`order_profit_summary.global_break_even_roas`
- 前端展示：实时大盘顶部 KPI 卡片“全局保本 ROAS”

计算公式：

```text
global_break_even_roas =
  total_revenue_usd /
  (
    total_revenue_usd
    - profit_deduction_usd
    - purchase_cost_with_estimate_usd
    - logistics_cost_with_estimate_usd
    - shopify_fee_total_usd
  )
```

口径说明：

- `total_revenue_usd` 使用订单利润汇总里的总销售额。
- 分母表示扣除退款/退货、采购、物流、Shopify 手续费后，最多可承受的广告花费。
- 不扣除 `ad_cost_usd`、`unallocated_ad_spend_usd` 或 `total_ad_spend_usd`，因为本指标是在求盈亏平衡广告花费对应的 ROAS。
- 分母 `<= 0` 或 `total_revenue_usd <= 0` 时返回 `None`，前端显示 `-`。

## 取整规则

返回值保留三位小数，第四位小数只要存在非零尾数就向上取整。

示例：

- 原始值 `1.5370` -> `1.537`
- 原始值 `1.5371` -> `1.538`

实现使用 `Decimal.quantize(Decimal("0.001"), rounding=ROUND_CEILING)`，避免二进制浮点导致边界误差。

## 数据流

后端在 `appcore/order_analytics/realtime.py` 中扩展 `order_profit_summary`：

1. `_empty_order_profit_summary()` 默认带上 `"global_break_even_roas": None`。
2. `_build_order_profit_summary()` 在各金额字段完成汇总和 rounding 后，根据上述公式计算。
3. `_build_order_profit_summary_from_status()` 对 day-final 状态汇总 fallback 使用同一公式。
4. 单日实时、单日历史、日期范围分支都已经返回 `order_profit_summary`，因此新字段自然跟随 `start_date/end_date`、`product_id`、`site_code` 生效。

前端在 `web/templates/order_analytics.html` 中扩展实时大盘顶部 KPI：

1. 在“真实 ROAS / Meta ROAS / 利润”等顶部卡片区域新增“全局保本 ROAS”卡片。
2. `loadRealtimeTopCards()` 从 `data.order_profit_summary.global_break_even_roas` 读取。
3. 数值为 `null/undefined` 时显示 `-`；否则固定显示三位小数。

## 方案比较

推荐方案：复用 `order_profit_summary` 计算并展示。

- 优点：与利润汇总同源，日期范围和店铺/产品筛选自然生效；无需新增 SQL 或路由。
- 缺点：依赖订单利润汇总数据完整性，空利润汇总时只能显示 `-`。

备选方案一：在 `summary` 顶层直接计算。

- 优点：顶部 KPI 读取更短。
- 缺点：`summary` 当前偏 ROAS/广告汇总口径，缺少采购、物流、手续费等成本字段，会引入重复数据或额外查询。

备选方案二：新增独立 endpoint。

- 优点：接口边界独立。
- 缺点：会重复已有实时大盘过滤和数据质量链路，增加不一致风险。本次不采用。

## 测试

新增或扩展测试：

- 后端：`tests/test_order_analytics_realtime_break_even_roas.py`
  - 正常利润空间计算为三位小数。
  - `1.5371` 向上到 `1.538`。
  - `1.5370` 保持 `1.537`。
  - 分母 `<= 0` 或销售额为 0 返回 `None`。
  - `_build_order_profit_summary_from_status()` fallback 也返回同字段。
- 前端静态测试：`tests/test_order_analytics_template_layout.py`
  - 模板包含 `realtimeGlobalBreakEvenRoas` KPI 节点。
  - JS 读取 `global_break_even_roas` 并用三位小数展示。

回归测试：

```bash
pytest tests/test_order_analytics_realtime_break_even_roas.py \
       tests/test_order_analytics_realtime_profit_margin.py \
       tests/test_order_analytics_template_layout.py \
       tests/test_order_analytics_realtime_profit_details.py \
       tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_responses_service.py \
       tests/test_order_analytics_dashboard.py \
       tests/characterization/test_order_analytics_baseline.py \
       -q
```

## 非目标

- 不修改 SKU 实际保本 ROAS 快照任务。
- 不改变现有真实 ROAS / Meta ROAS 的计算口径。
- 不新增数据库表或迁移。
- 不改变广告费分摊逻辑。
