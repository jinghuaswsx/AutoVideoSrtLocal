# 订单利润总览异常卡片交互设计

## 背景

`/order-profit` 总览页已有“未分摊广告费”和“不完备 SKU 行”两张告警卡片。当前“未分摊广告费”读取最近一次利润回填 run 的汇总值，不随顶部日期范围变化；“不完备 SKU 行”只显示行数，不能直接定位哪些产品需要补成本。

## 需求

1. “未分摊广告费”必须显示顶部当前时间范围内的未匹配广告费。
   - 口径：`meta_ad_daily_campaign_metrics.product_id IS NULL` 且 `report_date BETWEEN from AND to` 的 `spend_usd` 汇总。
   - 前端文案必须说明这是“当前时间范围”的金额，不能再写“最近一次跑”。
2. 点击“不完备 SKU 行”卡片后，页面直接弹出当前时间范围内所有问题产品。
   - 问题产品来自 `order_profit_lines.status='incomplete'`，按 `business_date BETWEEN from AND to` 过滤。
   - 弹窗每项显示 `中文名 - product_code`，中文名来自 `media_products.name`，无名称时显示“未命名产品”。
   - 同一产品多条不完备 SKU 行只显示一项，并展示 SKU 行数与缺失字段摘要。
   - 点击产品项跳转素材管理搜索页：`/medias/?q={{product_code}}`。
3. 弹窗为空时显示“当前时间范围内没有不完备产品”。

## 实现边界

- 不改利润核算公式、回填脚本、定时任务和数据库结构。
- 不改变订单明细、亏损订单、Campaign 配对已有接口契约。
- 新增后端聚合 helper 和 `/order-profit` 轻量 API，路由只负责参数解析和响应包装。
- 前端沿用 Ocean Blue Admin token，不引入紫色、营销式装饰或新依赖。

## 验收

- `/order-profit/api/summary?from=2026-05-01&to=2026-05-03` 返回的 `unallocated_ad_spend_usd` 来自同一日期范围内的未匹配 campaign spend。
- `/order-profit/api/incomplete_products?from=2026-05-01&to=2026-05-03` 返回去重后的问题产品列表，包含 `product_name`、`product_code`、`line_count`、`missing_fields`、`medias_search_url`。
- 不完备卡片可键盘聚焦并点击打开弹窗；Esc、关闭按钮、遮罩点击都能关闭。
- 点击弹窗产品链接打开 `/medias/?q=<product_code>`。
- 覆盖聚合层、路由和模板资产测试。
