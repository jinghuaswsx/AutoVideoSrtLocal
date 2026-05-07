# 实时大盘订单盈亏明细 Design

## 背景

`/order-analytics` 的实时大盘现在已有子 tab：订单明细、产品销量、广告计划、ROAS 走势。订单明细只展示销售侧字段，无法在同一张实时表里看到每笔订单的采购、物流、手续费、广告费和利润。

项目里已经存在订单利润核算基础：

- `dianxiaomi_order_lines` 保存店小秘订单行，包含订单销售额、运费收入、物流成本、退款金额、订单状态等字段。
- `order_profit_lines` 保存 SKU 行级利润核算结果，包含采购成本、物流成本、Shopify 手续费、广告费分摊、退货预留和净利润。
- `appcore/order_analytics/shopify_fee.py` 已实现 Shopify Payments 费率规则，并能拆出基础平台费率、国际信用卡费率、货币转换费率。

本次需求是在实时大盘新增子 tab「订单盈亏明细」，按订单级别展示每笔订单的费用项目和利润。

## 目标

在「实时大盘」内新增一个子 tab：`订单盈亏明细`。

该 tab 基于现有实时大盘订单明细的数据范围，展示当前广告系统日的订单级利润明细。所有费用项目都必须在前端单独显示，不能只展示一个合计成本。

## 2026-05-07 增量需求：分页、汇总、估算和产品筛选

用户在截图红框位置确认了 5 个增量要求：

1. `订单盈亏明细` 改为分页展示，每页固定 100 条订单级数据。
2. 表格标题右侧的红框区域展示当前筛选范围内的汇总：总销售额、总采购成本、总物流成本、总合计手续费、总广告费、总利润额。
3. 采购成本和物流成本存在缺失时，汇总卡片用小字标注订单缺失比例；汇总计算使用估算值补齐缺失成本：采购成本按订单总销售额 10% 估算，物流成本按订单总销售额 20% 估算。
4. 实时大盘顶部时间预设和日期输入一旦选择即自动查询；原「刷新」按钮改为「查询」，保留在日期选择框后面，用于手动重查当前条件。
5. 实时大盘增加产品搜索框。选择产品后，顶部数据卡、订单明细和订单盈亏明细都按该产品过滤，以便查看单产品数据。

### 增量口径

- 分页只影响明细表 rows，不影响汇总。汇总必须基于当前日期范围和产品筛选后的全量订单集合计算，不能只统计当前页 100 条。
- 产品筛选使用 `dianxiaomi_order_lines.product_id = product_id`。多产品订单被筛选后只统计选中产品对应的订单行金额、成本和广告分摊，订单号仍按 `dxm_package_id` 聚合展示。
- 采购缺失订单：筛选后的订单行中存在缺少 `order_profit_lines`，或利润行 `missing_fields` 包含 `purchase_price`，或采购成本聚合为 0 且利润状态不是完整时，视为该订单采购缺失。
- 物流缺失订单：筛选后的订单行中存在缺少 `order_profit_lines`，或利润行 `missing_fields` 包含 `shipping_cost` / `packet_cost`，或物流成本聚合为 0 且利润状态不是完整时，视为该订单物流缺失。
- 汇总展示的采购成本使用 `purchase_cost_usd + purchase_estimate_usd`，其中缺失订单的 `purchase_estimate_usd = total_revenue * 0.10`。
- 汇总展示的物流成本使用 `logistics_cost_usd + logistics_estimate_usd`，其中缺失订单的 `logistics_estimate_usd = total_revenue * 0.20`。
- 汇总利润使用补齐后的采购和物流成本：

```text
summary_profit =
  total_revenue
  - refund_deduction
  - (purchase_cost + purchase_estimate)
  - (logistics_cost + logistics_estimate)
  - shopify_fee_total
  - ad_cost
```

行级利润仍展示当前逐单计算值，同时响应中返回该行是否使用采购/物流估算以及估算后的利润，便于前端后续提示；V1 前端汇总必须明确显示缺失比例，行级不强制额外加列。

最终列：

