# 实时大盘费用完整展示与利润公式代入

## 背景

`/order-analytics/realtime/trend` 的四张 scope 汇总卡（全局 / 新品 / 老品 / 未匹配）已经展示采购、物流、手续费、广告费和利润，但没有把进入利润公式的退款/退货预留扣减作为独立费用项展示；利润卡也只展示结果和利润率，无法直接看到“每一项带入公式后如何得到最终利润”。

现有后端口径不可改乱：

- 有 `order_profit_lines` 的订单行使用 `return_reserve_usd` 进入利润公式，默认由 `appcore/order_analytics/profit_calculation.py` 按 `revenue × order_profit_return_reserve_rate` 计算，缺省 1%。
- 无可用利润行的实时兜底订单使用 `refund_deduction_usd` 进入利润公式；实际退款金额优先，退款/取消状态但无金额时按整单销售额扣减。
- 汇总利润使用 `profit_deduction_usd`，而不是直接把 `refund_deduction_usd` 和 `return_reserve_usd` 相加。

## 目标

四张 scope 汇总卡都要可见展示：

- 退款/预留扣减费用项：主值为实际进入利润公式的 `profit_deduction_usd`。
- 扣减比例：`profit_deduction_usd / total_revenue_usd × 100`。
- 扣减组成：退货预留计入、实际退款兜底计入、其他扣减。
- 退款记录与退货预留记录：分别展示金额及其占总销售额比例，方便核查退款费用比例。
- 利润公式模板。
- 利润数字代入过程。
- 每一项值和最终结果。

## 字段

在 `order_profit_summary` 中补充：

| 字段 | 含义 |
|---|---|
| `refund_deduction_ratio_pct` | `refund_deduction_usd / total_revenue_usd × 100` |
| `return_reserve_ratio_pct` | `return_reserve_usd / total_revenue_usd × 100` |
| `profit_deduction_ratio_pct` | `profit_deduction_usd / total_revenue_usd × 100` |
| `profit_deduction_from_return_reserve_usd` | 进入利润公式的退货预留扣减 |
| `profit_deduction_from_refund_usd` | 进入利润公式的实际退款兜底扣减 |
| `profit_deduction_other_usd` | 进入利润公式但无法归类到以上两项的扣减 |

## 前端展示

每个 scope 卡新增“退款/预留扣减”指标：

```text
退款/预留扣减
$257.75
占总销售额 1.00%
计入扣减: 预留 $257.75 / 退款兜底 $0.00
退款记录 $0.00 (0.00%) / 预留记录 $257.75 (1.00%)
```

每个 scope 的“利润”指标保持紧凑，只展示利润值、利润口径、利润率，并提供一个“计算详情”按钮。公式和数字代入过程不直接铺在卡片里，避免实时大盘首屏被长文本撑开。

点击“计算详情”后打开统一的利润计算弹窗，弹窗内按两行上下对齐展示：

```text
利润
$-1,030.45
利润口径 948 单，含估算
利润率 -4.00%
计算详情
```

弹窗内容：

```text
公式  利润 = 总销售额 - 退款/预留扣减 - 采购成本(含估算) - 物流成本(含估算) - 手续费 - 广告费
代入  $25,775.20 - $257.75 - $1,933.28 - $4,279.12 - $1,622.58 - $18,713.26 = $-1,030.45
```

公式行与代入行左侧标签、表达式和最终结果区域需要上下对齐；代入行必须体现每一项已填入的金额和最终利润结果。

## 验证

- `tests/test_order_analytics_realtime_profit_details.py` 覆盖汇总扣减比例和扣减来源字段。
- `tests/test_order_analytics_true_roas.py` 覆盖实时大盘模板存在退款/预留扣减卡、利润“计算详情”按钮、公式弹窗和代入行；卡片内不再直接展示长公式/明细/代入。
- 按 `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md` 跑相关 focused tests，不默认跑全量 pytest。
