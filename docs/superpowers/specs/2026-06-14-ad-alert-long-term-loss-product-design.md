# 2026-06-14 — 广告预警新增「长期亏损品」子 Tab

## 背景

`/ad-alerts` 现有三个子 Tab：「广告预警」（商品×语言，ROAS < 阈值）、「问题广告」（campaign/adset/ad，今天 0 成效）、「高额亏损广告」（AD 级，近 7 天 ROAS < 1）。

从广告优化视角，这三个口径都不能回答运营最关心的问题——「哪些品**长期在投、最近真的在亏**」。现有「高额亏损」Tab 有三处硬伤：

1. **只看瞬时 ROAS<1**：一个长期大赚、最近 7 天 ROAS 0.9 的好品会被误报，噪音大。
2. **不排除新品**：刚上线两天、还在学习期烧钱的广告，会因为消耗高直接排到榜首。
3. **盈亏只用广告口径**（销售额 vs 广告费），不含货物 / 物流 / 手续费 / 退款，系统性低估真实亏损。

业务方诉求：找出「长期看是赚钱的品里，最近亏损已经吃掉长期利润 10% 以上」以及「长期本身就在净亏」的品；长期赚钱、最近只是小幅波动的品要自动豁免，不打扰。

## 目标

1. `/ad-alerts` 新增子 Tab「长期亏损品」，**产品级**，默认按近 7 天广告消耗降序。
2. 盈亏用**真实成本逐项扣**口径（复用订单分析利润核算），缺成本的品用**估算兜底全算**并打标。
3. 用「近期亏损 ÷ 长期盈利」+ 波动豁免规则判定，自动放行正常波动。
4. 排除近 30 天投放不足的新品；滤掉小额噪音。
5. 现有三个子 Tab 的逻辑与 API 语义**完全不动**。

## 口径

### 维度

产品（`media_products.id`）级，聚合该品下所有广告 / 市场 / 订单。市场（国家）拆分不进入判定，仅作详情下钻（后续）。

### 利润口径（真实成本，估算兜底）

单日单品真实利润沿用 `order_analytics` 既有公式：

```
利润 = 销售额(revenue) − Shopify手续费 − 广告费(全额 spend) − 货物成本(采购价) − 物流 − 退款计提
```

- 数据来源：复用 `product_profit_report` / `product_profit_list` 的产品×日聚合（从 `order_profit_lines` 读 revenue / shopify_fee / purchase / shipping_cost / return_reserve，广告费按 `product_id` 日期范围**全额** Meta spend，含 0 成交日烧钱）。
- **估算兜底**（业务方本次决策：全算，不走 `cost_completeness` 的 incomplete gate）。估算基准「销售额」= 净销售额 + 用户支付运费 = `revenue`（`line_amount + shipping_allocated`）。逐项：
  - 货物成本（采购成本）缺失 → `销售额 × 8%`。
  - 物流成本缺失 → `销售额 × 17%`。
  - 手续费 → 沿用现有 `calculate_shopify_fee` 估算逻辑（有 Shopify Payments 记录则真实，否则估算）。
  - 退款 → 沿用固定 1% 计提（真实退款管道尚未接入）。
  - 广告费 → 真实 Meta spend（非估算）。
  - 任一成本项走了估算 → 该品 `has_estimated_cost = true`，前端打「含估算」标。
  - **实现注意**：现有成本字段含 `purchase_price` / `packet_cost` / `shipping_cost`，需确认三者与利润公式减项的对应关系，按上述比例估算时避免重复计或漏计打包费。

### 判定规则

对每个候选品，先备出近 `LONG_DAYS`(默认 30) 天的逐日真实利润 `daily_profit[d]`，再算：

```
profit_7d   = Σ daily_profit 最近 RECENT_DAYS(默认7) 天
profit_30d  = Σ daily_profit 最近 LONG_DAYS(默认30) 天
spend_7d    = 近 7 天广告 spend（全额）
active_days = 近 30 天有广告消耗(spend>0)的去重天数

# 入选前置：排除新品 / 投放不足
若 active_days < MIN_ACTIVE_DAYS(默认10)            → 不纳入（新品/零星投放）

# 报警判定
若 profit_7d ≥ 0                                     → 放行（最近不亏）
L7 = −profit_7d                                       # 近 7 天净亏损额
若 profit_30d ≤ 0                                     → 🔴 报警 verdict=long_term_net_loss（长期净亏）
否则若 L7 / profit_30d > LOSS_RATIO(默认0.10)         → 🔴 报警 verdict=erodes_profit（亏损侵蚀长期利润）
否则                                                  → 放行（正常波动）

# 噪音门槛（报警后过滤）
保留条件：spend_7d ≥ MIN_SPEND_7D(默认$50) 且 L7 ≥ MIN_LOSS_7D(默认$20)
```

### 排序和数量

