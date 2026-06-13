# 物流费预警数据分析页

## 背景

运营需要在数据分析模块里长期查看物流成本偏高的产品和订单。现有实时大盘只展示整体物流成本，无法直接定位哪些产品、哪些订单的物流费占销售额比例过高。

用户确认的预警口径：

- 订单销售额使用用户实际支付的总销售额，不只看商品定价。
- 商品定价示例 `12.95 USD` 时，用户支付运费按 `+7 USD` 计入销售额后，总售价口径为 `19.95 USD`。
- 对历史订单页面，应优先使用订单利润行里的实际订单销售额和物流成本：
  - `order_profit_lines.revenue_usd`：订单行商品金额 + 运费分摊，已是 USD。
  - `order_profit_lines.shipping_cost_usd`：物流成本，已按汇率换算为 USD。
- 无论物流成本来自实际物流费、产品实际小包成本、产品预估小包成本，还是缺失物流费估算，只要 `shipping_cost_usd / revenue_usd` 超过阈值都进入预警。

## 锚点

- `appcore/order_analytics/CLAUDE.md`：订单分析、业务日、店铺筛选和数据质量护栏。
- `docs/superpowers/specs/2026-06-07-realtime-dashboard-estimate-evidence-design.md`：采购/物流估算来源和估算解释。
- `docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md`：实时大盘业务日与订单利润口径。
- `docs/superpowers/specs/2026-06-12-realtime-breakeven-roas-price-unit-guard.md`：价格单位和成本污染修复背景。

## 范围

做：

- 在数据分析模块新增与“实时大盘”并列的一级子页面：`物流费预警`。
- 新增产品聚合 API，默认展示物流费占比超过 `20%` 的产品。
- 支持阈值筛选，例如 `20` / `30`。
- 支持业务日期范围筛选。
- 产品列表优先展示产品聚合，不直接展示订单明细。
- 点击产品行跳到独立详情页，展示该产品在所选日期范围内所有超标订单行。

不做：

- 不新增数据库表或 migration。
- 不改变订单利润公式。
- 不改变实时大盘 KPI。
- 不直接修改商品价格、采购价或物流费数据。
- 不在首版做自动修复、批量导出或定时告警。

## 预警口径

订单行预警：

```text
shipping_ratio_pct = shipping_cost_usd / revenue_usd * 100
```

过滤条件：

- `order_profit_lines.business_date BETWEEN start_date AND end_date`
- `order_profit_lines.product_id IS NOT NULL`
- `order_profit_lines.revenue_usd > 0`
- `order_profit_lines.shipping_cost_usd > 0`
- `shipping_ratio_pct >= threshold_pct`

默认参数：

- `threshold_pct = 20`
- 默认日期范围：今天
- `page_size = 100`

产品聚合字段：

- 产品 ID、产品代码、中文名。
- 超标订单行数、超标订单包裹数。
- 销售额合计、物流费合计。
- 加权物流费占比：`SUM(shipping_cost_usd) / SUM(revenue_usd) * 100`。
- 最高单行占比。
- 物流成本来源统计：实际 / 产品实际 / 产品预估 / 估算。

详情页字段：

- 业务日期、付款时间、店铺、店小秘包裹号、订单号。
- SKU、销售额、物流费、物流费占比。
- 物流成本来源、利润状态。

## 路由

页面：

```text
GET /order-analytics/logistics-alert
GET /order-analytics/logistics-alert/products/<product_id>
```

JSON：

```text
GET /order-analytics/logistics-alert/data
GET /order-analytics/logistics-alert/products/<product_id>/data
```

守卫与数据分析模块一致：

```python
@login_required
@permission_required("data_analytics")
```

## 前端

主页面 `order_analytics.html`：

- 一级 Tab 增加“物流费预警”。
- 面板包含日期范围选择、阈值输入/快捷按钮、查询按钮、汇总卡片、产品表。
- 产品表行点击跳转详情页，保留 `start_date`、`end_date`、`threshold_pct`。

详情页 `logistics_fee_alert_detail.html`：

- 顶部显示产品、日期范围、阈值、汇总。
- 明细表展示超标订单行。
- 提供返回“物流费预警”链接。

## 测试

新增测试：

- service 聚合：
  - 产品聚合只返回超过阈值的行。
  - 加权占比、最高占比、来源统计正确。
  - 详情接口只返回指定产品的超标订单行。
- route/template：
  - 未登录页面 302。
  - 已登录页面 200。
  - JSON 路由校验阈值和日期。
  - `order_analytics.html` 包含“物流费预警”一级 Tab、面板和 JS 请求路径。

必跑：

```bash
pytest tests/test_logistics_fee_alerts.py \
       tests/test_order_analytics_template_layout.py -q
python3 scripts/pytest_related.py --base origin/master --run
```
