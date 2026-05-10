# 实时大盘未分摊广告费 Campaign 跳转（2026-05-10）

## 背景

`数据分析 → 实时大盘 → 订单盈亏明细` 已展示「未分摊广告费」KPI。该金额来自 `order_profit_summary.unallocated_ad_spend_usd`，当前口径是：

```text
未分摊广告费 = 总广告费 - 订单已分摊广告费
```

包含两类 campaign：

1. campaign 花费匹配不到 `media_products.product_code` 或人工绑定。
2. campaign 匹配到了 product，但当前业务日没有可用于利润分摊的 `order_profit_lines` units。

用户希望点击「未分摊广告费」卡片后，直接跳到「广告计划」tab，并只看这些未分摊广告费对应的 campaign 集合。

## 锚点

- [2026-05-07-order-profit-detail-tab-design.md](2026-05-07-order-profit-detail-tab-design.md)：订单盈亏明细、`order_profit_summary`、前端汇总卡片。
- [2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md](2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md)：实时广告费来源、open-day realtime fallback、未收盘日不能信 daily 表。
- [2026-05-09-realtime-dashboard-store-filter.md](2026-05-09-realtime-dashboard-store-filter.md)：店铺筛选与 ad account 映射规则。
- [2026-05-10-realtime-dashboard-profit-margin.md](2026-05-10-realtime-dashboard-profit-margin.md)：实时大盘利润 KPI 的同一前端渲染链路。
- [appcore/order_analytics/CLAUDE.md](../../../appcore/order_analytics/CLAUDE.md)：实时大盘业务日、广告费分摊和店铺筛选硬规则。

## 范围

做：

- 后端在 `realtime-overview` 响应里给 campaign 标注分摊状态。
- 后端返回 `unallocated_campaigns` 集合和合计，供前端直接过滤。
- 前端把「未分摊广告费」KPI 卡片做成可点击控件。
- 点击后切换到「广告计划」tab，并启用「仅未分摊」视图。
- 当前日期范围、产品筛选、店铺筛选保持沿用现有实时大盘状态。

不做：

- 不新增数据库表或 migration。
- 不改变广告费分摊公式。
- 不改变 `order_profit_lines.ad_cost_usd` 写入逻辑。
- 不把 campaign 级花费强行拆到订单；当前只做查看和过滤。
- 不新增 Meta 后台外链跳转。

## 后端设计

### Campaign 分摊状态字段

在 `appcore/order_analytics/realtime.py` 里新增内部分类逻辑，复用现有 `resolve_ad_product_match()`：

```python
allocation_status: "allocated" | "unallocated"
allocation_reason: "allocated" | "unmatched_product" | "matched_no_units"
matched_product_id: int | None
matched_product_code: str | None
matched_product_name: str | None
unallocated_spend_usd: float
```

分类规则：

- `resolve_ad_product_match(normalized_campaign_code or campaign_name)` 为空：
  - `allocation_status = "unallocated"`
  - `allocation_reason = "unmatched_product"`
  - `unallocated_spend_usd = spend_usd`
- 匹配到 product，但该 `(business_date, product_id)` 在利润分摊口径下没有 units：
  - `allocation_status = "unallocated"`
  - `allocation_reason = "matched_no_units"`
  - `unallocated_spend_usd = spend_usd`
- 匹配到 product 且存在可分摊 units：
  - `allocation_status = "allocated"`
  - `allocation_reason = "allocated"`
  - `unallocated_spend_usd = 0`

units 口径必须与现有实时广告分摊一致：按 `order_profit_lines p JOIN dianxiaomi_order_lines d`，使用 `d.meta_business_date` 和 `p.product_id` 聚合 `SUM(d.quantity)`。这样「未分摊 campaign 集合」与 `order_profit_summary.unallocated_ad_spend_usd` 保持一致。

### 响应结构

在 `get_realtime_roas_overview()` 单日实时分支、单日明细分支返回中追加：

```json
{
  "campaigns": [
    {
      "campaign_name": "...",
      "spend_usd": 79.07,
      "allocation_status": "unallocated",
      "allocation_reason": "matched_no_units",
      "matched_product_id": 427,
      "matched_product_code": "fully-automatic-water-blaster-rjc",
      "matched_product_name": "ARP9电动水枪",
      "unallocated_spend_usd": 79.07
    }
  ],
  "unallocated_campaigns": [],
  "unallocated_campaign_summary": {
    "count": 0,
    "spend_usd": 0.0
  }
}
```

