# 2026-06-04 — 数据分析日期范围选择器统一改造

- 状态：已确认，待实现
- 需求来源：用户要求把数据分析模块内所有需要分别选择开始/结束日期的控件，改成素材管理右上角“创建时间范围选择”的交互。
- Docs-anchor：`AGENTS.md` 文档驱动代码、`docs/analytics-data-quality-guardrails.md` 时间范围显式要求、`web/static/CLAUDE.md` 前端设计系统约束。

## 目标

数据分析模块所有“开始日期 + 结束日期”成对日期控件统一改成一个日期范围按钮：

1. 点击按钮弹出日期面板。
2. 第一次点击日期作为开始。
3. 第二次点击日期作为结束；如果第二次早于第一次，自动交换起止日期。
4. 点击“确认”后写入原有日期字段并触发原页面的查询 / 刷新逻辑。

用户不再需要分别打开两个浏览器日期选择窗口。默认日期、快捷日期按钮、接口参数、后端业务逻辑保持不变。

## 覆盖范围

### `/order-analytics`

需要替换的范围控件：

- 实时大盘：`realtimeStartDate` / `realtimeEndDate`
- 新品投放分析：`nplStartDate` / `nplEndDate`
- 产品看板：`oadStartDate` / `oadEndDate`
- 国家看板：`countryStartDate` / `countryEndDate`
- 真实 ROAS：`trueRoasStart` / `trueRoasEnd`
- 订单分析：`dxmStartDate` / `dxmEndDate`
- 广告分析概览：`adStartDate` / `adEndDate`
- 广告分析未匹配计划：`adUnmatchedStartDate` / `adUnmatchedEndDate`
- 广告分析 Campaign / Ad Set / Ad 列表：`data-ads-list-start/end`
- 广告分析 Campaign / Ad Set / Ad 详情：`data-ads-detail-start/end`
- Meta 日终同步：`metaAdSyncStartDate` / `metaAdSyncEndDate`
- 人工录入列表：`amsListFrom` / `amsListTo`
- 产品盈亏报表下载弹窗：`ppr-date-from` / `ppr-date-to`

单日期控件不伪造成日期范围：

- ROAS 周报 `weeklyRoasWeekStart`
- 人工录入弹窗 `amsModalDate`

它们保留浏览器原生单日选择，因为用户本次要求的是范围选择。

### `/product-profit`

- 5 个 Tab 共用筛选条日期：`ppd-from` / `ppd-to`

### `/order-profit`

- 利润分析 / 订单利润日期范围：`opDateFrom` / `opDateTo`

## 交互设计

新组件沿用素材管理的视觉和行为，但针对数据分析做三点调整：

- 面板内加入“确认 / 取消”按钮。第二次点日期只完成草稿范围，不立即刷新；确认后才提交。
- 原始日期字段改为 hidden，保留原 id / name / value，避免改动现有 JS 取值、URL 同步和后端参数。
- 触发器文本展示业务含义，例如“实时大盘：2026/06/01 - 2026/06/04”。空值时显示“请选择日期范围”。

键盘 / 关闭规则：

- `Esc` 关闭面板并丢弃未确认草稿。
- 点击组件外关闭面板并丢弃未确认草稿。
- 确认按钮在未选满起止日期时禁用。

## 技术方案

新增共享前端文件 `web/static/analytics_date_range_picker.js`，暴露：

```js
window.AnalyticsDateRangePicker.init({
  root: HTMLElement,
  startInput: HTMLInputElement,
  endInput: HTMLInputElement,
  label: string,
  onApply: function(range) {}
});
window.AnalyticsDateRangePicker.initAll();
window.AnalyticsDateRangePicker.syncAll();
```

模板用一段统一 markup 包裹原有日期字段：

```html
<div class="analytics-range-picker" data-analytics-date-range data-range-label="实时大盘">
  <button type="button" class="analytics-range-trigger" data-range-trigger>
    <span data-range-label-text>实时大盘：请选择日期范围</span>
  </button>
  <input type="hidden" id="realtimeStartDate" value="">
  <input type="hidden" id="realtimeEndDate" value="">
</div>
```

组件确认后：

1. 写入 `startInput.value` / `endInput.value`。
2. 分别 dispatch `change` 事件，兼容现有页面逻辑。
3. 调用可选 `data-range-apply` 绑定的刷新函数；对于已有 `change` 就会刷新的页面，不额外绑定刷新函数，避免重复请求。
4. 更新触发器和北京时间业务日提示。

快捷按钮不变。原来的 `setRealtimeRange()` / `setAdRange()` 等函数继续直接写入同一个 hidden input，写完后调用 `AnalyticsDateRangePicker.syncAll()` 更新按钮文案。

## 测试

新增 / 更新模板测试，覆盖：

- 数据分析主页面不再渲染需要用户直接点击的成对 `input type="date"` 控件。
- 主页面渲染 `data-analytics-date-range` 和共享脚本。
- 广告分析列表 / 详情的动态日期控件也接入 `data-analytics-date-range`。
- `/product-profit` 和 `/order-profit` 顶部日期范围改成同一组件。
- 原有 hidden input id 保留，现有 JS 仍能读取 `start_date/end_date` 或 `date_from/date_to`。

回归命令：

```bash
pytest tests/test_order_analytics_template_layout.py \
       tests/test_order_analytics_ads.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_dianxiaomi_analysis.py \
       tests/test_order_profit_routes.py \
       tests/test_product_profit_routes.py \
       tests/test_product_profit_report.py -q
```

如改动触及数据分析脚本初始化，还需跑 `appcore/order_analytics/CLAUDE.md` 登记的完整回归集。

## 不改内容

- 不改任何后端接口、SQL、数据质量计算或业务日边界。
- 不改各模块默认日期范围。
- 不改快捷日期按钮文案和行为。
- 不改权限、路由、CSRF 策略。