| 列 | 说明 |
|---|---|
| 订单时间 | `COALESCE(order_paid_at, attribution_time_at, order_created_at)` |
| 广告日小时 | 沿用现有实时订单明细的 business hour |
| 店铺 | `site_code` |
| 订单号 | 优先 `dxm_order_id`，兜底 `dxm_package_id` |
| 国家 | `buyer_country_name / buyer_country` |
| 商品 | 订单内商品名或 SKU 汇总 |
| 件数 | 订单内 `quantity` 求和 |
| 总销售额 | 商品销售额 + 买家支付运费 |
| 退款扣减 | 部分退款按实际 `refund_amount_usd` 扣，全额退款或退款状态但无金额时按整单总销售额扣 |
| 采购成本 | `order_profit_lines.purchase_usd` 订单级求和 |
| 物流成本 | `order_profit_lines.shipping_cost_usd` 订单级求和 |
| Shopify平台手续费 | 基础处理费，订单级拆分展示 |
| 国际信用卡费 | 跨境卡费，订单级拆分展示 |
| 货币转换费 | 非 USD 结账的货币转换费，订单级拆分展示 |
| 合计手续费 | 平台手续费 + 国际信用卡费 + 货币转换费 |
| 广告费分摊 | `order_profit_lines.ad_cost_usd` 订单级求和 |
| 订单利润 | 本需求定义的订单级利润 |
| 状态 | 完备、部分完备、不完备、退款、部分退款等提示 |

## 已确认口径

### 利润公式

用户确认后的订单利润口径：

```text
订单利润 =
  总销售额
  - 退款扣减
  - 采购成本
  - 物流成本
  - Shopify平台手续费
  - 国际信用卡费
  - 货币转换费
  - 广告费分摊
```

注意：

- `合计手续费` 只是展示字段，不在公式里二次扣减。
- 扣广告费分摊。
- 不扣现有订单利润核算里的 `return_reserve_usd`，也就是不扣 1% 退货预留。
- 因为本 tab 的利润口径不扣退货预留，所以不能直接展示 `order_profit_lines.profit_usd` 的订单级求和。

### 退款扣减

退款扣减按订单级字段处理：

1. 部分退款订单按实际 `refund_amount_usd` 扣。
2. 全额退款订单按整单 `总销售额` 扣。
3. 如果订单状态显示退款/取消/退货，但 `refund_amount_usd` 为空或 0，为避免漏算，按整单 `总销售额` 扣。
4. `refund_amount_usd` 在 `dianxiaomi_order_lines` 里可能是订单级字段重复到多条 SKU 行，因此订单聚合必须用 `MAX(refund_amount_usd)`，不能 `SUM(refund_amount_usd)`。
5. 若 `refund_amount_usd` 大于订单总销售额，展示和计算时按订单总销售额封顶，避免异常数据把退款扣减放大。

退款状态判断 V1 使用保守规则：

- `order_state` 包含 refund、refunded、cancel、cancelled、closed、return 等英文关键词。
- 如果项目中已有中文状态值，后续实现时把「退款」「已退款」「取消」「已取消」「退货」加入同一判断。

### 手续费拆分

现有 `order_profit_lines.shopify_fee_usd` 已保存合计 Shopify 手续费，但没有落库保存三项拆分。V1 在订单级现算拆分：

```text
shopify_platform_fee = order_revenue * 0.025 + 0.30
international_card_fee = order_revenue * 0.010 if card_country != "US" else 0
currency_conversion_fee = order_revenue * 0.015 if presentment_currency != "USD" else 0
total_fee = shopify_platform_fee + international_card_fee + currency_conversion_fee
```

其中：

- `order_revenue` 使用整单总销售额，也就是商品销售额 + 买家支付运费。
- `card_country` 继续沿用现有策略：用买家国家 `buyer_country` 代理。
- `presentment_currency` 继续沿用现有策略：通过 `buyer_country` 映射推断。
- 多 SKU 订单只收一次固定费 `$0.30`，不能每条 SKU 行重复收。
- 为了和现有 `order_profit_lines.shopify_fee_usd` 对齐，订单级 `total_fee` 应优先来自重新按订单总额计算的拆分值，而不是行级固定费重复求和。
- 如需和历史核算严格对账，可在响应里同时返回 `stored_shopify_fee_total_usd`，但前端 V1 只展示拆分后的三项和合计。

## 数据来源

### 主表

`dianxiaomi_order_lines`

用于筛选实时大盘订单范围并聚合订单基础信息：

