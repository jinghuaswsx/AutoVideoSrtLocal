# 店小秘订单分析与国家看板设计

日期：2026-04-29
状态：已确认设计，待实现计划
分支：`codex/dianxiaomi-order-analysis`

## 背景

`/order-analytics` 目前是统一的数据分析页，已有实时大盘、产品看板、真实 ROAS、Shopify 订单导入、Shopify 订单分析和广告分析能力。店小秘订单数据已经通过 `dianxiaomi_order_lines` 持久化，并被实时大盘和真实 ROAS 使用，但还缺少一个面向店小秘订单明细的独立分析 Tab。

本次需求把 Shopify 订单能力显式命名为 Shopify，同时新增店小秘订单分析和国家看板。店小秘相关日期筛选统一使用 `dianxiaomi_order_lines.meta_business_date`，也就是和真实 ROAS 对齐的广告系统日口径。

## 目标

1. 把现有“订单导入”Tab 改名为“Shopify 订单导入”。
2. 把现有“订单分析”Tab 改名为“Shopify 订单分析”。
3. 新增“订单分析”Tab，展示店小秘订单明细。
4. 新增“国家看板”Tab，展示每个国家每天、每周、每月的订单量。
5. 所有看板类列表默认按订单量从高到低排序，订单量越多越靠前。

## 非目标

- 不调整店小秘导入流程、浏览器自动化或导入表结构。
- 不改变真实 ROAS 的日期口径。
- 不新增本地 MySQL 依赖；验证仍以测试服务器 MySQL 为准。
- 不把国家看板拆成独立侧栏菜单，本次仍放在数据分析页内。

## 信息架构

数据分析页 Tab 顺序调整为：

1. 实时大盘
2. 产品看板
3. 国家看板
4. 真实 ROAS
5. Shopify 订单导入
6. Shopify 订单分析
7. 订单分析
8. 广告分析

“订单分析”在本次需求中专指店小秘订单数据。旧 Shopify 数据分析保留功能和 API，只改展示名称。

## 日期口径

店小秘订单分析与国家看板都按 `meta_business_date` 过滤。这个字段对应北京时间 16:00 到次日 16:00 的 Meta 广告系统日，与真实 ROAS 页面一致。

页面文案需要说明“日期口径：广告系统日”，避免用户把它误解成自然日。

## 店小秘订单分析 Tab

### 筛选

控件包括：

- 开始日期：`<input type="date">`
- 结束日期：`<input type="date">`
- 快捷按钮：今天、昨天、本周、上周、本月、上月
- 查询按钮

默认加载本月。开始日期和结束日期都按 `meta_business_date` 闭区间查询。

### 汇总指标

顶部显示 5 个指标卡：

- 总销售额：商品净销售额 + 运费
- 订单总量：`COUNT(DISTINCT dxm_package_id)`
- 销售件数：`SUM(quantity)`
- 运费：`SUM(ship_amount)`
- 商品净销售额：`SUM(line_amount)`

金额字段按美元展示。空值按 0 处理。

### 明细表

下方显示分页订单明细。默认每页 50 条，支持上一页和下一页。

建议列：

- 订单时间：`COALESCE(order_paid_at, paid_at, order_created_at, shipped_at, attribution_time_at)`
- 广告系统日：`meta_business_date`
- 店铺：`site_code` / `dxm_shop_name`
- 订单号：优先 `dxm_order_id`，其次 `extended_order_id`，再其次 `dxm_package_id`
- 包裹号：`package_number`
- 国家：`buyer_country_name` + `buyer_country`
- 商品：`product_name`
- SKU：`product_sku` / `product_sub_sku` / `product_display_sku`
- 件数：`quantity`
- 商品净销售额：`line_amount`
- 运费：`ship_amount`
- 总销售额：`line_amount + ship_amount`
- 状态：`order_state`

明细按订单时间倒序，再按 `dxm_package_id` 倒序稳定排序。表格需要 loading、empty、error 三态。

### API

新增接口：

`GET /order-analytics/dianxiaomi-orders`

参数：

- `start_date`：必填，`YYYY-MM-DD`
- `end_date`：必填，`YYYY-MM-DD`
- `page`：默认 1，最小 1
- `page_size`：默认 50，限制在 10 到 200

返回：

