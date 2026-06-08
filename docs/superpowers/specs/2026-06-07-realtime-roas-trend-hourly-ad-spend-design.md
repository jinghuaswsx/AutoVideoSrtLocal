# 实时大盘 ROAS 走势小时广告费补齐

## 背景

`/order-analytics -> 实时大盘 -> ROAS 走势` 的「当日节点记录」已经能按 Meta 业务日相对小时展示订单数、商品销售额、运费和总销售额，但广告费与 ROAS 仍显示为空。上一版 [2026-06-06-realtime-roas-trend-today-hourly-orders-design.md](2026-06-06-realtime-roas-trend-today-hourly-orders-design.md) 第 6 条明确当时不拆分小时广告费；本次按用户新要求补齐这两列。

相关锚点：

- `appcore/order_analytics/CLAUDE.md`：实时大盘业务日、店铺筛选、预聚合表使用边界。
- [2026-05-08-analytics-business-date-alignment-fix.md](2026-05-08-analytics-business-date-alignment-fix.md)：实时广告费读取必须按 `(business_date, ad_account_id)` 各自最新快照汇总，不能用全局 `MAX(snapshot_at)`。
- [2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md](2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md)：未收盘业务日优先使用真实最新 partial，过滤未来 `snapshot_at`。
- [2026-06-06-realtime-roas-trend-today-hourly-orders-design.md](2026-06-06-realtime-roas-trend-today-hourly-orders-design.md)：小时行按 Meta 业务日相对小时展示，并带北京时间 / Europe-Berlin 窗口。

## 要求

1. 「当日节点记录」每小时行必须展示该小时广告费和该小时 ROAS；前端已有字段 `hourly[*].ad_spend` / `hourly[*].true_roas`，后端补值即可。
2. 小时 ROAS 口径为：`(hourly.order_revenue + hourly.shipping_revenue) / hourly.ad_spend`。其中分子是该小时总销售额，沿用数据分析模块统一口径 `line_amount + ship_amount`。
3. 小时广告费从 Meta 实时 campaign 累计快照拆分：`小时广告费 = 小时结束水位累计广告费 - 小时开始水位累计广告费`。
4. 累计广告费水位必须按广告账户分别取 `snapshot_at <= cutoff` 的最新快照后合并，不能用全局最大 `snapshot_at`。
5. 当前未完整小时按“截至当前数据水位”的小时内累计值展示；未来小时没有广告水位时保持空值。
6. 单店、产品筛选、新品 / 老品 / 未匹配 scope 必须沿用现有实时 campaign 过滤规则，不能把全量广告费写入 scoped 小时行。
7. 不改表结构、不新增同步任务、不改变顶部 KPI 汇总选源；`roi_realtime_daily_snapshots` 分支仍作为 KPI 汇总快路径。
8. 2026-06-08 补充：历史单日如果已经生成 `meta_ad_daily_campaign_metrics` 日终数据，接口会进入 daily 分支；此时「当日节点记录」不能因为不走 realtime 分支而让小时广告费 / ROAS 为空。小时广告费按分层数据源兜底：
   - 第一层：`roi_daily_roas_nodes`，按相邻节点的累计 `ad_spend_usd` 做差分，得到每小时广告费；这是闭合日已有节点时的优先来源。
   - 第二层：`meta_ad_realtime_daily_campaign_metrics`，按实时 campaign 累计水位做小时差分，沿用现有 per-account 最新快照规则。
   - 第三层：`meta_ad_daily_campaign_metrics` 日终表只作为顶部 KPI / summary 的日级兜底；它没有小时水位，不能强行平均或按订单占比摊到小时行，避免制造假小时数据。

## 设计

在 `appcore/order_analytics/realtime.py` 增加一个小时广告费填充 helper：

- 输入 `target`、`day_start`、`data_until`、24 条 `hourly` 订单行，以及现有 `product_id` / `product_ids` / `unmatched_ads` / `site_codes` 过滤参数。
- 读取 `meta_ad_realtime_daily_campaign_metrics` 在 `business_date=target` 且 `snapshot_at <= data_until` 的实时 campaign 行，复用 `_get_realtime_campaign_rows_until()` 的店铺到账户过滤。
- 对每个小时窗口计算 `start_cutoff=window_start_at`、`end_cutoff=min(window_end_at, data_until)`；`window_start_at >= data_until` 的未来小时不补广告费。
- 对 `start_cutoff` 和 `end_cutoff` 分别按 `ad_account_id` 找最新快照并按 campaign 过滤后求累计 spend，再做差值。
- 差值小于 0 时按 0 处理，避免异常回写或广告平台校正导致小时行出现负广告费。
- `hourly[*].ad_spend` 写入小时广告费；`hourly[*].true_roas` 用 `_roas(_revenue_with_shipping(order_revenue, shipping_revenue), ad_spend)` 计算。
- 当 `roas_points` 来自 `roi_daily_roas_nodes` 且存在节点时，先用节点累计广告费差分填 `hourly`；只有缺少可用节点时才调用实时 campaign 水位 helper。

## 验证

- 新增回归测试：默认双店快照分支中，小时 0 的订单销售额为 `$440`，广告累计快照从 `$0` 到 `$110`，返回 `ad_spend=110`、`true_roas=4.0`。
- 覆盖多账户水位：同一小时内两个账户的最新快照不同时，小时广告费分别按各账户水位求差后相加。
- 覆盖连续小时差分：后一小时必须用该小时结束水位减该小时开始水位，不能把当天累计广告费直接写入小时行。
- 新增回归测试：昨天 / 闭合日已经存在 `meta_ad_daily_campaign_metrics` 日终行时，单日接口仍用 `roi_daily_roas_nodes` 差分填 `hourly[*].ad_spend` 和 `hourly[*].true_roas`，避免表格列显示 `-`。
- 运行：

```bash
pytest tests/test_order_analytics_true_roas.py -q
```
