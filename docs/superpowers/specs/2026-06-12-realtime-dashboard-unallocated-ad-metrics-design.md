# 实时大盘广告卡片展示未分摊广告费指标设计 (2026-06-12)

## 需求背景
在「数据分析 → 实时大盘」页面上，包含 4 个数据卡片：全局数据、新品数据、老品数据、未匹配广告和订单。
用户希望在这 4 个卡片的「广告消耗费用」指标下方，增加一个新的数据项，用于展示由产品盈亏明细统计出来的「未分摊广告费」金额，并展示该未分摊金额在「总广告费金额」和「总销售额金额」中的占比。

## 数据来源与口径
1. **未分摊广告费金额 (`unallocated_ad_spend_usd`)**：
   - 来源于后端返回的 `order_profit_summary.unallocated_ad_spend_usd`。
   - 口径：未分摊广告费 = 总广告费 - 订单已分摊广告费。
2. **总广告费金额 (`total_ad_spend_usd`)**：
   - 来源于后端返回的 `order_profit_summary.total_ad_spend_usd`。
3. **总销售额金额 (`costRatioDenominator`)**：
   - 即后端 `order_profit_summary.total_revenue_usd`，若未提供则 fallback 为前端计算的 `revenue_with_shipping`。
4. **占比计算方式**：
   - 占比总广告费：`unallocated_ad_spend_usd / total_ad_spend_usd * 100`。
   - 占比总销售额：`unallocated_ad_spend_usd / costRatioDenominator * 100`。

## 前端 UI 交互设计
在 `order_analytics.html` 模板中，4 个卡片的「广告消耗费用」的 metric container 如下：
```html
<div class="oar-scope-metric">
  <div class="oa-stat-label">广告消耗费用</div>
  <div class="oa-stat-value" id="realtimeSpend">$0.00</div>
  <div class="oa-stat-sub" id="realtimeSpendRatio">占总销售额 -</div>
</div>
```
我们在其下增加一个新的数据行：
```html
<div class="oa-stat-sub" id="[PREFIX]SpendUnallocated">未分摊: -</div>
```
并在 JS 渲染逻辑 `renderRealtimeScopeSummary(scope, data)` 中计算并填入以下格式的字符串：
`未分摊: $X.XX (占广告 XX.XX% / 占销售 XX.XX%)`

为保证页面响应一致，在 loading 或 报错时，调用 `setRealtimeProfitText(prefix + 'SpendUnallocated', '未分摊: -')` 恢复默认占位值。

## 影响评估与验证
1. 后端接口 `/order-analytics/realtime-overview` 已统一在其 `order_profit_summary` 对象中暴露了 `unallocated_ad_spend_usd` 和 `total_ad_spend_usd`。本需求只涉及前端渲染，后端无须变更。
2. 页面在单日、时间范围切换以及过滤店铺时，均能正确显示当前未分摊广告费及其占比。
