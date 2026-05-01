# 实时大盘改版：贴齐国家看板的密度与时间选择

## 背景

「数据分析 → 实时大盘」当前 UI 是 5 张浅蓝底大卡（38px 蓝字）+ 5 张白底小卡 + 蓝底「日期口径」提示框，桌面端 6 列、移动端塌成单列。在移动端密度极低（每屏 1–2 张卡）。同模板下的「国家看板」是白底 + 1.5px 灰边 + 24px 黑字的 `.oa-stat-card`，移动端 flex-wrap 自然 2 列，密度高得多。

时间选择上，实时大盘只支持单个 `<input type="date">`，而国家看板已经具备「今天/昨天/本周/上周/本月/上月 + 自定义日期范围」的工具栏。

## 目标

把实时大盘的 UI 视觉与时间选择器对齐到国家看板的样式与密度，**保持原有 11 个数据项不变**：商品销售额、运费、总销售额、广告消耗费用、真实 ROAS、Meta ROAS、订单数、商品件数、订单最新时间、广告数据更新时间、数据快照时间。

## 用户已批准的关键决策

1. **时间选择只影响顶部数据卡**。下方 3 个子 tab（订单明细 / 广告计划 / ROAS 走势）始终展示「当前广告系统日」，不跟随顶部时间选择。
2. **去掉所有蓝色背景**：包括 `.oar-card.is-primary` 浅蓝底 + 蓝边 + 38px 蓝字，以及 `.oar-time-rule` 蓝底框。
3. **原 5 主大卡 + ROAS 双格 + 5 小卡（共 11 个数字位）合并为一级 11 张同密度卡**，不再保留「主卡 / 次级卡」两层视觉层级；ROAS 双格拆为两张独立卡。

## 设计

### UI（前端模板 + CSS）

**1. 工具栏（仿国家看板）**

替换原 `oar-toolbar` 的「单 date input + 刷新按钮」为：

```
[今天] [昨天] [本周] [上周] [本月] [上月]   [起始日期] 至 [结束日期]   [刷新]
```

直接复用国家看板已有的样式：
- `.oad-toolbar / .oad-toolbar-row`
- `.oad-range-presets / .oad-seg`
- `.oad-date-range-field`
- `.oad-btn-primary`

默认「今天」高亮、起止日期均为今天的广告系统日。

**2. 时间口径说明**

删除 `.oar-time-rule` 蓝底提示框，改为工具栏下方一行小灰字 `.oar-note`：

> 「按 Meta 广告系统日聚合 · 订单按广告日归属 · 北京时间 04:00 切日」

**3. 数据卡片（一级 11 张同密度）**

把原 `.oar-hero` + `.oar-grid` 两段合并为单个 `.oa-stats` flex-wrap 容器，全部使用 `.oa-stat-card`（白底 + 1.5px 灰边 + radius-lg + min-width 150 + flex 1 + 24px fg 黑色数字）：

| # | label | 主值 | sub 行（小灰字 `.oa-stat-sub`） |
|---|---|---|---|
| 1 | 商品销售额 | `summary.order_revenue` | 订单 N 单 |
| 2 | 运费 | `summary.shipping_revenue` | 订单收取的运费 |
| 3 | 总销售额 | `summary.revenue_with_shipping` | 商品销售额 + 运费 |
| 4 | 广告消耗费用 | `summary.ad_spend` | Meta 实际消耗 |
| 5 | 真实 ROAS | `summary.true_roas` | 总销售额 / 广告费 |
| 6 | Meta ROAS | `summary.meta_roas` | Meta 后台成效口径 |
| 7 | 订单数 | `summary.order_count` | — |
| 8 | 商品件数 | `summary.units` | — |
| 9 | 订单最新时间 | `freshness.last_order_at` | — |
| 10 | 广告数据更新时间 | `freshness.last_ad_updated_at` | — |
| 11 | 数据快照时间 | `period.data_until_at` | — |

注：原页面共 11 个数字位（4 张主卡含单数字 + ROAS 双格内含 2 数字 + 5 张小卡 = 4 + 2 + 5 = 11）。新版把 ROAS 双格拆成两张独立卡，正好 11 张同级卡，数据项零增减。

桌面端 flex 自然 6 列；移动端自然 wrap 成 2 列（同国家看板）。

**4. CSS 新增/修改**

- 新增 `.oa-stat-sub { margin-top: 4px; color: var(--fg-muted); font-size: var(--text-xs); }`
- 删除 / 不再使用：`.oar-card.is-primary`、`.oar-hero`、`.oar-grid`、`.oar-roas-pair`、`.oar-roas-cell`、`.oar-roas-divider`、`.oar-time-rule`、`.oar-mini-value`（保留 `.oar-toolbar` / `.oar-subtabs` / `.oar-subpanel` / `.oar-compact-table`，子 tab 那块复用）
- `.oar-toolbar` 内的 date input 删除（被工具栏 picker 替代）

**5. 子 tab 区域**

子 tab 容器上方加一行小灰字 `.oar-note`：

> 「以下明细仅展示「当前广告系统日」 · 不跟随上方时间范围」

子 tab 的请求逻辑：始终用「当前广告系统日」（即不传 `date` 参数，让后端走默认逻辑）独立请求一次 `/realtime-overview`，复用其中的 `order_details` / `campaigns` / `roas_points` 字段。

### 后端 API

