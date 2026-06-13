# 实时大盘估算数据比例与证据页

## 背景

数据分析实时大盘的全局数据、新品数据、老品数据和未匹配数据四个 scope 卡片中，利润相关指标会使用缺失成本估算：

- 采购成本缺失时，按订单总销售额的 10% 估算采购成本。
- 物流成本缺失时，按订单总销售额的 20% 估算物流成本。

当前卡片只显示“含估算”，业务方无法快速判断估算部分占整体成本的比例，也无法打开一个页面确认哪些订单和产品有确凿成本来源、哪些订单和产品使用了估算。

## 锚点

- [2026-05-02-realtime-dashboard-redesign.md](2026-05-02-realtime-dashboard-redesign.md)：实时大盘四个 scope 卡片与日期范围结构。
- [2026-05-08-analytics-business-date-alignment-fix.md](2026-05-08-analytics-business-date-alignment-fix.md)：利润汇总、广告分摊与缺失成本估算口径。
- [2026-05-10-realtime-dashboard-profit-margin.md](2026-05-10-realtime-dashboard-profit-margin.md)：`order_profit_summary` 字段扩展与前端利润 KPI 渲染链路。
- [2026-06-07-realtime-unmatched-detail-pages-design.md](2026-06-07-realtime-unmatched-detail-pages-design.md)：实时大盘详情页参数透传与未匹配 scope 页面结构。
- [../../../appcore/order_analytics/CLAUDE.md](../../../appcore/order_analytics/CLAUDE.md)：实时大盘业务日、店铺筛选与数据质量硬规则。

## 范围

做：

- `order_profit_summary` 增加估算金额占比字段，四个 scope 卡片显示一位小数百分比。
- 四个 scope 卡片在存在估算金额时显示「估算数据」按钮。
- 新增估算证据页：
  - `/order-analytics/realtime-estimates`
  - `/order-analytics/realtime-estimates/data`
- 新页面沿用实时大盘当前参数：
  - `start_date`
  - `end_date`
  - `site_code`
  - `product_id`：当前实时大盘产品搜索筛选，可选
  - `product_launch_scope`：空值表示全局，`new` / `old` / `unmatched` 表示对应 scope
  - `product_launch_window_days`
- 页面展示总体统计、明细订单、产品情况和估算规则说明。

不做：

- 不改估算公式。
- 不新增数据库表或 migration。
- 不改变广告费分摊逻辑。
- 不改变订单利润公式，只增加可解释性字段和页面。

## 实时利润行刷新

实时大盘的采购成本、物流成本和保本 ROAS 必须以 `order_profit_lines` 为实际成本来源；只有订单利润行缺失或对应字段缺失时，才按 10% / 20% 规则估算。

当 `get_realtime_roas_overview()` 请求包含 `include_profit_summary` 或 `include_details`，且日期范围覆盖当前未收盘业务日时，后端在读取利润汇总或利润明细前必须调用 `ensure_open_day_profit_lines_fresh(date_from, date_to)`。该调用复用现有 30 秒限流 open-day backfill，先把当天已进入 `dianxiaomi_order_lines` 且能匹配到实际采购/物流数据的订单刷新进 `order_profit_lines`，避免实时大盘把整天订单误判为 `p.id IS NULL` 并统一走估算。

关闭日范围、未请求利润汇总/明细的轻量请求不触发刷新。

## 字段定义

后端 `order_profit_summary` 新增：

| 字段 | 类型 | 含义 | 取整 |
| --- | --- | --- | --- |
| `purchase_estimate_ratio_pct` | `float \| None` | `purchase_estimate_usd / purchase_cost_with_estimate_usd * 100` | 1 位小数 |
| `logistics_estimate_ratio_pct` | `float \| None` | `logistics_estimate_usd / logistics_cost_with_estimate_usd * 100` | 1 位小数 |
| `cost_estimate_total_usd` | `float` | `purchase_estimate_usd + logistics_estimate_usd` | 2 位小数 |
| `cost_with_estimate_total_usd` | `float` | `purchase_cost_with_estimate_usd + logistics_cost_with_estimate_usd` | 2 位小数 |
| `cost_estimate_ratio_pct` | `float \| None` | `cost_estimate_total_usd / cost_with_estimate_total_usd * 100` | 1 位小数 |
| `has_estimated_costs` | `bool` | 是否有采购或物流估算金额 | 布尔 |

