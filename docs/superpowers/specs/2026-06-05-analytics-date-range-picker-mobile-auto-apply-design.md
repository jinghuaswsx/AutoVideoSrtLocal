# 2026-06-05 — 数据分析日期范围选择器移动端与自动生效

- 状态：已实现
- 需求来源：用户反馈移动端日期范围面板超出屏幕，并要求选择第二个日期后直接生效加载数据，不再手动点确认。
- Docs-anchor：`AGENTS.md` 文档驱动代码、`docs/superpowers/specs/2026-06-04-analytics-date-range-picker-unification-design.md`、`docs/superpowers/specs/2026-05-10-data-analysis-mobile-actions-placement.md`、`web/static/CLAUDE.md`。

## 目标

数据分析共享日期范围选择器继续保留“先选开始、再选结束”的交互，但第二次点击日期后立即提交范围：

1. 写入原有 hidden start/end input。
2. 关闭日期面板。
3. 触发原页面对应的刷新或查询逻辑，开始加载数据。

移动端面板必须限制在当前视口内，不能像当前截图那样向右溢出或被浏览器底部栏截断主要操作。

## 覆盖范围

- 共享组件：`web/static/analytics_date_range_picker.js`。
- 主数据分析页：`web/templates/order_analytics.html`。
- 订单利润页：`web/templates/order_profit_dashboard.html`。
- 产品盈亏页：`web/templates/product_profit_dashboard.html`。

`Meta 同步`日期范围只自动写入范围并关闭面板，不自动点击“开始同步”。同步任务属于有副作用操作，仍由用户显式点击启动。

## 交互设计

- 第一次点击日期：进入“等待结束日期”状态，只更新草稿范围和高亮。
- 第二次点击日期：若结束早于开始，自动交换起止日期；随后立即提交并关闭面板。
- 取消 / Esc / 点击外部：丢弃草稿，不写入 hidden input。
- 面板文案改为“第二个日期会自动生效”，不再提示“确认后生效”。
- 确认按钮不再作为主路径展示，避免用户误以为还需要多点一次。

## 移动端布局

在 `max-width: 640px` 下，日期范围面板改为固定在视口底部的单列浮层：

- `position: fixed`，左右使用 token 间距约束，宽度不超过视口。
- `bottom: 0`，带 `env(safe-area-inset-bottom)` 内边距。
- `max-height` 约束并允许内部滚动，避免日历被浏览器底部栏遮住。
- 月份单列展示，日期按钮最小触控高度提升到移动端可点尺寸。

桌面端保留现有 absolute 两月并排浮层。

## 页面加载绑定

共享组件提交后继续派发 `analytics-date-range:apply` 事件。

已有 `change` 监听会加载数据的范围保持原路径，避免重复请求：

- 实时大盘
- 新品投放分析
- 产品看板

没有自动加载监听的范围在页面层消费 `analytics-date-range:apply`，调用原有查询函数：

- 国家看板
- 真实 ROAS
- 订单分析
- 广告分析概览
- 广告分析未匹配计划
- 广告分析 Campaign / Ad Set / Ad 列表与详情
- 广告费人工录入列表
- 订单利润
- 产品盈亏

## 不改内容

- 不改后端接口、SQL、数据质量逻辑、默认日期范围或权限。
- 不改快捷日期按钮行为。
- 不让日期选择自动触发 Meta 同步任务。

## 验证

静态测试覆盖：

- 共享 JS 有移动端 fixed/bottom-sheet 布局约束。
- 第二次选日期会调用 `applyRange()`，不再等待确认按钮。
- 页面有 `analytics-date-range:apply` 加载绑定，且不会绑定到 Meta 同步自动启动。

回归命令：

```bash
pytest tests/test_analytics_date_range_picker_asset.py \
       tests/test_order_analytics_template_layout.py \
       tests/test_order_profit_dashboard_assets.py \
       tests/test_product_profit_dashboard_assets.py -q
```

再按需运行：

```bash
pytest tests/test_order_analytics_ads.py \
       tests/test_order_analytics_dianxiaomi_analysis.py \
       tests/test_order_analytics_true_roas.py -q
```
