# 实时大盘利润 KPI 利润率字段（2026-05-10）

## 背景

`/order-analytics?tab=realtime-overview`（实时大盘）顶部 KPI 区只有「总利润额」一个利润项，对应后端 `order_profit_summary.profit_with_estimate_usd`（[`appcore/order_analytics/realtime.py`](../../../appcore/order_analytics/realtime.py) 第 241-249 行）。

参照已经落地的兄弟看板：

- 订单利润核算看板 `/order-profit`：`order_profit_summary.total_margin_pct` 与 `profit_with_estimate_margin_pct` 已存在（[`appcore/order_analytics/order_profit_aggregation.py`](../../../appcore/order_analytics/order_profit_aggregation.py) 第 828-839 行）；
- 产品盈亏看板 `/order-analytics/product-profit/`：`report.total.profit_pct` 已存在（[`appcore/order_analytics/product_profit_report.py`](../../../appcore/order_analytics/product_profit_report.py) 第 572 行）。

实时大盘是三个看板里唯一缺利润率的。本 spec 只补这一处 KPI 卡片层的利润率字段，**不动**任何明细表行、不动其它两个已经有利润率的看板。

## 范围

**做什么**

- `appcore/order_analytics/realtime.py` 的 `order_profit_summary` 数据结构新增字段 `profit_with_estimate_margin_pct`（百分比数值，2 位小数；`total_revenue_usd ≤ 0` 时为 `None`）。
- 实时大盘 KPI 卡片「总利润额」下方新增一行小字「利润率 XX.XX%」；总营收 ≤ 0 或字段为 `None` 时显示「利润率 -」。

**不做什么**

- 不改订单利润核算看板（KPI 已有利润率）。
- 不改产品盈亏看板（KPI / 国家维度 / 订单明细均已有 `profit_pct`）。
- 不改任何明细表行，不新增列。
- 不改 SQL 聚合、不改 API 路由、不改前端 tab 结构。
- 不改既有 `*_margin_pct` 字段语义。

## 字段定义

| 字段 | 类型 | 含义 | 取整 | 缺省 |
|------|------|------|------|------|
| `order_profit_summary.profit_with_estimate_margin_pct` | `float \| None` | `profit_with_estimate_usd / total_revenue_usd * 100` | 2 位小数 | `total_revenue_usd ≤ 0` 时返回 `None` |

字段命名理由：与订单利润核算看板的 `profit_with_estimate_margin_pct` 同名，语义对齐——两边的「全口径含估算利润」都覆盖采购估算 + 物流估算 + 已分摊广告 + 未分摊广告，下游消费者可以无缝复用同一字段名。

## 实现要点

### 后端：`appcore/order_analytics/realtime.py`

1. **`_empty_order_profit_summary()`**（第 151-173 行）末尾追加 `"profit_with_estimate_margin_pct": None`。

2. **`_build_order_profit_summary()`**（第 176-257 行）：在尾部 rounding 循环（第 250-256 行）**之后**补一段：

   ```python
   total_revenue = summary["total_revenue_usd"]
   if total_revenue > 0:
       summary["profit_with_estimate_margin_pct"] = round(
           summary["profit_with_estimate_usd"] / total_revenue * 100, 2
       )
   else:
       summary["profit_with_estimate_margin_pct"] = None
   ```

   必须放在 rounding 循环之后——循环对每个 key 做 `round(float(value), 2)`，若把 `None` 放进去会立刻 `TypeError`。后置赋值同时也保证用的是 round 后的 `total_revenue_usd` 与 `profit_with_estimate_usd`，避免 0.001 级浮点尾数让 KPI 卡上显示与「利润 / 营收 × 100」目测对不上。

3. **`_build_order_profit_summary_from_status()`**（第 260-310 行）：同样在尾部 rounding 循环（第 303-309 行）**之后**补同样的赋值。这条 fallback 路径是 day-final 报告口径，与主路径共用前端字段。

4. 若仓库内还有别的 `_empty_order_profit_summary` / `_build_order_profit_summary*` 直接返回点（grep 校验），同步处理。

### 前端：`web/templates/order_analytics.html`

KPI 卡「总利润额」当前由 `id="realtimeProfitTotal"` 单独渲染（约第 3756 行）。

1. 在该卡片节点内 `realtimeProfitTotal` 之后增加一个 `realtimeProfitTotalMargin` sub 元素（沿用既有 `*Note` / sub 文案 class，参考 `realtimeProfitTotalRevenueNote` 模式）；模板 HTML 由附近的 KPI 卡 markup 块插入。

