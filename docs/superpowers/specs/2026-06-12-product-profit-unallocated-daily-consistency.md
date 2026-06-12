# 产品盈亏未分摊广告费每日口径统一（2026-06-12）

## 背景

运营在 `/product-profit` 的“产品列表”里查看当天时，未分摊广告费通常包含几十个无订单 campaign，金额约 $1,000；切到昨天后，同一位置只剩二三十美元。根因是开放业务日实时链路会把“已匹配产品但当天没有可分摊订单数量”的广告费计入未分摊，而收盘日产品盈亏列表和未分摊 campaign 明细仍主要按 `product_id IS NULL` 统计。

## 锚点

- `docs/analytics-data-quality-guardrails.md`：源广告费必须等于已分摊广告费 + 未分摊广告费；已匹配 product 但没有可分摊订单 units 的 spend 进入未分摊。
- `docs/superpowers/specs/2026-05-10-realtime-unallocated-campaign-navigation.md`：未分摊广告费包含 `unmatched_product` 与 `matched_no_units`。
- `docs/superpowers/specs/2026-06-01-ad-allocation-label-clarity-design.md`：产品盈亏未分摊 campaign 必须展示未匹配产品和已匹配但无可分摊订单两类原因。

## 口径

产品盈亏任意日期范围、任意单日快捷选择，都使用同一口径：

```text
已分摊广告费 = 匹配到 product，且该 (Meta 业务日, product_id) 有可分摊 order_profit_lines 数量的广告费
未分摊广告费 = 匹配不到 product 的广告费 + 匹配到 product 但该 (Meta 业务日, product_id) 无可分摊订单数量的广告费
```

“今天”和“昨天”的差异只能来自 Meta 数据水位，不允许来自计算公式差异。用户点击“昨天”后，也必须能在未分摊 campaign 明细里看到昨天无订单的广告计划。

## 实现要求

1. `product_profit_list._load_ad_spend_and_value` 在读取收盘日 daily 表时，必须按 `(business_date, product_id)` 查询 `order_profit_lines JOIN dianxiaomi_order_lines` 的 `SUM(quantity)`。units `<= 0` 的 daily spend 不进入产品行广告费。
2. `product_profit_list._load_unallocated_ad_spend` 在收盘日必须同时统计：
   - `product_id IS NULL` 的广告费；
   - `product_id IS NOT NULL` 但同业务日同产品 units `<= 0` 的广告费。
3. 开放业务日继续复用现有 realtime fallback。该 fallback 已经按同样 units 口径把 `matched_no_units` 计入 `unallocated_spend`。
4. 国家筛选下，订单 units 使用同国家 `buyer_country` 过滤；广告费继续使用 ad 层 `market_country` 过滤。开放业务日 + 国家筛选仍沿用既有限制，不新增 realtime country 拆分。
5. `product_profit_ads.generate_unmatched_ads_report` 的收盘日明细必须列出 `matched_no_units` campaign，字段包含 `allocation_reason=matched_no_units`、匹配到的产品信息和 `matched_profit_units=0`。

## 验收

- 选择“昨天”时，产品列表顶部 `unallocated_ad_spend_usd` 包含无订单 campaign spend。
- 点击未分摊广告费后，未分摊 campaign 明细展示这些无订单 campaign，并标注“已匹配产品但无可分摊订单”。
- `allocated_ad_spend_usd + unallocated_ad_spend_usd` 与来源广告费总额对齐。
- focused tests 覆盖 `product_profit_list` 和 `product_profit_ads` 的 closed-day `matched_no_units`。
