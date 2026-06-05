# 素材管理单量情况列设计

- 状态：已确认
- 日期：2026-06-05
- 页面：`/medias`
- 接口：`GET /medias/api/products`

## 文档锚点

- [AGENTS.md](../../../AGENTS.md)：素材管理结构、文档驱动代码、改动后验证顺序。
- [web/static/CLAUDE.md](../../../web/static/CLAUDE.md)：`medias.js` 前端约束、Ocean Blue 视觉规则。
- [appcore/order_analytics/CLAUDE.md](../../../appcore/order_analytics/CLAUDE.md)：订单与广告数据口径、Meta 业务日规则。
- [2026-05-07 product-profit 5 Tab](2026-05-07-product-profit-dashboard-tabs-redesign-design.md)：产品维度订单数和国家维度使用 `buyer_country`。
- [2026-06-04 广告与订单同步调度](2026-06-04-ad-order-sync-schedule-design.md)：Meta 业务日为北京时间 16:00 切日。

## 背景

素材管理产品列表已经在“语种和投放情况”列里按启用语种顺序展示每个国家/语种的投放排布、推送数、ROAS 和消耗。运营还需要在同一行看到对应产品在这些国家的近期单量：今天、昨天、7 天、30 天，以及产品总单量。

## 目标

1. 在素材管理产品表新增一列“单量情况”。
2. 单量列按现有投放国家排布一行一个展示，并在顶部展示产品总计。
3. 每行展示四个窗口：今天、昨天、7 天、30 天。
4. 单量口径和现有产品盈亏/订单分析一致：按 Meta 业务日、产品、买家国家统计订单。
5. 不新增数据库表，不改变广告缓存刷新逻辑，不新增逐产品前端请求。

## 口径定义

“今天 / 昨天 / 7天 / 30天”均使用 Meta 业务日：

- 今天：`current_meta_business_date()`。
- 昨天：今天减 1 个 Meta 业务日。
- 7天：含今天在内最近 7 个 Meta 业务日。
- 30天：含今天在内最近 30 个 Meta 业务日。

单量使用 `COUNT(DISTINCT dianxiaomi_order_lines.dxm_package_id)`，避免同一订单多个 SKU 行重复计数。订单数据源使用 `order_profit_lines JOIN dianxiaomi_order_lines`，日期字段使用 `dianxiaomi_order_lines.meta_business_date`，国家字段使用 `order_profit_lines.buyer_country`，并通过 `appcore.order_analytics._constants.COUNTRY_TO_LANG` 映射到素材管理现有语言 key。

## 后端设计

新增 `appcore/media_product_order_stats.py`：

- `get_product_order_stats(product_ids, today=None) -> dict[int, dict]`
- 输入为空时返回 `{}`。
- 只查询 `today - 29 days` 到 `today` 的订单。
- 返回结构：

```json
{
  "626": {
    "total": {"today": 2, "yesterday": 1, "last_7d": 9, "last_30d": 31},
    "by_lang": {
      "de": {"today": 1, "yesterday": 0, "last_7d": 4, "last_30d": 12}
    },
    "computed_at": "2026-06-05"
  }
}
```

`web/services/media_products_listing.py::build_products_list_response` 增加一个可注入依赖 `get_product_order_stats_fn`，默认调用新模块。它在当前页产品 ID 列表上批量取数，并传给 serializer。

`web/routes/medias/_serializers.py::_serialize_product` 增加 `order_stats` 参数，输出到产品 JSON 顶层，默认为空结构，保证前端旧数据不报错。

## 前端设计

`web/static/medias.js` 新增：

- `fmtOrderCount(value)`：非负整数格式化，空值按 0。
- `renderProductOrderStatsBar(orderStats, coverage, langAdSummary)`：沿用当前 `LANGUAGES` 顺序和已展示投放/素材语言集合，顶部展示“总计”，每个语种展示“今 / 昨 / 7天 / 30天”。
- “语种和投放情况”和“单量情况”必须复用同一个 `mediaProductLangOrder(coverage, langAdSummary)` 国家行序列；两列顶部摘要行和国家明细行使用统一高度，保证德/法/西/意/日等国家按行对齐。

表格结构：

- 在“语种和投放情况”之后、“投放情况”之前新增 `<th>单量情况</th>`。
- 对应行中新增 `<td>${renderProductOrderStatsBar(...)}</td>`。
- 样式复用现有 `.oc-lang-*` 的紧凑行布局，新增类名只控制单量列宽和数字等宽，不引入新色板。

## 非目标

- 不做订单金额、利润或 ROAS 重算。
- 不新增筛选项。
- 不做单量缓存表；若未来列表性能不足，再单独设计缓存和定时刷新。
- 不改变“投放情况”列的 active/stopped/never 判定。

## 验证

1. 新增后端单元测试覆盖：
   - 空产品列表不查询数据库。
   - 同一订单多 SKU 行只计一单。
   - `buyer_country -> lang` 映射后按语言聚合。
   - 统计窗口使用 `meta_business_date` 和最近 30 个 Meta 业务日。
2. 扩展产品列表 service 测试，确认 `order_stats` 传入 serializer。
3. 扩展前端静态测试，确认表头、渲染函数和 row cell 都存在。
4. 执行：

```bash
pytest tests/test_media_product_order_stats.py tests/test_media_products_listing_service.py tests/test_medias_translation_assets.py tests/test_medias_list_filters.py -q
python3 -m compileall -q appcore/media_product_order_stats.py web/services/media_products_listing.py web/routes/medias/_serializers.py
node --check web/static/medias.js
```