- `site_code`
- `dxm_package_id`
- `dxm_order_id`
- `package_number`
- `order_state`
- `buyer_country`
- `buyer_country_name`
- `quantity`
- `line_amount`
- `ship_amount`
- `refund_amount_usd`
- `refund_amount`
- `order_paid_at`
- `attribution_time_at`
- `order_created_at`
- `meta_business_date`
- `product_sku`
- `product_name`

### 利润表

`order_profit_lines`

按 `order_profit_lines.dxm_order_line_id = dianxiaomi_order_lines.id` 关联：

- `status`
- `purchase_usd`
- `shipping_cost_usd`
- `ad_cost_usd`
- `shopify_fee_usd`
- `return_reserve_usd`

本需求不使用 `profit_usd` 做最终利润，但可用于对账或调试。

## 后端设计

### 新增查询函数

在 `appcore/order_analytics/realtime.py` 新增内部函数：

```python
def _get_realtime_order_profit_details(
    target: date,
    day_start: datetime,
    data_until: datetime,
) -> list[dict[str, Any]]:
    ...
```

行为：

- 与 `_get_realtime_order_details()` 使用相同订单时间表达式和筛选范围。
- 仅查 `site_code IN ('newjoy', 'omurio')`。
- 仅查 `meta_business_date = target`。
- 仅查 `order_time <= data_until`。
- 按 `dxm_package_id` 订单级聚合。
- 订单内多行产品名、SKU 用 `GROUP_CONCAT(DISTINCT ...)` 汇总。
- left join `order_profit_lines`，允许利润核算尚未完成的订单仍展示销售侧数据。

SQL 聚合重点：

```sql
COUNT(*) AS line_count,
SUM(COALESCE(d.quantity, 0)) AS units,
SUM(COALESCE(d.line_amount, 0)) AS product_revenue,
SUM(COALESCE(d.ship_amount, 0)) AS shipping_revenue,
SUM(COALESCE(d.line_amount, 0)) + SUM(COALESCE(d.ship_amount, 0)) AS total_revenue,
MAX(COALESCE(d.refund_amount_usd, 0)) AS refund_amount_usd,
SUM(COALESCE(p.purchase_usd, 0)) AS purchase_cost,
SUM(COALESCE(p.shipping_cost_usd, 0)) AS logistics_cost,
SUM(COALESCE(p.ad_cost_usd, 0)) AS ad_cost,
SUM(CASE WHEN p.status='ok' THEN 1 ELSE 0 END) AS profit_ok_count,
SUM(CASE WHEN p.status='incomplete' THEN 1 ELSE 0 END) AS profit_incomplete_count
```

注意：如果 `ship_amount` 是订单级字段重复到多行，需要沿用当前实时订单明细既有口径。现有 `_get_realtime_order_details()` 已使用 `SUM(ship_amount)`，本次不改变这个既有行为，避免实时大盘两个订单表的销售额不一致。如后续发现 `ship_amount` 重复，可另开修正。

### 手续费拆分辅助函数

在 `appcore/order_analytics/realtime.py` 或更合适的 `appcore/order_analytics/shopify_fee.py` 增加一个可测试的拆分函数：

```python
def split_shopify_fee_for_order(
    *,
    amount: float,
    buyer_country: str | None,
) -> dict[str, float | str]:
    ...
```

返回：

```python
{
    "shopify_platform_fee_usd": 0.0,
    "international_card_fee_usd": 0.0,
    "currency_conversion_fee_usd": 0.0,
    "shopify_fee_total_usd": 0.0,
    "shopify_tier": "A" | "B" | "C" | "D" | "..._estimated",
    "presentment_currency": "USD" | "EUR" | ...
}
```

实现时复用现有常量：

- `BASE_RATE`
- `FIXED_FEE`
- `CROSS_BORDER_RATE`
- `CURRENCY_CONVERSION_RATE`
- `infer_presentment_currency_from_country`
- `classify_tier`

金额四舍五入到 2 位，用于前端展示。若需要和利润核算 DECIMAL(12,4) 更贴近，内部可以保留 Decimal，响应时转 float。

### 订单利润计算

每个订单响应行计算：

