# 2026-05-09 — 数据分析「广告分析」默认日期范围改成「3 月 1 日 → 今天」

- 触发 issue：[AUT-16](mention://issue/210a1dfd-413d-4c03-93d7-c0e170d25057)
- 工作分支：`agent/server-opus4-7/04376117`
- 关联文档：
  - [docs/superpowers/specs/2026-05-08-ads-analytics-tabs-design.md](2026-05-08-ads-analytics-tabs-design.md) — Q5 决策（最近 14 天）被本 spec 覆盖；Tab 结构（概览 / Campaign / Ad Set / Ad）保持不变
  - [CLAUDE.md](../../../CLAUDE.md) — 路由守卫、发布流程

## 1. 目标 / 决策

「广告分析」面板下面所有日期范围控件——`概览` 子 Tab 顶部的快速段 + 自定义起止日，以及 `Campaign / Ad Set / Ad` 三个子 Tab 各自的列表 / 详情页起止日——**首次进入时**默认到 **「最近一次出现的 3 月 1 日」 → 今天**：

- 今天位于本年 3 月 1 日及之后 → `<本年>-03-01` → 今天
- 今天位于本年 3 月 1 日之前（即 1 月 / 2 月）→ `<上一年>-03-01` → 今天

Tab 文案、Tab 数量、底层数据链路、API、表头列、其他模块（订单分析、产品看板、国家看板、ROAS 等）的默认日期 **完全不变**。

为什么要改：

- 用户在 [AUT-16](mention://issue/210a1dfd-413d-4c03-93d7-c0e170d25057) 评论里明确以 `2026-03-01 → 今天` 为目标范围，并要求"广告分析模块"整块统一这个默认。
- 以前 `概览` 默认 `今天`、子 Tab 默认 `today-13`，跨 Tab 切换体验不一致；统一到「3 月 1 日 → 今天」让全年累计趋势成为开箱即用的视角。

## 2. 替换的旧默认

| 控件 | 旧默认 | 来源 | 新默认 |
|------|------|------|------|
| `概览` 子 Tab `#adStartDate` / `#adEndDate` | 今天 → 今天（preset=`today`） | [`web/templates/order_analytics.html`](../../../web/templates/order_analytics.html) `initAds()` 调 `setAdRange('today', true)` | `adsDefaultStartIso()` → `today` ISO；preset 状态 `custom`（无激活按钮，但快速段仍可点） |
| `Campaign / Ad Set / Ad` 列表 `[data-ads-list-start/end]` | `today-13` → 今天 | `adsInitDateInputs(level)` 用 `adsDaysAgoIso(13)` / `adsTodayIso()` | `adsDefaultStartIso()` / `adsTodayIso()` |
| `Campaign / Ad Set / Ad` 详情 `[data-ads-detail-start/end]` | `today-13` → 今天 | 同上 | 同上 |
| `adsLoadList` / `adsLoadDetail` 兜底（input 缺值时使用） | `adsDaysAgoIso(13)` | 同 file 5813 / 5932 行 | `adsDefaultStartIso()` |

`adsDaysAgoIso(days)` helper 在替换后无引用，**直接删掉**（不留兼容外壳）。

## 3. JS 实现

新增 helper（紧挨现有 `adsTodayIso` 定义）：

```js
function adsDefaultStartIso() {
  // 「最近一次 3 月 1 日」：今天 ≥ 本年 03-01 用本年；否则用上一年。
  var d = window.orderAnalyticsMetaCalendar
    ? window.orderAnalyticsMetaCalendar.today()
    : new Date();
  var year = d.getFullYear();
  if (d.getMonth() < 2) year -= 1;
  return year + '-03-01';
}
```

调用 `window.orderAnalyticsMetaCalendar.today()` 而不是 `new Date()`，与现有页面其它默认日期保持同一日历口径；该对象在脚本里早于 `adsInitDateInputs` 定义，调用时已就绪，但仍带 fallback 防御 `undefined`（脚本内联在同一页，理论上不会触发）。

`initAds()` 替换 `setAdRange('today', true);` 为：

```js
setInputValue('adStartDate', adsDefaultStartIso());
setInputValue('adEndDate', formatDateInput(window.orderAnalyticsMetaCalendar.today()));
adRangeState.range = '';
syncAdRangeSelection();
```

`syncAdRangeSelection` 自身遍历六个 preset，匹配不上时把 `adRangeState.range` 设为 `custom` 并清掉所有按钮的 `is-active`，正好对应"3 月 1 日 → 今天"不属于任何 preset 的状态。

## 4. 不改的部分

- 顶层 Tab 列表（`实时大盘 / 订单分析 / 广告分析 / 广告账户 / ...`）保持不动。
- `广告分析` 内的子 Tab 数量与文案：仍然 `概览 / Campaign / Ad Set / Ad`（与 [2026-05-08-ads-analytics-tabs-design.md](2026-05-08-ads-analytics-tabs-design.md) §5.1 一致）。
- 后端路由 `/order-analytics/ad-summary` / `/order-analytics/ads/list` / `/order-analytics/ads/search` / `/order-analytics/ads/detail` 的入参口径不变；前端按新默认拼参数即可。
- 其他模块（订单分析、国家看板、产品看板、真实 ROAS、ROAS 周报、Shopify 订单分析）的默认日期范围 **不动**——本次仅作用于 `#panelAds` 内部所有日期控件。
- 用户手动改过日期后再切 Tab 不重置；这条逻辑由现有 `adsInitDateInputs` 的 `!startListEl.value` 守卫保留。

## 5. 测试

`tests/test_order_analytics_ads.py` 新增一项：

- `test_ads_default_date_range_uses_march_first_of_current_year` — 渲染 `/order-analytics`，断言 body 中包含 `function adsDefaultStartIso()` 定义、`startListEl.value = adsDefaultStartIso();` / `startDetailEl.value = adsDefaultStartIso();`、`adsLoadList` 与 `adsLoadDetail` 的兜底已切换到 `adsDefaultStartIso()`、`initAds` 不再调用 `setAdRange('today', true)`，并且旧 `adsDaysAgoIso(13)` 不再出现。

回归集（保留 [AGENTS.md](../../../AGENTS.md) / [CLAUDE.md](../../../CLAUDE.md) 已登记的）：

```
pytest tests/test_order_analytics_ads.py \
       tests/test_order_analytics_data_quality.py \
       tests/test_order_profit_routes.py \
       tests/test_product_profit_routes.py -q
```

## 6. 风险 / 边界

| 风险 | 缓解 |
|------|------|
| `window.orderAnalyticsMetaCalendar.today()` 与浏览器本地 `new Date()` 时区差异，导致跨 23-24 点边缘日期偏一天 | 走 `orderAnalyticsMetaCalendar.today()`（业务日历，与现有 `country / dxm / true_roas` 等模块同源），fallback 仅在该对象不可用时启用 |
| 1-2 月进入页面，Mar 1 在未来 → 默认 `下一年-03-01 → 今天` 是非法区间 | helper 用 `getMonth() < 2` 把年份退到上一年，end ≥ start 必然成立 |
| `广告分析` 的子 Tab 加载量从 14 天扩到接近 70 天（5 月 9 日相对 3 月 1 日），后端聚合 / 前端表格压力变大 | List 端点本来就强制 `page_size`（默认 50，上限 200），单产品/单 code 仍按日期聚合一行；只有 `Ad` 级 90 行 vs 14 行的差距，浏览器渲染表格无瓶颈 |
| 页面其它"按今天"判断的逻辑被误改 | 仅替换 `#panelAds` 局部的 4 处赋值；不动 `setAdRange()` 函数本体（仍可被「今天 / 昨天 / ...」preset 按钮调用） |