- 排序：`spend_7d` 降序，次级 `L7` 降序（消耗大、亏得多的在最前）。
- `limit` 默认 30，上限 100（比现有高额亏损的硬上限 30 放宽，便于全面排查）。
- 复用 `ad_alert_actions`（新 `scope = ad_alert_long_term_loss`）：默认隐藏已标记处理/忽略的品，`include_handled=true` 时全显示并附状态。

### 配置项（`system_settings`，均可配）

| key | 默认 | 含义 |
|---|---|---|
| `ad_alert_ltl_long_days` | 30 | 长期窗口天数 |
| `ad_alert_ltl_recent_days` | 7 | 近期窗口天数 |
| `ad_alert_ltl_loss_ratio` | 0.10 | 波动豁免阈值（近期亏损 ÷ 长期盈利） |
| `ad_alert_ltl_min_active_days` | 10 | 最少在投天数（排除新品） |
| `ad_alert_ltl_min_spend_7d` | 50 | 近 7 天消耗下限（USD） |
| `ad_alert_ltl_min_loss_7d` | 20 | 近 7 天净亏下限（USD） |
| `ad_alert_ltl_est_cost_rate` | 0.08 | 缺货物成本时按销售额估算的成本率 |
| `ad_alert_ltl_est_shipping_rate` | 0.17 | 缺物流成本时按销售额估算的物流率 |

## 性能

候选品可能数百个，**禁止对每个品单独调 `product_profit_report`（N+1）**。实现优先：

1. 复用 / 扩展 `product_profit_list` 的批量产品级聚合能力，一次取回多品近 30 天的逐日利润分量。
2. 若无现成批量接口，写一条批量 SQL：`order_profit_lines` 按 `(product_id, business_date)` 聚合 revenue/各成本项，LEFT JOIN 该品日维度 Meta spend，Python 内存里逐品算 `daily_profit` 与窗口聚合。

候选集先用「近 30 天有 Meta 广告消耗」收口，避免全产品扫描。

## 展示

新子 Tab「长期亏损品」，卡片列表：

- 产品主图 + 名称 + product_code。
- 判定标签：`长期净亏` / `亏损侵蚀利润`（红）。
- 指标：近 7 天消耗、近 7 天亏损额、近 30 天利润、亏损占比、在投天数、连续亏损天数（连续 `daily_profit < 0` 的天数，利润口径）、首投日期。
- `含估算` 标（`has_estimated_cost`）。
- 行级操作：标记已处理 / 忽略（复用 action workflow）。
- 顶部工具条：业务日、搜索框、当前阈值回显。
- 顶部提示条：`X 个高 GMV 品因缺成本仅用估算判定，建议优先补录`，链接到成本完备性看板（复用 `get_completeness_overview` 按 GMV 排序）。

## API

```
GET /ad-alerts/api/long-term-loss
  query: q?, limit?(默认30,上限100), include_handled?
  resp:  {
    business_date, total, thresholds:{...回显当前配置},
    items: [{
      product_id, product_code, product_name, product_main_image,
      spend_7d, profit_7d, loss_7d, profit_30d, loss_ratio,
      verdict,                       # long_term_net_loss | erodes_profit
      active_days, consecutive_loss_days, first_active_date,
      has_estimated_cost, detail_url, action
    }]
  }
```

页面路由 `GET /ad-alerts/?tab=long_loss`，复用现有 `/ad-alerts` 模板的 Tab 框架。

## 每日飞书推送

`ad_alert_daily_report` 新增一条「长期亏损品 Top N」推送（沿用 17:00 北京时间、24h 分享链接机制）。现有「高额亏损 Top 10」推送暂时保留并存；是否下线由运营在新口径稳定后决定。

## 非目标

1. 不做市场 / 国家维度的自动拆分判定（详情下钻后续迭代）。
2. 不接真实退款管道，沿用 1% 计提。
3. 不改动现有三个子 Tab 的判定逻辑与 API。
4. 不新增 AI 评估流程（沿用现有 `/api/evaluate`）。
5. 不重算成本核算公式，只复用 `order_analytics` 既有口径。

## 验证

- 判定函数单测：覆盖 近7天不亏放行 / 长期净亏报警 / 侵蚀>10%报警 / 波动豁免 / 比值边界 / `profit_30d` 接近 0 的敏感边界。
- 门槛与排除单测：`active_days < 10` 排除、消耗/亏损下限过滤、排序、Top N 截断。
- 估算兜底单测：缺采购价 / 缺打包费 / 缺物流的品都能算出利润且 `has_estimated_cost=true`。
- API 集成测试：返回结构、`include_handled`、搜索过滤。
- 复用 `order_analytics` 利润测试基线，确认未回归既有口径。

## Docs-anchor 关联

- 现有高额亏损：`docs/superpowers/specs/2026-06-12-ad-alert-high-loss-ads-tab-design.md`
- 处理状态工作流：`docs/superpowers/specs/2026-06-12-ad-alert-action-workflow-design.md`
- 成本完备性 gate：`appcore/order_analytics/cost_completeness.py`
- 真实利润核算：`appcore/order_analytics/profit_calculation.py` / `product_profit_report.py`