2. `renderRealtimeOrderProfitSummary(summary)`（第 3732 行）末尾、在「亏损态着色」之后追加：

   ```javascript
   var marginPct = s.profit_with_estimate_margin_pct;
   var marginText = marginPct === null || marginPct === undefined
     ? '利润率 -'
     : '利润率 ' + Number(marginPct).toFixed(2) + '%';
   setRealtimeProfitText('realtimeProfitTotalMargin', marginText);
   ```

3. 着色复用既有 `oar-profit-loss / oar-profit-ok` class——直接挂在 `realtimeProfitTotalMargin` 上，与「总利润额」数字保持视觉同步：profit < 0 红色、≥ 0 中性。

4. 不引入新的 CSS 变量、不偏离 Ocean Blue Admin 设计系统约束。

## 测试

### 单元测试（必须）

新文件 `tests/test_order_analytics_realtime_profit_margin.py` 覆盖：

- `_build_order_profit_summary` 输入正常订单：`profit_with_estimate_margin_pct == round(profit_with_estimate_usd / total_revenue_usd * 100, 2)`，且为 `float`。
- `_build_order_profit_summary` 输入空 rows / `total_revenue_usd == 0`：字段为 `None`。
- `_build_order_profit_summary` 输入亏损订单（profit 为负）：`margin_pct` 为负数（不取绝对值，前端着色 fallback 到 `oar-profit-loss`）。
- `_build_order_profit_summary_from_status` 同款三态校验。
- `_empty_order_profit_summary()` 返回字典含该 key 且默认 `None`。

### 既有测试保护

修改后必须运行（覆盖实时大盘 + day-final 兜底 + 特征基线）：

```bash
pytest tests/test_order_analytics_realtime_profit_details.py \
       tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_responses_service.py \
       tests/test_order_analytics_dashboard.py \
       tests/characterization/test_order_analytics_baseline.py \
       tests/test_order_analytics_realtime_profit_margin.py \
       -q
```

特征测试 (`tests/characterization/test_order_analytics_baseline.py`) 锁定 KPI 字段集；新增字段会引发它的 schema 断言变化——对应 baseline 文件 / 期望集合需同步更新（若仅是新增 key，断言常用 `>=` 即不需要改；具体看实现决定）。

### 端到端验证

1. 在 worktree 启 dev server（空闲端口，如 `5090`）。
2. admin 登录（[testuser.md](../../../testuser.md)）→ 访问 `/order-analytics?tab=realtime-overview`。
3. 浏览器 devtools 抓 `/order-analytics/realtime-overview` 的 JSON：确认 `order_profit_summary.profit_with_estimate_margin_pct` 字段存在，数值与「利润 / 营收 × 100」二位小数对齐。
4. KPI 卡「总利润额」下方显示「利润率 XX.XX%」；切到无订单的旧业务日确认显示「利润率 -」。
5. 切换 `site_code=newjoy` / `site_code=omurio` 单店筛选：利润率随分店数据刷新，不为 0、不为 NaN（分店空订单走 `None` 分支显示 `-`）。

## 修改顺序

1. 写本 spec（**当前步骤**）→ commit。
2. 修改 `appcore/order_analytics/realtime.py` 两个聚合函数 + `_empty_order_profit_summary`。
3. 写新单元测试 `tests/test_order_analytics_realtime_profit_margin.py`。
4. 修改 `web/templates/order_analytics.html` KPI 卡片 markup + `renderRealtimeOrderProfitSummary` JS。
5. 跑测试集；端到端 dev server 自验。
6. 更新 `CLAUDE.md`「实时大盘店铺筛选（2026-05-09 起）」章节末尾追加一行 cross-reference 到本 spec。
7. commit、push、按 CLAUDE.md「本机部署到线上的标准流程」发布。

## 文档锚点更新

- 新增本 spec：`docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md`（即本文件）。
- `CLAUDE.md` 现有「实时大盘店铺筛选（2026-05-09 起）」章节追加一句话指向本 spec，作为「KPI 利润率字段」锚点。
- `CHANGELOG`：本仓库未维护根级 CHANGELOG，不适用。

## related

- [docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md](2026-05-09-realtime-dashboard-store-filter.md) — 实时大盘店铺筛选 spec
- [docs/superpowers/specs/2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md](2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md) — 实时大盘广告费口径
- [docs/superpowers/specs/2026-05-02-realtime-dashboard-redesign.md](2026-05-02-realtime-dashboard-redesign.md) — 实时大盘改版基线
- [docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md](2026-05-08-analytics-business-date-alignment-fix.md) — 业务日对齐与广告分摊口径