```python
refund_deduction = resolve_refund_deduction(
    total_revenue=total_revenue,
    refund_amount_usd=refund_amount_usd,
    order_state=order_state,
)

order_profit = (
    total_revenue
    - refund_deduction
    - purchase_cost
    - logistics_cost
    - shopify_platform_fee
    - international_card_fee
    - currency_conversion_fee
    - ad_cost
)
```

`resolve_refund_deduction()` 必须单独测试。

### 状态语义

响应行新增：

```python
{
    "profit_status": "ok" | "partially_complete" | "incomplete" | "not_computed",
    "refund_status": "none" | "partial_refund" | "full_refund",
    "status_label": "完备" | "部分完备" | "未核算" | "部分退款" | "全额退款"
}
```

建议派生规则：

- 没有关联到任何 `order_profit_lines`：`profit_status = "not_computed"`。
- 有关联且 `profit_incomplete_count = 0`：`profit_status = "ok"`。
- 有关联且 ok 与 incomplete 混合：`profit_status = "partially_complete"`。
- 有关联但全部 incomplete：`profit_status = "incomplete"`。
- `refund_status` 独立于 `profit_status`，前端可同时显示。

### API 响应

在 `get_realtime_roas_overview()` 返回中增加：

```json
{
  "order_profit_details": [],
  "order_profit_details_page": {
    "page": 1,
    "page_size": 100,
    "total": 0,
    "pages": 0
  },
  "order_profit_summary": {
    "order_count": 0,
    "total_revenue_usd": 0.0,
    "refund_deduction_usd": 0.0,
    "purchase_cost_usd": 0.0,
    "purchase_estimate_usd": 0.0,
    "purchase_cost_with_estimate_usd": 0.0,
    "purchase_missing_order_count": 0,
    "purchase_missing_order_ratio": 0.0,
    "logistics_cost_usd": 0.0,
    "logistics_estimate_usd": 0.0,
    "logistics_cost_with_estimate_usd": 0.0,
    "logistics_missing_order_count": 0,
    "logistics_missing_order_ratio": 0.0,
    "shopify_fee_total_usd": 0.0,
    "ad_cost_usd": 0.0,
    "profit_with_estimate_usd": 0.0
  }
}
```

保持已有字段不变：

- `order_details`
- `product_sales_stats`
- `campaigns`
- `roas_points`
- `hourly`

范围模式 `start_date != end_date` 继续返回空数组：

```json
{
  "order_profit_details": []
}
```

2026-05-07 增量后，前端子 tab 跟随顶部日期范围；范围模式在 `include_details=1` 时可以返回分页订单明细和订单盈亏明细。未传 `include_details` 时仍返回空数组，避免顶部卡片请求拖慢。

### 新增查询参数

- `include_details=1`：返回订单明细和订单盈亏明细。
- `page`：订单盈亏明细页码，最小 1。
- `page_size`：订单盈亏明细每页条数，V1 固定 100，后端做上限保护。
- `product_id`：可选，按 `dianxiaomi_order_lines.product_id` 过滤顶部汇总、订单明细和订单盈亏明细。

## 前端设计

### 子 tab

在实时大盘子 tab 区新增按钮：

```html
<button type="button" class="oar-subtab" data-realtime-subtab="profitDetails">订单盈亏明细</button>
```

新增 panel：

```html
<div class="oar-subpanel" id="realtimeSubProfitDetails">
  ...
</div>
```

### 表格展示

`订单盈亏明细` 标题行右侧新增紧凑汇总条，放在截图红框位置。汇总项：

```text
总销售额
总采购成本（缺失 x.x%）
总物流成本（缺失 x.x%）
总合计手续费
总广告费
总利润额
```

采购和物流显示的是“实际 + 估算”后的金额；括号内小字显示缺失订单比例。没有缺失时不显示括号。

表头完整展示每个费用项目：

```text
订单时间
广告日小时
店铺
订单号
国家
商品
件数
总销售额
退款扣减
采购成本
物流成本
Shopify平台手续费
国际信用卡费
货币转换费
合计手续费
广告费分摊
订单利润
状态
```

UI 约束：

- 表格横向滚动，避免压缩到不可读。
- 金额列右对齐。
- 成本/扣减项可用负向语义颜色，但不能引入紫色。
- `订单利润 < 0` 使用 danger 色，`订单利润 >= 0` 使用 success 或默认文本色。
- `部分完备`、`不完备`、`未核算` 用 badge 或文本提示，不阻断行展示。