```json
{
  "period": {
    "start_date": "2026-04-01",
    "end_date": "2026-04-30",
    "date_field": "meta_business_date",
    "timezone": "Asia/Shanghai"
  },
  "summary": {
    "total_sales": 0,
    "order_count": 0,
    "units": 0,
    "shipping": 0,
    "product_net_sales": 0
  },
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total": 0,
    "total_pages": 0
  },
  "rows": []
}
```

## 国家看板 Tab

### 筛选

国家看板支持日、周、月三个视图，控件风格复用产品看板：

- 日：日期选择
- 周：年份 + 周数
- 月：年份 + 月份

默认视图为本月。

### 数据展示

展示每个国家在选中周期内的订单量，辅助列包括销售件数、商品净销售额、运费、总销售额。列表默认按订单量倒序。

建议列：

- 国家
- 订单量
- 销售件数
- 商品净销售额
- 运费
- 总销售额

空国家显示为“未知”。如果同一国家同时有 `buyer_country` 和 `buyer_country_name`，展示为“国家名 / 国家代码”。

### API

新增接口：

`GET /order-analytics/country-dashboard`

参数：

- `period`：`day` / `week` / `month`
- `date`：日视图使用，`YYYY-MM-DD`
- `year`、`week`：周视图使用
- `year`、`month`：月视图使用

返回：

```json
{
  "period": {
    "type": "month",
    "start": "2026-04-01",
    "end": "2026-04-30",
    "label": "2026 年 4 月",
    "date_field": "meta_business_date"
  },
  "summary": {
    "country_count": 0,
    "total_orders": 0,
    "total_units": 0,
    "total_sales": 0,
    "shipping": 0,
    "product_net_sales": 0
  },
  "countries": []
}
```

## 排序规则

所有看板类列表统一默认排序：

1. 订单量倒序
2. 总销售额倒序
3. 展示名称升序

适用范围：

- 产品看板 `/order-analytics/dashboard`
- 新增国家看板 `/order-analytics/country-dashboard`

产品看板现有列头点击排序可以保留，但初始加载必须按订单量倒序。后端默认 `sort_by` 从广告花费/收入调整为 `orders`。

## 后端设计

在 `appcore/order_analytics.py` 增加两个查询函数：

- `get_dianxiaomi_order_analysis(start_date, end_date, page, page_size)`
- `get_country_dashboard(period, year=None, month=None, week=None, date_str=None, today=None)`

两者都复用现有 `_parse_iso_date_param`、`_resolve_period_range`、`_money` 等工具函数。

订单分析汇总和分页明细分开查询，避免分页影响汇总。国家看板单次聚合查询即可。

在 `web/routes/order_analytics.py` 增加两个路由，统一使用 `_json_safe` 返回 JSON，并对日期参数错误返回 400。

## 前端设计

所有 UI 保持 Ocean Blue Admin 风格，使用已有 token 和表格样式。新增样式尽量复用 `.oad-*`、`.oar-*`、`.oa-table-*`，必要时添加轻量的 `oac-*` 或 `dxm-*` 类。

交互要求：

- 新 Tab 切换到“订单分析”时自动加载本月数据。
- 点击快捷日期按钮立即更新日期并查询。
- 分页按钮在第一页和最后一页时禁用。
- 新 Tab 切换到“国家看板”时自动加载本月数据。
- 国家看板日/周/月切换时刷新数据。

## 错误和空状态

- 日期缺失、格式错误、结束日期早于开始日期：接口返回 400，前端展示错误态。
- 查询成功但无数据：显示空状态，不展示空表格。
- 网络或服务异常：显示错误态和重试按钮。

## 测试计划

新增或更新测试：

1. DAO：`get_dianxiaomi_order_analysis` 正确按 `meta_business_date` 过滤、汇总、分页，并按订单时间倒序。
2. DAO：`get_country_dashboard` 日/周/月范围正确，国家按订单量倒序。
3. DAO：产品看板默认排序改为订单量倒序。
4. 路由：两个新接口能正确传参、JSON 序列化和处理参数错误。
5. 模板：`/order-analytics` 包含“Shopify 订单导入”“Shopify 订单分析”“订单分析”“国家看板”。

建议执行：

```powershell
pytest tests/test_order_analytics_true_roas.py tests/test_order_analytics_dianxiaomi.py tests/test_order_analytics_ads.py -q
```

如实现新增专门测试文件，也一并运行。
