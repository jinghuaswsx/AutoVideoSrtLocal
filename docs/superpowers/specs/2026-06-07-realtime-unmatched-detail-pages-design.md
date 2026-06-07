# 实时大盘未匹配订单与广告明细页

## 背景

`数据分析 -> 实时大盘` 顶部已有「未匹配广告和订单」汇总卡，卡片按 `product_launch_scope=unmatched` 展示无法归入产品维度的数据。用户需要从该汇总直接打开明细，确认金额背后的数据来源：

- 哪些订单 / 商品订单没有匹配到素材库产品。
- 哪些广告计划没有匹配到素材库产品。

历史 `/order-analytics/orphan-orders` 页面查询的是 `shopify_orders.product_id IS NULL`，权限也是 `orphan_orders`，与实时大盘的店小秘订单和 Meta 广告口径不同，本需求不复用该页面。

## 锚点

- [2026-05-02-realtime-dashboard-redesign.md](2026-05-02-realtime-dashboard-redesign.md)：实时大盘日期范围与 `realtime-overview` 基础结构。
- [2026-05-09-realtime-dashboard-store-filter.md](2026-05-09-realtime-dashboard-store-filter.md)：`site_code` 店铺筛选和广告账户映射规则。
- [2026-05-10-realtime-unallocated-campaign-navigation.md](2026-05-10-realtime-unallocated-campaign-navigation.md)：广告计划分摊状态字段，包含 `allocation_reason=unmatched_product`。
- [2026-06-01-ad-allocation-label-clarity-design.md](2026-06-01-ad-allocation-label-clarity-design.md)：未匹配广告只表示产品解析失败，不包含 `matched_no_units`。
- [appcore/order_analytics/CLAUDE.md](../../../appcore/order_analytics/CLAUDE.md)：实时大盘业务日、店铺筛选与数据质量硬规则。

## 范围

做：

- 在实时大盘「未匹配广告和订单」卡片标题右侧新增两个新窗口入口：
  - `未匹配订单`
  - `未匹配广告`
- 新增两个独立页面路由：
  - `/order-analytics/realtime-unmatched-orders`
  - `/order-analytics/realtime-unmatched-ads`
- 两个页面沿用当前实时大盘日期范围、店铺筛选和新品范围参数，固定 `product_launch_scope=unmatched`。
- 未匹配订单明细展示 `dianxiaomi_order_lines.product_id IS NULL` 的订单聚合行。
- 未匹配广告明细只展示 `allocation_reason=unmatched_product` 的广告计划。

不做：

- 不新增数据库表或 migration。
- 不改变实时大盘汇总公式。
- 不改变广告分摊逻辑，不把未匹配广告强行分摊到订单。
- 不复用或修改 `/order-analytics/orphan-orders`。
- 不把 `matched_no_units` 命名为未匹配广告；它属于未分摊广告费原因，不属于产品解析失败。

## 前端设计

实时大盘卡片头部保持现有标题和 chip，在标题右侧增加两个紧凑按钮链接。链接使用 `target="_blank" rel="noopener noreferrer"`，并由 JS 根据当前筛选实时更新 query string：

```text
start_date=YYYY-MM-DD
end_date=YYYY-MM-DD
site_code=newjoy|omurio  # 可选
product_launch_window_days=7
```

产品搜索筛选不传入两个明细页，因为未匹配订单和未匹配广告本身都没有可用的产品 ID。

## 后端设计

新增两个只读页面和两个只读 JSON 数据接口，均使用 `@login_required + @admin_required + @permission_required("data_analytics")`：

```text
GET /order-analytics/realtime-unmatched-orders
GET /order-analytics/realtime-unmatched-ads
GET /order-analytics/realtime-unmatched-orders/data
GET /order-analytics/realtime-unmatched-ads/data
```

数据接口统一调用 `oa.get_realtime_roas_overview(..., include_details=True, product_launch_scope="unmatched")`，并透传：

- `start_date`
- `end_date`
- `site_code`
- `product_launch_window_days`
- 订单页分页参数 `page` / `page_size` 映射到 `order_page` / `order_page_size`

订单接口返回：

- `rows = result.order_details`
- `page = result.order_details_page`
- `summary / period / freshness / scope`

广告接口返回：

- `rows = result.campaigns` 中 `allocation_reason == "unmatched_product"` 的行
- `summary.count`
- `summary.spend_usd`
- `summary.purchase_value_usd`
- `period / freshness / scope`

## 验证

必跑：

```bash
pytest tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_template_layout.py -q
```

手动：

1. 未登录访问 `/order-analytics/realtime-unmatched-orders` 和 `/order-analytics/realtime-unmatched-ads` 应 302，不应 500。
2. 登录后两个页面应 200。
3. 实时大盘卡片右侧两个按钮新窗口打开，URL 保留当前日期范围和店铺筛选。
4. 未匹配广告页不展示 `matched_no_units` 行。