分母为 0 时比例返回 `None`，前端显示 `估算占比 -`。

## 卡片展示

四个 scope 卡片：

- 采购成本 sub 文案从「占总销售额 X%」扩展为两行：
  - `占总销售额 X%`
  - `估算占比 Y.Y%`，仅采购估算金额大于 0 时展示，否则显示 `估算占比 0.0%`
- 物流成本同款。
- 利润 sub 文案继续显示“利润口径 N 单，含估算”，并新增「估算数据」按钮。
- 「估算数据」按钮只在 `has_estimated_costs = true` 时可用；无估算时保留禁用态，避免用户误以为有隐藏数据。

按钮跳转：

```text
/order-analytics/realtime-estimates?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
+ site_code=newjoy|omurio                 # 可选
+ product_id=123                          # 可选，当前卡片受产品筛选时带上
+ product_launch_scope=new|old|unmatched  # 新品 / 老品 / 未匹配卡片带上
+ product_launch_window_days=7
```

全局卡片不带 `product_launch_scope`。

## 证据页

页面结构：

1. 总体统计
   - 订单数、总销售额、采购估算金额、物流估算金额、总估算金额、估算占比。
   - 有依据金额和估算金额用同一总成本分母展示。
2. 明细订单
   - 订单时间、业务日、店铺、订单号、产品、SKU、总销售额。
   - 采购依据：有采购数据 / 估算；估算时显示 `总销售额 × 10%`。
   - 物流依据：有物流数据 / 估算；估算时显示 `总销售额 × 20%`。
   - 估算金额合计、利润状态。
3. 产品情况
   - 按订单明细中的产品名称和 SKU 聚合。
   - 展示订单数、件数、总销售额、采购估算订单数、采购估算金额、物流估算订单数、物流估算金额、总估算金额。
4. 数据依据说明
   - 采购有依据：订单利润行可读取有效 `purchase_usd` 且未命中采购缺失条件。
   - 物流有依据：订单利润行可读取有效 `shipping_cost_usd` 且未命中物流缺失条件。
   - 采购估算：采购缺失时 `订单总销售额 × 10%`。
   - 物流估算：物流缺失时 `订单总销售额 × 20%`。

## 后端设计

`web/routes/order_analytics.py` 新增：

```text
GET /order-analytics/realtime-estimates
GET /order-analytics/realtime-estimates/data
```

守卫：

```python
@login_required
@admin_required
@permission_required("data_analytics")
```

数据接口统一调用：

```python
oa.get_realtime_roas_overview(
    None,
    start_date=...,
    end_date=...,
    include_details=True,
    include_profit_summary=True,
    product_launch_scope=...,
    product_launch_window_days=...,
    page=...,
    page_size=...,
)
```

返回：

- `summary`：来自 `order_profit_summary`。
- `rows`：只返回采购或物流有估算的 `order_profit_details`。
- `products`：由全部估算明细按产品名称 / SKU 聚合。
- `rules`：估算规则元数据。
- `data_quality`：透传 `_attach_realtime_data_quality`。

## 测试

必跑：

```bash
pytest tests/test_order_analytics_realtime_profit_details.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_template_layout.py \
       tests/test_order_analytics_realtime_product_launch_scope.py -q
```

新增或扩展：

- `_build_order_profit_summary` 输出估算比例字段。
- `get_realtime_roas_overview()` 在单日和范围模式读取利润数据前刷新 open-day `order_profit_lines`。
- 估算详情页登录态 200、未登录 302。
- 估算详情数据接口正确透传 scope、分页和店铺参数。
- 估算详情数据接口只返回有估算的订单，并聚合产品情况。
- 模板包含四个 scope 的「估算数据」入口和一位小数比例渲染逻辑。