### JS 渲染

新增：

```javascript
function renderRealtimeOrderProfitDetails(rows) { ... }
function renderRealtimeOrderProfitSummary(summary) { ... }
function renderRealtimeOrderProfitPagination(pageInfo) { ... }
```

`loadRealtimeSubTabs()` 在拿到 `/order-analytics/realtime-overview` 后调用：

```javascript
renderRealtimeOrderProfitDetails(data.order_profit_details || []);
renderRealtimeOrderProfitSummary(data.order_profit_summary || {});
renderRealtimeOrderProfitPagination(data.order_profit_details_page || {});
```

加载态和错误态：

- 加载中：`<td colspan="18">加载中...</td>`
- 空数据：`<td colspan="18">暂无订单盈亏数据</td>`
- 错误：沿用现有 catch，把错误信息写进该 tbody。

## 不做

V1 不做以下内容：

- 不新增数据库列保存手续费拆分。
- 不修改 `order_profit_lines.profit_usd` 既有净利润口径。
- 不改变 `/order-profit` 独立利润看板。
- 不让新增子 tab 跟随顶部日期范围，仍展示当前广告系统日。
- 不在 Windows 本地连接 MySQL 验证真实数据。
- 不做订单详情弹窗，先用一张订单级表满足查看需求。

## 风险和处理

### 风险 1：手续费拆分与 `order_profit_lines.shopify_fee_usd` 有小额差异

原因：现有行级核算可能已按订单总额计算再摊回 SKU 行，但前端新表按订单级重新拆分。由于四舍五入和行聚合，可能有 0.01 到 0.02 的差异。

处理：

- V1 以前端拆分值为展示口径。
- 测试允许总手续费与现有合计在小额容差内。

### 风险 2：`refund_amount_usd` 在多行订单重复

处理：

- 聚合使用 `MAX(refund_amount_usd)`。
- 增加测试覆盖多 SKU 订单部分退款，确保不会重复扣。

### 风险 3：利润核算未跑完

处理：

- left join `order_profit_lines`。
- 销售额、退款、手续费拆分仍可展示。
- 采购、物流、广告费为 0 或空时状态标记为 `not_computed` 或 `incomplete`，前端提示「未核算」。

## 测试计划

### 后端单元测试

新增或扩展 `tests/test_order_analytics_true_roas.py` / 新建聚焦测试：

1. `_get_realtime_order_profit_details()` 返回订单盈亏列。
2. 多 SKU 订单 `refund_amount_usd` 用 `MAX`，不重复扣。
3. 部分退款按实际 `refund_amount_usd` 扣。
4. 全额退款或退款状态但无金额时按整单总销售额扣。
5. 订单利润扣广告费，不扣 `return_reserve_usd`。
6. 手续费拆分只收一次 `$0.30` 固定费。
7. `start_date != end_date` 范围响应包含空 `order_profit_details`，保持 schema 稳定。

### 前端模板测试

扩展现有 `/order-analytics` 模板测试：

1. 页面包含 `data-realtime-subtab="profitDetails"`。
2. 页面包含 `id="realtimeOrderProfitBody"`。
3. 表头包含全部费用列：退款扣减、采购成本、物流成本、Shopify平台手续费、国际信用卡费、货币转换费、合计手续费、广告费分摊、订单利润。
4. JS 调用 `renderRealtimeOrderProfitDetails(data.order_profit_details || [])`。

### 验证限制

本需求不在 Windows 本地连接 `127.0.0.1:3306`。若后续需要真实数据验证，按项目规则走测试环境 `http://172.30.254.14:8080/` 和服务器测试库。

## 实施文件范围

预计实现会修改：

- `appcore/order_analytics/realtime.py`
- `appcore/order_analytics/shopify_fee.py`，如选择把拆分 helper 放到该模块
- `web/templates/order_analytics.html`
- `tests/test_order_analytics_true_roas.py`
- 可能新增 `tests/test_order_analytics_realtime_profit_details.py`

不需要新增 migration，不需要改定时任务注册。

## 用户确认记录

- 用户确认利润需要扣广告费分摊。
- 用户确认不扣 1% 退货预留。
- 用户要求每个费用项目都显示在前端。
- 用户确认部分退款订单按实际 `refund_amount_usd` 扣。