范围模式 `start_date != end_date` 当前不展示 campaign 明细，保持 `campaigns = []`，同步返回空 `unallocated_campaigns` 和 0 汇总，保证 schema 稳定。

### 与汇总金额的一致性

`unallocated_campaign_summary.spend_usd` 应尽量等于 `order_profit_summary.unallocated_ad_spend_usd`。允许存在 0.01 级四舍五入差异；测试以 `pytest.approx(..., abs=0.01)` 验证。

如果后续出现一个 product 下多个 campaign 且只有部分 spend 可分摊的场景，本期以 product 级 units 判定。只要该 product 有可分摊 units，该 product 下 campaign 均视为 `allocated`；如果要精确拆出同 product 下的部分未分摊，需要新增 campaign 到订单的归因关系，不在本期范围内。

## 前端设计

### 可点击 KPI

将「未分摊广告费」汇总卡改为按钮语义：

- 保持现有卡片视觉，不引入新颜色。
- `unallocatedAd > 0` 时可点击，`aria-label` 描述为「查看未分摊广告费对应广告计划」。
- `unallocatedAd <= 0` 时禁用点击态，保留当前文本。

### 点击行为

点击后：

1. 激活 `data-realtime-subtab="campaigns"`。
2. 设置 `realtimeState.campaignFilter = "unallocated"`。
3. 调用 `renderRealtimeCampaigns(lastRealtimeCampaignRows)`。
4. 广告计划表头显示「仅显示未分摊广告费 campaign」和合计金额。

广告计划 tab 增加一个轻量筛选状态：

- 默认显示全部 campaign。
- 点击未分摊卡后只显示 `allocation_status === "unallocated"` 的行。
- 在广告计划 tab 内提供「显示全部」按钮，清除过滤。

### 表格展示

广告计划表新增一列「分摊状态」：

- 已分摊
- 未匹配 product
- 无可分摊订单

未分摊行可用 warning 文本色或 badge，不引入紫色。金额、ROAS 等原列保持不变。

## 测试计划

### 后端

新增或扩展 `tests/test_order_analytics_realtime_profit_details.py`：

1. campaign 匹配不到 product 时标记 `unmatched_product`，进入 `unallocated_campaigns`。
2. campaign 匹配 product 但无 `order_profit_lines` units 时标记 `matched_no_units`，进入 `unallocated_campaigns`。
3. campaign 匹配 product 且有 units 时标记 `allocated`，不进入 `unallocated_campaigns`。
4. `unallocated_campaign_summary.spend_usd` 与 `order_profit_summary.unallocated_ad_spend_usd` 对齐。

### 前端模板

扩展 `tests/test_order_analytics_true_roas.py` 或 `tests/test_order_analytics_template_layout.py`：

1. 「未分摊广告费」卡片有可点击 hook。
2. JS 包含切换到 `campaigns` subtab 的函数。
3. `renderRealtimeCampaigns` 支持 `allocation_status` 过滤。
4. 广告计划表包含「分摊状态」列。

### 必跑

```bash
pytest tests/test_order_analytics_realtime_profit_details.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_template_layout.py -q
```

### 手动验证

1. 启动 dev server。
2. 未登录访问 `/order-analytics` 应 302，不应 500。
3. 登录后访问 `/order-analytics?tab=realtime-overview` 应 200。
4. 打开「订单盈亏明细」，点击「未分摊广告费」卡片。
5. 页面切到「广告计划」，只显示未分摊 campaign；点击「显示全部」恢复全部。

## 修改顺序

1. 新增本 spec。
2. 更新 `appcore/order_analytics/CLAUDE.md` 追加本 spec 引用。
3. 后端新增 campaign allocation 分类 helper，并把字段接入 `get_realtime_roas_overview()`。
4. 前端增加 KPI 点击、campaign filter 状态和表格状态列。
5. 补测试并运行必跑集。

## related

- [2026-05-07-order-profit-detail-tab-design.md](2026-05-07-order-profit-detail-tab-design.md)
- [2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md](2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md)
- [2026-05-09-realtime-dashboard-store-filter.md](2026-05-09-realtime-dashboard-store-filter.md)
- [2026-05-10-realtime-dashboard-profit-margin.md](2026-05-10-realtime-dashboard-profit-margin.md)
