# 实时大盘保本 ROAS 价格单位污染修复

## 背景

2026-06-12 排查实时大盘「全局保本 ROAS」和新品投放分析保本 ROAS 异常时，确认公式实现与前端渲染均符合既有设计，异常来自商品成本数据污染：

- 部分商品 `standalone_price` 被写成 cents-like 数值，例如 `9.99 USD` 写为 `999.00`。
- `purchase_price` 又按该异常售价推导为 `standalone_price * 0.683` RMB，导致订单利润快照里的采购成本远高于销售额。
- 受影响商品进入新品 scope 后，新品保本 ROAS 被异常采购成本拉高。

## 锚点

- `appcore/order_analytics/CLAUDE.md`：订单分析、业务日和数据质量护栏。
- `docs/superpowers/specs/2026-05-17-realtime-dashboard-global-break-even-roas-design.md`：全局保本 ROAS 公式，不扣除广告费。
- `docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md`：新品 / 老品 scope 复用实时大盘口径。
- `docs/superpowers/specs/2026-06-07-realtime-dashboard-estimate-evidence-design.md`：采购和物流缺失时使用估算成本，不改变利润公式。
- `docs/superpowers/plans/2026-05-04-order-profit-calculation.md`：订单利润中采购价按 RMB 转 USD。

## 范围

做：

- 修复已确认污染的商品价格字段。
- 清空受影响订单行的异常采购价快照，让重算按修复后的商品成本或缺失成本估算口径处理。
- 重算 `2026-06-11` 到 `2026-06-12` 的订单利润快照。
- 在商品 ROAS 字段更新链路增加价格单位护栏：
  - 当商品级 `standalone_price >= 100`；
  - 且 `standalone_price / 100` 与该商品已有 Shopify SKU 价格匹配；
  - 判定为 cents-like 单位错误，拒绝写入。

不做：

- 不改变全局保本 ROAS 公式。
- 不改变新品 / 老品 scope 规则。
- 不改变采购缺失和物流缺失的估算比例。
- 不重启服务；代码改动按正常发布流程另行上线。
- 不批量修改未确认污染的其它商品。

## 数据修复口径

本次仅修复排查确认的三个商品：

| product_id | 商品 | 错误售价 | 正确商品级售价 |
| --- | --- | ---: | ---: |
| `692` | `2024-sale-no-mess-easy-egg-opener-rjc` | `998.00` | `9.98` |
| `697` | `fresh-keeping-bags-100pcs-rjc` | `995.00` | `9.95` |
| `750` | `3-in-1-ultimate-caulking-tool-rjc` | `999.00` | `9.99` |

这三个商品当前没有可采用的真实云仓采购单价时，`purchase_price` 清空为 `NULL`，订单利润重算时走既有采购缺失估算规则。

需要清空的订单行采购价快照限定为：

- `meta_business_date BETWEEN '2026-06-11' AND '2026-06-12'`
- `product_id IN (692, 697, 750)`
- `purchase_price_cny` 等于对应污染值附近的异常数值

## 价格单位护栏

护栏使用商品已有 Shopify SKU 价格作为证据，不做纯阈值拦截：

- `standalone_price=999`，SKU 价格包含 `9.99`：拒绝。
- `standalone_price=129.99`，SKU 价格包含 `129.99`：允许。
- 商品无 SKU 价格证据时：允许，不阻断历史数据补录。

## 验证

必跑 focused tests：

```bash
pytest tests/test_product_roas.py tests/test_media_product_mutations_service.py -q
```

数据验证：

- 修复前后查询三个商品的 `standalone_price` / `purchase_price`。
- 重跑 `tools/order_profit_backfill.py --from 2026-06-11 --to 2026-06-12`。
- 查询 `/order-analytics/realtime-overview`：
  - `2026-06-12` 全局与新品 `order_profit_summary.global_break_even_roas`。
  - `2026-06-11` 全局与新品 `order_profit_summary.global_break_even_roas`。
  - 确认三个商品在 6/11-6/12 的采购成本不再高于对应销售额数倍。
