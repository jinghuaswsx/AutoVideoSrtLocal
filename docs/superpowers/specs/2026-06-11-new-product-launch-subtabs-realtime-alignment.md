# 新品投放分析子 Tab 与实时大盘对齐

## 背景

`docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md` 已定义「新品投放分析」复用实时大盘的指标和明细结构，只把数据范围限定为新品、老品或未匹配产品。当前前端仍有一套较早的简化渲染：产品销量第一列还是单行拼接，订单商品列没有实时大盘的中英文分层，订单/盈亏明细没有同款分页加载，广告计划缺少未分摊筛选条。

## 目标

1. 「新品投放分析」下的 `订单明细 / 订单盈亏明细 / 产品销量 / 广告计划 / ROAS 走势` 与「实时大盘」对应子 Tab 的前端展示、空态、加载态和交互保持一致。
2. 数据请求继续走 `/order-analytics/realtime-overview`，唯一数据范围差异是固定传入 `product_launch_scope=new|old|unmatched` 与 `product_launch_window_days`。
3. 产品销量列复用实时大盘已实现的名称/code 两行展示、复制按钮和 `/medias/?q=<product_code>` 搜索入口。
4. 新品投放分析不新增后端专用接口，不复制实时大盘业务逻辑。

## 设计

- 前端保留 NPL 独立 DOM id 和状态对象，避免破坏实时大盘路径，但渲染函数复用实时大盘 helper：
  - 订单商品列：`addRealtimeProductNameCell`
  - 产品销量列：`addRealtimeProductSalesCell`
  - 广告计划对应产品列：`addCampaignMatchedProductCell`
  - ROAS 小时列：`formatRealtimeHourCell`
- NPL 请求参数与实时大盘明细请求保持同构：
  - `start_date` / `end_date`
  - `include_details=1`
  - `include_profit_summary=1`
  - `order_page` / `order_page_size`
  - `page` / `page_size`
  - `site_code`
  - `product_launch_scope`
  - `product_launch_window_days`
- NPL 的订单明细和订单盈亏明细使用与实时大盘相同的分页/滚动加载语义，只是分页状态存在 `newProductLaunchState`。
- NPL 广告计划使用同款未分摊广告费筛选条；筛选只作用于当前 scope 返回的 `campaigns`。
- 后端已有 `product_launch_scope`、`product_launch_window_days`、分页和 scope 过滤能力；本次只在测试发现缺口时做后端补齐。

## 验证

- 静态模板测试锁定：
  - NPL 产品销量调用 `addRealtimeProductSalesCell`，不再拼接 `产品名 · code`。
  - NPL 订单商品列调用 `addRealtimeProductNameCell`。
  - NPL 明细请求携带 scope、window 和分页参数。
  - NPL 补齐订单/盈亏分页渲染与 infinite scroll 绑定。
  - NPL 广告计划补齐未分摊筛选条。
  - NPL ROAS 小时列调用实时大盘小时格式化。
- 后端 focused tests：`tests/test_order_analytics_realtime_product_launch_scope.py`。

## Docs-anchor

- `AGENTS.md`
- `appcore/order_analytics/CLAUDE.md`
- `docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md`
- `docs/superpowers/specs/2026-05-02-realtime-dashboard-redesign.md`
- `docs/superpowers/specs/2026-06-07-realtime-dashboard-product-sales-copy-search.md`
- `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`
