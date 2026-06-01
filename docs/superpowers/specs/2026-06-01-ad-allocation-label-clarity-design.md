# 2026-06-01 广告未分摊与未匹配口径命名统一

## 背景

`/product-profit` 的产品盈亏看板会展示 `unallocated_ad_spend_usd`。这个金额代表没有进入产品利润分摊的广告费，原因可能是 campaign 无法匹配产品，也可能是 campaign 已匹配到产品但当天没有可分摊的 `order_profit_lines` 数量。

`/order-analytics/ads-view` 的广告分析未匹配 Tab 只展示素材库里无法解析到产品的 campaign。它不是利润分摊口径。

两个入口此前都使用“未匹配广告/未匹配 campaign”文案，容易让用户误以为两边应该返回同一组 campaign。

## 目标

- 产品盈亏看板统一使用“未分摊广告费 / 未分摊 campaign”命名。
- 产品盈亏未分摊明细必须展示原因：`unmatched_product` 显示为“未匹配产品”，`matched_no_units` 显示为“已匹配产品但无可分摊订单”。
- 广告分析 Tab 统一使用“未匹配产品广告计划”命名，强调它只表示产品解析失败。
- 不改变现有金额计算、分摊计算、手动配对写入逻辑。

## 数据口径

产品盈亏未分摊广告：

- 来源是 `generate_unmatched_ads_report()`。
- `allocation_reason=unmatched_product`：campaign 不能解析到素材库产品。
- `allocation_reason=matched_no_units`：campaign 能解析到产品，但查询窗口内该产品没有可分摊利润行数量，广告费不能进入产品利润。

广告分析未匹配产品广告计划：

- 来源是 `/order-analytics/ad-summary` 的 `unmatched`。
- 只包含 `product_id IS NULL` 或实时解析后仍没有产品的 campaign。
- 不包含 `matched_no_units`。

## 验收

- 产品盈亏 summary、Tab 标题、空态、表头不再把未分摊广告叫成未匹配广告。
- 产品盈亏未分摊表包含“分摊原因”列，并显示已匹配产品信息。
- 广告分析未匹配 Tab 的标题和空态明确写成“未匹配产品广告计划”。
- 相关模板测试和 `product_profit_ads` 聚合测试通过。
