# 数据分析默认进入实时大盘 ROAS 走势

## 背景

数据分析模块当前顶层默认进入「实时大盘」，但实时大盘内部默认落在「订单明细」。用户希望点击侧栏「数据分析」时，默认加载「实时大盘」的同时，直接加载其下的「ROAS 走势」子 Tab。

同一模块内，用户选定日期、店铺、产品或新品范围后，切换实时大盘底部子 Tab 时，不应让顶部全局 / 新品 / 老品 / 未匹配四组实时卡片重新请求。切换子 Tab 只刷新底部明细区域，顶部卡片保持当前筛选结果。

## 锚点

- `AGENTS.md`：文档驱动代码与数据分析验证规则。
- [2026-05-02-realtime-dashboard-redesign.md](2026-05-02-realtime-dashboard-redesign.md)：实时大盘顶部卡片与子 Tab 请求拆分基线。
- [2026-06-07-realtime-roas-trend-hourly-ad-spend-design.md](2026-06-07-realtime-roas-trend-hourly-ad-spend-design.md)：ROAS 走势小时行口径。
- `web/templates/CLAUDE.md`：Jinja 模板修改规则。

## 要求

1. `/order-analytics` 和 `/order-analytics/realtime` 的默认落点改为 `/order-analytics/realtime/trend`。
2. 直接打开默认数据分析页时，顶层 Tab 仍为「实时大盘」，实时大盘内部 active 子 Tab 为「ROAS 走势」。
3. 点击实时大盘底部子 Tab 时，不做整页跳转，不触发 `loadRealtimeTopCards()`，只刷新底部明细请求。
4. 子 Tab 切换后要用 History API 更新地址到 `/order-analytics/realtime/<subtab>`，并保留当前查询参数，便于刷新和分享。
5. 顶部卡片刷新边界保持明确：首次进入实时大盘、点击「查询」、修改日期范围、店铺、产品、新品范围时刷新；仅切换子 Tab 时不刷新。

## 不改

- 不改 `/order-analytics/realtime-overview` 后端参数、SQL 或数据口径。
- 不改实时大盘日期、店铺、产品、新品范围筛选的现有查询参数。
- 不改 ROAS 走势表格列、小时广告费或 ROAS 计算逻辑。

## 验证

- 模板测试覆盖：默认页面 active 子 Tab 为 `trend`；子 Tab 点击逻辑不再使用 `window.location.href` 整页跳转，并调用 `setRealtimeSubtab()`、`loadRealtimeSubTabs()` 与 `history.pushState()`。
- 路由测试覆盖：`/order-analytics` 和 `/order-analytics/realtime` 默认重定向到 `/order-analytics/realtime/trend`。
- 按 targeted pytest 规则运行相关测试，不默认跑全量。
