# 实时大盘 ROAS 走势当天小时订单修复

## 背景

`/order-analytics -> 实时大盘 -> ROAS 走势` 的「当日节点记录」表用于查看一天 24 小时的订单数、销售额、广告费和 ROAS。历史日走明细聚合时会返回 24 条 `hourly`；当天全量实时大盘命中 `roi_realtime_daily_snapshots` 时，后端返回 `hourly: []` 和一条 `snapshots`，前端因此只展示一行快照汇总。

相关锚点：

- `appcore/order_analytics/CLAUDE.md`：实时大盘业务日、店铺筛选、预聚合表使用边界。
- `docs/superpowers/specs/2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md`：当天快照选源必须优先真实最新 partial，且过滤未来 `snapshot_at`。
- `docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md`：ROAS 走势 scope 模式不得冒充全量节点；必要时后端按明细聚合节点。
- `docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md`：ROI / 订单 / 日内广告每 20 分钟同步。

## 要求

1. 当天全量实时大盘仍优先使用 `roi_realtime_daily_snapshots` 作为 KPI 汇总和广告费选源。
2. 当天命中快照分支时，`ROAS 走势 -> 当日节点记录` 必须仍返回 24 条 `hourly`，每小时订单数使用店小秘订单明细聚合。
3. 小时订单口径保持现有明细分支一致：按 Meta 业务日窗口内 `COALESCE(order_paid_at, attribution_time_at, order_created_at)` 分组，`COUNT(DISTINCT dxm_package_id)` 计订单数。
4. 小时行必须按 Meta 业务日窗口的相对小时分组：`00:00-01:00` 代表 `day_start_at` 到 `day_start_at + 1h`，不是北京时间自然日 00:00 到 01:00。对于 16:00 切日的当前业务日，昨天 16:00-17:00 的订单应进入第 0 小时行，不能显示在表格下半段造成“未来小时已有数据”的误解。
5. 本次不拆分小时广告费。快照分支的 `hourly[*].ad_spend` 和 `hourly[*].true_roas` 可保持空值；图表仍使用 `roi_daily_roas_nodes` / `roas_points`。
6. 单店 / 产品筛选 / 新品老品 scope 继续走既有明细路径，不读取双店全量预聚合快照。

## 设计

在 `appcore/order_analytics/realtime.py` 中抽出一个复用 helper：

- 查询当前 Meta 业务日窗口内的店小秘订单明细。
- 支持现有 `product_id` / `product_ids` / `unmatched_ads` / `site_codes` 过滤。
- 返回 `orders_by_hour` 和 24 条 `hourly`，并可累加订单侧 summary 字段。

`get_realtime_roas_overview` 的快照分支在返回前调用该 helper：

- `summary` 继续来自 `roi_realtime_daily_snapshots`，不被小时明细重算覆盖。
- `hourly` 来自订单明细 24 小时聚合。
- `period.data_until_at`、`snapshots`、`campaigns`、`order_profit_summary` 等保持原行为。

## 验证

- 新增回归测试：当天命中 `roi_realtime_daily_snapshots` 时，响应仍返回 `len(hourly) == 24`，指定小时的 `order_count` 来自明细聚合，`summary.order_count` 仍来自快照。
- 运行 `pytest tests/test_order_analytics_true_roas.py -q`。
