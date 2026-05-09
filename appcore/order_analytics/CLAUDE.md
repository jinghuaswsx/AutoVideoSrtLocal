# appcore/order_analytics/

订单 / 广告盈亏分析模块。涉及实时大盘、产品盈亏报表、Meta 广告费分摊、业务日窗口对齐。

## 关键 specs（按需读）
- 实时大盘改版：`docs/superpowers/specs/2026-05-02-realtime-dashboard-redesign.md`
- 业务日对齐：`docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md`
- 实时大盘店铺筛选：`docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md`
- 数据质量护栏：`docs/analytics-data-quality-guardrails.md`

## 实时大盘业务日 + 广告费分摊（硬规则）

**Meta 实时表 fallback 必须按 `(business_date, ad_account_id)` 取最新 snapshot**，再合并各账户结果。**不允许**用 `GROUP BY business_date` 取全局 `MAX(snapshot_at)` 来代表当日——某账户某轮 tick 失败就会让它整账户消耗被静默丢弃。

> **2026-05-08 事故**：newjoyloo_bak 浏览器导出连续 600s timeout，全局 `MAX(snapshot_at)` 取到 Omurio 17:00，order-profit「已分摊广告费」整列读到 $0。

应用范围：`order_profit_aggregation.py::_load_realtime_ad_snapshot_fallback` + `realtime.py::_get_today_realtime_meta_totals`。新增同类查询要走同款分组。

## 实时大盘订单盈亏（日终未到的兜底）

`/order-analytics → 实时大盘 → 订单盈亏明细` 与 `order_profit_summary.ad_cost_usd` 不允许在「Meta 实时表已有数据但日终未生成」窗口里恒为 0：

- `realtime.py::_get_realtime_order_profit_details(_for_range)` 必须调一遍 `_apply_realtime_ad_cost_adjustments`（共用 `order_profit_aggregation::_load_realtime_ad_cost_adjustments`），按 package id 加 delta，把每行 `ad_cost_usd` 抬到实时分摊值，并同步下调 `order_profit_usd` / `order_profit_with_estimate_usd`。
- 与 `order_profit_aggregation.get_order_profit_list` 的兜底口径完全对齐。

## 店铺筛选

- `/order-analytics/realtime-overview` 接受可选 `site_code=newjoy|omurio`；不传 = 全部店铺。白名单来自 `appcore/meta_ad_accounts.AVAILABLE_STORE_CODES`，不能随手扩展。
- 后端走 `_normalize_site_codes` + `_site_codes_in_sql` 两个统一入口。**不要**在新代码里直接拼 `site_code IN ('newjoy', 'omurio')` 字面量。
- 单店 / 局部店铺筛选必须**绕过** `roi_realtime_daily_snapshots` / `roi_daily_roas_nodes` 这两张 `store_scope='newjoy,omurio'` 的预聚合表，回落到明细路径。已通过 `_should_try_realtime_snapshot` 与 `site_filter_active` 短路；新增同类查询带上同款判断。
- 单店筛选下广告 / campaign 数据按 `meta_ad_accounts.site_account_map` 翻译为 `ad_account_id IN (...)`。**不要**新增硬编码 `site_code -> account_id` 常量。
- KPI「总利润额」下方利润率字段 `profit_with_estimate_margin_pct`：spec `docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md`。改 KPI 卡 markup / `renderRealtimeOrderProfitSummary` / `_build_order_profit_summary*` 链路时同步看该 spec；`_empty_order_profit_summary` 把该字段默认 `None`，所以 `_build_order_profit_summary*` 的 rounding 循环加了 `if value is None: continue` 守卫，不要回滚。

## 广告费分摊 / 多账户

产品盈亏广告费分摊必须从 `meta_ad_accounts.store_codes` 生成店铺到账户映射。这里使用**所有已配置账户（包括 `enabled=false` 的历史账户）**，因为暂停同步不代表历史广告数据失效。

`appcore/order_analytics/meta_ads.fill_purchase_value_from_orders`（在 `get_ads_level_list / get_ads_level_detail` 出口）对**整组** `(ad_account_id, matched_product_code)` 满足 `SUM(purchase_value_usd) == 0 AND SUM(spend_usd) > 0` 的行套用站内同产品营收按 spend 占比分摊。命中兜底的行 `purchase_value_source = "order_fallback"`，否则 `"meta"`；API 顶层 `data_quality.status` 标 `"fallback_used"`。决策面只在**整组 0** 时才触发，避免污染老户合理 0。

## 数据质量护栏

所有 `/order-profit/*`、`/order-analytics/realtime-overview`、`/order-analytics/product-profit/*` JSON 顶层必须带 `data_quality`；前端缺失时按 `unknown` 处理，不要默认 `ok`。后端校验集中在 [data_quality.py](data_quality.py)，**禁止**在 route 或模板里散开。定时巡检 `analytics_data_quality_inspection` 已登记到 `appcore/scheduled_tasks.py`；新增类似巡检必须同步登记。

## 改动后必跑的测试

```bash
pytest tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_data_quality.py \
       tests/test_order_profit_aggregation.py \
       tests/test_order_analytics_ads.py \
       tests/test_product_profit_report.py \
       tests/characterization/test_order_analytics_baseline.py -q
```