**接口**：`GET /order-analytics/realtime-overview`

**新增可选参数**：
- `start_date` (YYYY-MM-DD)
- `end_date` (YYYY-MM-DD)

**行为矩阵**：

| 入参 | 行为 |
|---|---|
| 都不传 | 走原逻辑：取当前广告系统日的完整 overview（含 hourly / order_details / campaigns / roas_points） |
| 只传 `date` | 走原逻辑：取指定日的完整 overview（向后兼容） |
| `start_date == end_date` | 等价于 `date=start_date`，走原单日逻辑 |
| `start_date != end_date` | **新增范围分支**：返回 `summary` + `freshness` + `period`（含 start/end），不返回 `hourly` / `order_details` / `campaigns` / `roas_points` / `snapshots` |
| 同时传 `date` 和 `start_date` | 以 `start_date / end_date` 为准（更明确） |
| `end_date < start_date` | 400 invalid_date |

**范围分支实现**（`appcore/order_analytics.py::get_realtime_roas_overview`）：

直接复用 `get_true_roas_summary(start, end)` 的逐日聚合逻辑（它已经处理了「今天」用 `_get_today_realtime_meta_totals` 覆盖 Meta 数据），把它返回的 `summary` 字段作为范围分支的 `summary`；`freshness` 取范围内最后一天的 `last_order_at` / `last_ad_updated_at`；`period` 给出 `start / end` 而不是 `date`。

**响应结构**（范围分支，跨多日）：

```json
{
  "period": {
    "start_date": "2026-04-25",
    "end_date": "2026-05-01",
    "timezone": "America/Los_Angeles",
    "meta_cutover_hour_bj": 4,
    "day_definition": "meta_ad_platform_business_day_range"
  },
  "scope": { ... 同原结构 ... },
  "freshness": {
    "first_order_at": null,
    "last_order_at": "2026-05-01T12:34:56",
    "last_ad_updated_at": "2026-05-01T13:00:00"
  },
  "summary": {
    "order_count": ...,
    "line_count": ...,
    "units": ...,
    "order_revenue": ...,
    "line_revenue": ...,
    "shipping_revenue": ...,
    "revenue_with_shipping": ...,
    "ad_spend": ...,
    "meta_purchase_value": ...,
    "meta_purchases": ...,
    "true_roas": ...,
    "meta_roas": ...
  },
  "hourly": [],
  "roas_points": [],
  "snapshots": [],
  "order_details": [],
  "campaigns": []
}
```

`hourly` / `roas_points` / `order_details` / `campaigns` 在范围分支返回空数组，以保持响应 schema 不变（前端可安全访问字段而不需 if 判断）。

### 前端 JS 行为

1. **请求拆分**：
   - 顶部 12 张卡：每次时间切换都请求一次 `/realtime-overview?start_date=X&end_date=Y`，单日时也照样传 `start_date=end_date=date`（统一逻辑）
   - 子 tab：页面加载时**独立**请求一次 `/realtime-overview`（不带任何日期参数，走「当前广告系统日」），并把 `order_details` / `campaigns` / `roas_points` 渲染到 3 个子 tab。
   - 顶部「刷新」按钮：同时刷新顶部和子 tab 两套数据。
2. **预设按钮**：复用国家看板的 `setDxmRange` 计算逻辑（同模板内有现成 today / yesterday / thisWeek / lastWeek / thisMonth / lastMonth）。
3. **render**：把原来的 `realtimeRevenue / realtimeShipping / ...` 等 DOM id 重新分布到新的 11 张 `.oa-stat-card` 上；保持 id 不变以减小 JS 改动量。
4. **错误态**：保持 `oa-error` 容器不动；范围请求失败时只影响顶部卡片，子 tab 仍可正常渲染。

## 不做

- 子 tab 的语义、列、请求逻辑都不动（仅改请求时机：从「随顶部刷新」改为「独立刷新当前广告系统日」）
- 不改变其他 4 个 tab（订单分析 / 广告分析 / 产品看板 / 国家看板）
- 不改顶部模块导航栏
- 不引入新依赖

## 验证

1. **后端单测**（`tests/test_order_analytics_realtime_overview.py`，新建）：
   - 单日（不传 `start_date/end_date`）：行为与现状完全一致
   - `start_date == end_date`：等价单日
   - `start_date != end_date`：返回范围聚合 summary，`hourly / order_details / campaigns` 为空
   - `end_date < start_date`：抛 ValueError → 400
2. **前端 UI 回归**（Playwright 走 webapp-testing）：
   - PC：默认「今天」时 11 张卡有数据；切「昨天」、「本周」后顶部数字变化、子 tab 内容不变
   - 移动端 (375×812)：toolbar 一行能 wrap、preset 横滚或换行；卡片 2 列；无横向滚动
   - 视觉：无任何 `oklch(56% 0.16 230)`（accent 蓝）作为大面积背景；卡片外观与「国家看板」tab 一致
3. **既有测试不破**：`pytest tests/test_order_analytics*.py -q` 全过
4. **手动**：登录 `http://172.30.254.14`（admin/709709@），切到「数据分析」 tab，确认 PC + Chrome DevTools mobile 都正常。

## 部署

worktree 完成后按 CLAUDE.md 标准流程：commit → 切回主 worktree → merge 到 master → push origin/master → ssh LocalServer git pull + 重启服务（重启前向用户报备）→ healthcheck → 清理 worktree。
