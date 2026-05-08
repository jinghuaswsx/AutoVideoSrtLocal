# 数据分析时间对齐修复（2026-05-08）

## 背景

2026-05-08 13:00 左右，用户反馈 newjoyloo 新广告户 20 分钟同步后在「数据分析 → 实时大盘」看不到数据。生产排查显示实时同步任务已成功写入 `meta_ad_realtime_daily_campaign_metrics` 与 `roi_realtime_daily_snapshots`，但页面在部分时间/筛选组合下仍可能显示 0。

根因不是同步任务完全无数据，而是几个时间口径和数据源选择不一致：

- 实时大盘带 `product_id` 时跳过 `roi_realtime_daily_snapshots`，并回退到 `meta_ad_daily_campaign_metrics` 日终表；当前 Meta 业务日尚未日终同步时，广告费会显示 0。
- 当前 Meta 业务日按北京时间 16:00 切日；北京时间 16:00 前的“今天”仍是前一个 Meta 业务日。页面或接口默认值不能使用北京时间自然日直接代表业务日。
- 产品盈亏 / 订单利润看板的时间范围查询应以已落库的业务日期字段为准，避免 `DATE(order_paid_at)` 把 16:00 后订单归到自然日。

## 修复目标

1. 实时大盘单日当前业务日带产品筛选时，广告费必须使用最新 realtime campaign snapshot，并按 campaign 归属产品过滤。
2. 实时大盘单日当前业务日带产品筛选时，订单侧必须按 `meta_business_date = target` 且 `order_time <= snapshot_at` 截止，不能用日终表或整日窗口冒充实时。
3. 产品盈亏相关页面和接口的默认日期使用 `current_meta_business_date()`，而不是 `date.today()` / 浏览器自然日。
4. 订单利润看板涉及时间范围过滤时，以 `order_profit_lines.business_date` 为主，不再用 `DATE(dianxiaomi_order_lines.order_paid_at)`。
5. 文档和前端提示统一写“北京时间 16:00 切日”。
6. 订单利润的日级广告费分摊函数把入参 `business_date` 解释为 Meta 业务日：订单销量按 `dianxiaomi_order_lines.meta_business_date` 查，广告费按 `COALESCE(meta_business_date, report_date)` 查。
7. 成本完备性看板的近 N 天影响范围也按当前 Meta 业务日倒推，并用 `dianxiaomi_order_lines.meta_business_date` 过滤。
8. 刚过北京时间 16:00 后选择“昨天”时，如果该刚关闭业务日的日终广告表尚未生成，应继续使用该业务日最后一个实时快照兜底，避免广告花费临时显示 0。
9. 订单利润核算页在所选业务日期缺少 `meta_ad_daily_campaign_metrics` 日终行、但已有 `meta_ad_realtime_daily_campaign_metrics` 快照时，订单列表和汇总应按最新快照的 campaign spend 临时分摊广告费，不能把广告费展示为 0。
10. 实时快照里已匹配 product、但该 product 在所选业务日没有可分摊订单 units 的广告费，应作为“未分摊广告费”扣入订单利润总利润，不能既不进订单分摊、也不进待处理广告成本。

## 非目标

- 不在本修复里实现广告 ad/adset/广告语层的实时入库和展示；当前只修 campaign 级 realtime 数据被产品筛选漏用的问题。
- 不调整 Meta 多账户同步调度频率、浏览器导出脚本和 systemd timer。
- 不改利润公式、成本估算比例、Shopify fee 计算逻辑。

## 验收

- 2026-05-08 13:20 这类北京时间 16:00 前场景，默认“今天”应解析到 `2026-05-07` Meta 业务日。
- `/order-analytics/realtime-overview?start_date=<当前业务日>&end_date=<当前业务日>&product_id=<id>` 能从 `meta_ad_realtime_daily_campaign_metrics` 最新 snapshot 取到匹配产品的广告费。
- 北京时间刚过 16:00 后，`start_date=end_date=<上一 Meta 业务日>` 且日终广告表无行时，实时大盘仍能从 `roi_realtime_daily_snapshots` 取到上一业务日广告费。
- `/order-analytics/product-profit/*` 默认日期范围的 `date_to` 应为当前 Meta 业务日。
- `/order-profit/api/orders` 和 summary 类接口按 `order_profit_lines.business_date` 过滤。
- `/order-profit` 点“昨天”后，如果该业务日只有实时广告快照，`/order-profit/api/orders` 的订单广告费与利润、`/order-profit/api/summary` 的广告成本与总利润都应使用实时快照兜底后的值；预览页面不得继续显示广告费 0。
- `/order-profit` 汇总里“广告费分摊 + 未分摊广告费”应覆盖实时快照总广告费；未分摊广告费包括未匹配 product，以及已匹配 product 但当天没有可分摊订单 units 的 spend。

## Docs-anchor

- 本文件
- 相关设计：`docs/superpowers/specs/2026-05-02-realtime-dashboard-redesign.md`
- 相关设计：`docs/superpowers/specs/2026-05-07-product-profit-dashboard-tabs-redesign-design.md`
- 相关设计：`docs/superpowers/specs/2026-05-07-order-profit-detail-tab-design.md`
