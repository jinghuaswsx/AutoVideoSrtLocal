# 数据分析订单明细动态加载

## 背景

`/order-analytics` 的订单明细类表格在大日期范围下会一次性渲染大量行，造成页面卡顿，也会放大后端查询压力。用户要求所有订单明细统一改为动态加载：首批加载 30 条，滚动到底后继续加载下一批。

## 范围

本期只覆盖 `/order-analytics` 页面内的交互式订单明细表：

- `实时大盘 -> 订单明细`
- `实时大盘 -> 订单盈亏明细`
- `订单分析 -> 店小秘订单明细`

不覆盖独立页面 `/product-profit`、`/order-profit`，不改变 Excel 下载，不改变商品销量、广告计划、ROAS 走势、导入任务等非订单明细表。

## 设计

1. 三张订单明细表默认 `page_size=30`。
2. 前端不再通过“上一页 / 下一页”查看订单明细；表格滚动容器触底时自动请求下一页并追加到现有 `<tbody>`。
3. 日期范围、店铺筛选、产品筛选、刷新按钮变化时重置对应订单表状态，清空旧行，从第 1 页重新加载。
4. 每张表都展示总数和已加载数量，格式为“已加载 X / 共 Y 条”。无数据时展示空状态。
5. 后端接口必须真正按页返回订单明细，不能在每次滚动时为了渲染当前页而先加载全量明细。
6. `实时大盘 -> 订单盈亏明细` 的汇总卡仍保持全范围口径；列表页只返回当前 30 条。总数通过单独 count / 既有聚合口径获得。

## 接口

### `GET /order-analytics/realtime-overview`

新增订单明细独立分页参数：

- `order_page`：实时大盘普通订单明细页码，默认 1。
- `order_page_size`：实时大盘普通订单明细每页条数，默认 30，上限 100。

既有订单盈亏明细分页参数保留：

- `page`：订单盈亏明细页码。
- `page_size`：订单盈亏明细每页条数，前端本期传 30，后端仍做上限保护。

响应新增：

```json
{
  "order_details": [],
  "order_details_page": {
    "page": 1,
    "page_size": 30,
    "total": 0,
    "pages": 0
  }
}
```

`order_profit_details_page` 结构不变，但前端按无限滚动消费。

### `GET /order-analytics/dianxiaomi-orders`

接口已有 `page` / `page_size` / `pagination.total`，本期前端固定以 `page_size=30` 调用并追加行。

## 前端行为

- 表格滚动容器监听 `scroll`，距离底部小于约 80px 时加载下一页。
- 同一张表在 `loading=true` 或 `hasMore=false` 时不重复请求。
- 追加渲染时只拼接新行，不重绘整张表，减少浏览器压力。
- 错误态保留在表格底部或状态文案中，不清空已加载成功的数据。

## 验证

- 后端测试覆盖 `order_page/order_page_size` 透传、实时订单明细 `LIMIT/OFFSET` 和 `order_details_page.total`。
- 模板测试覆盖三张订单表默认 `pageSize: 30`、滚动监听和追加渲染函数。
- 运行：

```bash
pytest tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_dianxiaomi_analysis.py \
       tests/test_order_analytics_realtime_profit_details.py -q
```

