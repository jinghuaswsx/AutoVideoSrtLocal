# 2026-05-11 — 广告分析搜索改为底部列表查询

## 背景

`/order-analytics` 的「广告分析」面板已有 `Campaign / Ad Set / Ad` 三个子 Tab。旧交互来自 `docs/superpowers/specs/2026-05-08-ads-analytics-tabs-design.md`：搜索框实时请求 `/order-analytics/ads/search`，结果显示在下拉列表，点击后进入单条详情。

用户在逐品查看广告投放时，需要输入一个产品关键字后，直接在底部数据列表看到该产品相关的所有广告计划，而不是只能从下拉里选一条进入详情。

## 目标

1. `概览 / Campaign / Ad Set / Ad` 四个子 Tab 的搜索框改为列表过滤条件。
2. 搜索框右侧按钮文案从「刷新」改为「查询」。
3. 点击「查询」后，底部表格展示符合搜索条件和日期范围的所有广告记录。
4. 广告搜索不再显示下拉结果。
5. 表格行点击进入详情的能力保留。

## 查询语义

`概览` 接口 `/order-analytics/ad-summary` 新增可选参数 `q`。

`Campaign / Ad Set / Ad` 列表接口 `/order-analytics/ads/list` 新增可选参数 `q`。

`q` 非空时，`Campaign / Ad Set / Ad` 按当前 level 限定在本级表内过滤，匹配以下字段：

- 广告实体名称：`campaign_name` / `adset_name` / `ad_name`
- 广告实体标准 code：`normalized_campaign_code` / `normalized_adset_code` / `normalized_ad_code`
- 已匹配产品 code：`matched_product_code`

匹配采用大小写不敏感的包含匹配。日期范围、分页、排序沿用现有 `/ads/list` 逻辑；`q` 变化后前端从第一页重新查询。

`概览` 按 Campaign 级汇总数据过滤，匹配以下字段：

- 产品名：`media_products.name`
- 产品 code：`media_products.product_code`
- 广告系列名称：`campaign_name`
- 标准 Campaign code：`normalized_campaign_code`
- 已匹配产品 code：`matched_product_code`

概览保留原有「广告 × 订单关联分析」和「未匹配广告系列」两个底部列表；`q` 只改变两个列表的数据范围，不改变表头和汇总指标含义。

## 前端交互

- 四个子 Tab 的输入框只保存查询词，不再打开 `.oa-search-results` 下拉。
- 点击「查询」后调用 `adsLoadList(level)`，该函数把搜索框当前值作为 `q` 传给 `/ads/list`。
- 输入框按 Enter 等同点击「查询」。
- 清空搜索框再点「查询」显示当前日期范围下的全部列表。
- 顶部日期范围输入仍保留；「查询」同时应用日期范围和搜索词。
- `概览` 的「查询」调用 `loadAdSummary()`，把概览搜索框当前值作为 `q` 传给 `/ad-summary`。

## 非目标

- 不改变 `/order-analytics/ads/search` 后端接口，避免影响潜在旧调用；前端本次不再调用它。
- 不做跨 level 混搜；`Campaign` 只查 Campaign，`Ad Set` 只查 Ad Set，`Ad` 只查 Ad。
- 不改变列表列、详情页列、购买金额兜底、数据质量提示和默认日期范围。

## 验收

- 渲染后的广告分析四个子 Tab 不再包含 `data-ads-search-results` 下拉容器。
- 模板里四个子 Tab 查询按钮显示「查询」。
- `/order-analytics/ad-summary?q=water-blaster` 透传到数据层。
- `get_meta_ad_summary(..., q="water-blaster")` 的 SQL 过滤产品名、产品 code、Campaign 名、标准 Campaign code、`matched_product_code`。
- `/order-analytics/ads/list?q=water-blaster` 透传到数据层。
- `get_ads_level_list(..., q="water-blaster")` 的 SQL 同时过滤名称、标准 code、`matched_product_code`。
- 搜索词变化后三级列表请求回到第一页；概览重新加载当前日期范围内的汇总列表。

## Docs-anchor

- 本文件
- `docs/superpowers/specs/2026-05-08-ads-analytics-tabs-design.md`
- `docs/superpowers/specs/2026-05-09-ads-analytics-default-date-range.md`
- `appcore/order_analytics/CLAUDE.md`
