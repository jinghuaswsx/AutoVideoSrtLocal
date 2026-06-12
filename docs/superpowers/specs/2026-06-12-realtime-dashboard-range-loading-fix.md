# 实时大盘日期范围切换加载卡住修复（2026-06-12）

## 背景

用户反馈 `/order-analytics → 实时大盘` 强制刷新页面时数据很快加载，但在页面里点击「今天」「昨天」「本周」后，卡片或表格可能长时间停留在「加载中」，甚至加载不出数据。

排查结论：

- 实时大盘 JSON 入口是 `/order-analytics/realtime-overview`，已有服务端缓存层 `appcore/order_analytics/realtime_cache.py`；缓存按日期范围、明细开关、scope、分页、店铺和产品筛选分 key。
- 页面刷新和点击日期按钮最终都调用同一个接口，但交互路径不同：刷新会初始化日期后加载一次；按钮会先写 hidden 日期、同步 UI、重置分页，再并发加载顶部 4 个 scope 卡片和 1 个明细请求。
- 「今天 / 昨天」单日请求走单日实时/快照分支；「本周」等跨日范围走 `_build_realtime_overview_for_range()`，计算更重，首次 cache miss 比单日慢。
- 页面存在初始化异常：产品看板脚本中的 `setDashboardRange()` 在前置 script 中调用 `setInputValue()`，但该 helper 定义在后续 script 的局部作用域内，浏览器报 `ReferenceError: setInputValue is not defined`。该异常会污染页面初始化和后续交互稳定性。
- 实时大盘前端 fetch 当前没有超时、取消和请求序号保护；用户连续点击日期范围时，旧请求可能晚于新请求回写 UI，慢请求也可能让 loading 状态停留过久。

## 目标

1. 页面初始化不得再出现 `setInputValue is not defined`。
2. 实时大盘日期范围切换时，旧请求不得覆盖新范围结果。
3. 实时大盘请求需要有合理超时，超时后 UI 显示失败状态，不无限停留在「加载中」。
4. 点击「今天 / 昨天 / 本周」仍使用同一个 `/order-analytics/realtime-overview` 数据源和现有缓存 key，不改变业务口径。

## 非目标

- 不调整实时大盘 SQL 口径、业务日定义、广告费兜底逻辑。
- 不修改 `realtime_cache.py` 缓存失效策略。
- 不修测试环境缺 `meta_ad_realtime_daily_campaign_metrics` 表的问题；这是环境 schema 问题。
- 不拆分实时大盘 5 个请求为新接口。

## 设计

### 共享日期 helper

在 `web/templates/order_analytics.html` 的首个页面脚本中提供共享 helper：

```javascript
window.orderAnalyticsSetInputValue = function(id, value) { ... };
```

产品看板、实时大盘、新品投放等脚本都通过局部 `setInputValue()` 包装调用这个共享 helper，避免跨 script 作用域断裂。

### 实时大盘请求生命周期

在实时大盘 JS 状态中新增：

- `requestSeq`：每次完整刷新递增；
- `topCardsController` / `subTabsController`：取消上一轮顶部卡片和明细请求；
- `requestTimeoutMs`：默认 30000ms。

`loadRealtimeOverview()` 统一创建新的请求序号；`loadRealtimeTopCards(seq)` 和 `loadRealtimeSubTabs(seq)` 只允许当前序号回写 DOM。切换范围时 abort 上一轮请求。

`fetchRealtimeJson(url, controller)` 包装 `fetch()`：

- 使用 `AbortController`；
- 超时后 abort；
- HTTP 非 2xx 时解析 JSON 错误；
- abort 旧请求时不把 UI 改成失败，真正超时或 500 才进入失败态。

### 缓存行为

维持现状：`/order-analytics/realtime-overview` 按完整请求参数生成缓存 key。切换到未命中过的范围时第一次查询可能是 MISS，之后同参数会 HIT。

## 验收

- 打开 `/order-analytics?tab=realtime` 后控制台不再出现 `setInputValue is not defined`。
- 点击「今天」「昨天」「本周」后，最多 5 个 `/order-analytics/realtime-overview` 请求；旧请求被取消或被序号忽略，不覆盖当前范围。
- 任一请求超时或 500 时，顶部卡片 / 明细表显示失败信息，不无限显示「加载中」。
- 生产只读验证：响应头仍带 `X-Realtime-Cache: HIT/MISS`，缓存行为不变。

## 验证

Focused pytest：

```bash
/opt/autovideosrt/venv/bin/python -m pytest \
  tests/test_order_analytics_true_roas.py::test_realtime_range_loading_uses_abortable_request_sequence \
  tests/test_order_analytics_true_roas.py::test_order_analytics_shared_set_input_value_is_defined_before_dashboard_range \
  -q
```

手动/浏览器验证：

- 登录生产或测试环境，打开 `/order-analytics?tab=realtime`。
- 点击「昨天」「本周」「今天」，观察网络请求状态和页面 loading 状态。
- 检查控制台无 `setInputValue is not defined`。

## Docs-anchor

- 本文件
- `docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md`
- `docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md`
- `docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md`
