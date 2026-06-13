# 动态 Shopify 手续费率与新订单利润核算设计

## 1. 背景

实时大盘与利润表当前通过 `order_profit_lines.shopify_fee_usd` 展示 Shopify 手续费。该字段由
`appcore/order_analytics/profit_calculation.py` 调用
`appcore/order_analytics/shopify_fee.py::estimate_fee_for_buyer_country()` 计算，核心是策略 C：

- 用店小秘订单的 `buyer_country` 代理发卡国家。
- 用 `buyer_country` 推断 `presentment_currency`。
- 用 Shopify 四档费率估算，并把百分比部分乘以固定校准乘数 `1.076`。
- 固定费 `$0.30` 不放大。

2026-06-13 对最近导入的 Shopify Payments 数据复查发现，最近完整 7 天 Newjoy 真实手续费率明显随区域变化：

| 区域 | 真实平均费率 |
|---|---:|
| 美国区 | 约 3.86% |
| 欧洲区 | 约 7.54% |
| 其他区 | 约 6.40% |

固定乘数无法同时贴合不同区域和支付方式结构。用户确认：新机制上线后只影响新订单，不回刷历史利润表数据。

## 2. 文档锚点

- `docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md`：Shopify Payments 四档费率、策略 C、2026-06-06 固定乘数校准。
- `appcore/order_analytics/CLAUDE.md`：实时大盘、订单利润与数据质量约束。
- `appcore/order_analytics/shopify_fee.py`：手续费估算函数与费率常量。
- `appcore/order_analytics/profit_calculation.py`：订单行利润核算入口。
- `appcore/order_analytics/realtime.py`：实时大盘订单盈亏明细和汇总。
- `appcore/order_analytics/shopify_payments_import.py`：Shopify Payments CSV 导入与真实 fee 对账数据源。

## 3. 目标

1. 新订单利润核算使用动态手续费率，提高实时大盘和利润表的手续费估算准确度。
2. 历史 `order_profit_lines` 不重算、不覆盖，避免历史利润报表突然变口径。
3. 新逻辑生效后，实时大盘、利润表、产品盈亏、周报使用同一套手续费来源。
4. 每笔新核算订单都能解释手续费来源：真实 fee、动态区域费率，或策略 C 兜底。
5. 动态费率必须可追溯到 Payments CSV 的统计窗口和样本量。

## 4. 非目标

- 不重算生效时间之前的历史订单。
- 不改变采购、物流、广告分摊、退货占用等其它利润项口径。
- 不在本次引入 Shopify Admin API、BIN 查询或支付方式实时同步。
- 不用单一全局费率覆盖所有区域。

## 5. 生效边界

新增一个配置项：

```text
SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT
```

含义：只有订单支付时间满足 `paid_at >= SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT` 时，利润核算才使用动态手续费逻辑。

`SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT` 为空、非法，或订单没有可比较的订单时间时，动态手续费逻辑视为未启用：

- 实时大盘未核算订单继续使用策略 C，不得静默启用真实 fee 或动态快照。
- 订单利润增量/回填不得覆盖这些订单的历史手续费口径。
- 发布时可通过清空或设置未来时间来保证“从现在开始”边界。

订单时间取值顺序沿用当前实时大盘口径：

```text
COALESCE(order_paid_at, attribution_time_at, order_created_at)
```

如果订单时间早于生效时间：

- 已有历史 `order_profit_lines` 保持不变。
- 增量任务再次扫到该订单时，不覆盖其 `shopify_fee_usd` 和利润结果。
- 查询展示时可把来源视为 `legacy_strategy_c` 或空来源。
- 新 resolver 返回策略 C 兜底结果，并在 trace 中说明 `dynamic_fee_not_effective`。

## 6. 区域定义

区域由订单的推断结账币种决定。动态费率快照和实时估算都使用同一映射：

| region | 条件 |
|---|---|
| `us` | `presentment_currency = USD` |
| `europe` | `EUR/GBP/CHF/SEK/NOK/DKK/PLN/CZK/HUF/RON/BGN` |
| `other` | 其它币种 |

对于还没有真实 `presentment_currency` 的店小秘订单，先复用 `infer_presentment_currency_from_country(buyer_country)` 推断。

## 7. 动态费率快照

新增表 `shopify_fee_rate_snapshots`，用于保存每次导入 Payments CSV 后计算出的费率：

```text
id
store_code
region
window_start_date
window_end_date
window_days
orders_count
amount_usd
fee_usd
effective_rate
fixed_fee_per_order
variable_rate
source_csvs_json
sample_status
computed_at
```

字段说明：

- `effective_rate = fee_usd / amount_usd`，用于直接估算展示。
- `variable_rate = max(fee_usd - orders_count * 0.30, 0) / amount_usd`，用于保留 `$0.30` 固定费的订单级估算。
- `sample_status`：
  - `ok_7d`：最近完整 7 天样本足够。
  - `ok_30d`：7 天样本不足，使用最近 30 天。
  - `insufficient`：30 天仍不足，不参与估算。

样本门槛：

- 优先使用最近完整 7 天，且 `orders_count >= 100`。
- 不足时使用最近 30 天，且 `orders_count >= 300`。
- 店铺级样本不足时回退到全店铺同区域快照。
- 仍不足时回退策略 C。
- `orders_count` 统计必须按标准化 Shopify order name 去重：去掉首个前导 `#` 后再计数，空 order name 才回退 `transaction_id`，避免 `#2001` 和 `2001` 被算成两单。

## 8. 手续费来源优先级

新订单利润核算使用以下优先级：

1. `actual_payment`
   - 能按 Shopify order name 匹配到 `shopify_payments_transactions` 的正向 charge。
   - 匹配必须按店铺隔离，不能让不同店铺相同 Shopify order name 的交易互相命中。
   - 使用真实 `fee_usd`，多 SKU 订单按行收入比例摊回。

2. `dynamic_region_rate`
   - 匹配不到真实 fee，但有可用动态区域快照。
   - 订单级公式：

```text
fee = amount * variable_rate + 0.30
```

   - 多 SKU 订单先按整单 amount 算一次 fee，再按行收入比例摊回，保持当前固定费不重复收取的原则。

3. `strategy_c_fallback`
   - 没有真实 fee，也没有足够动态样本。
   - 继续使用当前策略 C 函数和 `1.076` 乘数。

## 9. 利润表字段

`order_profit_lines` 新增可追溯字段：

```text
shopify_fee_source
shopify_fee_rate
shopify_fee_rate_region
shopify_fee_rate_window_start
shopify_fee_rate_window_end
shopify_fee_basis_json
```

`shopify_fee_basis_json` 记录：

- `strategy_version`
- `order_total_revenue_usd`
- `order_fee_usd`
- `line_allocation_ratio`
- `matched_payment_transaction_ids`，仅真实 fee 命中时有值
- `snapshot_id`，仅动态快照命中时有值
- `fallback_reason`，仅兜底时有值

历史行不强制补这些字段。查询侧看到空来源时，按旧口径处理。

## 10. 实时大盘集成

实时大盘保持优先读 `order_profit_lines.shopify_fee_usd`：

- 对已核算的新订单，展示利润表中的手续费和来源。
- 对当天未入利润表的订单，现场调用同一套手续费 resolver：
  - 真实 fee 命中则用 `actual_payment`。
  - 动态快照可用则用 `dynamic_region_rate`。
  - 否则用 `strategy_c_fallback`。
  - 动态手续费未达到生效时间、配置为空/非法或订单时间缺失时，也只能走策略 C。

接口增加手续费来源汇总：

```text
order_profit_summary.shopify_fee_source_counts
order_profit_summary.shopify_fee_source_amounts
order_profit_summary.shopify_fee_rate_watermark
```

前端可在数据质量条或利润说明中展示：

- 真实手续费多少单。
- 动态估算多少单。
- 策略 C 兜底多少单。
- 动态费率窗口截止日期。

## 11. 增量任务与导入流程

Payments CSV 导入后：

1. 写入 `shopify_payments_transactions`。
2. 计算或刷新 `shopify_fee_rate_snapshots`。
3. 不自动重算历史订单。
4. 可触发生效时间之后的新订单增量重算。

订单利润增量任务：

- 对 `paid_at < SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT` 的已有利润行跳过覆盖。
- 对生效后的订单，按新 resolver 写入手续费和来源字段。
- `order_profit_runs.summary_json` 记录策略版本、快照窗口和来源计数。

## 12. 数据质量与告警

动态快照需要暴露以下质量状态：

- `ok`：当前店铺 + 区域命中足量样本。
- `fallback_store_scope`：店铺样本不足，使用全店铺同区域。
- `fallback_strategy_c`：动态样本不足，回落策略 C。
- `stale`：最新快照窗口结束日期距离当前日期超过 14 天。

实时大盘的 `data_quality.warnings` 应包含：

- 当前是否存在 `strategy_c_fallback`。
- 动态费率是否 stale。
- 某区域样本是否不足。

手续费来源告警必须同时进入 `order_profit_summary.data_quality` 和 `/order-analytics/realtime-overview` 顶层 `data_quality`，顶层 `status` 至少降级为 `warning`，让页面数据质量条可见。

## 13. 测试范围

后端单元测试：

- 动态区域映射。
- 快照生成：7 天足量、7 天不足回退 30 天、30 天不足。
- 快照订单数去重：`#order_name` 与 `order_name` 算同一单。
- 手续费 resolver 优先级：真实 fee > 动态费率 > 策略 C。
- 手续费 resolver 生效边界：空/非法生效时间、缺订单时间、生效前订单都不得启用动态逻辑或真实 fee。
- 真实 fee 匹配店铺隔离：不同店铺相同 order name 不得串单。
- 固定费 `$0.30` 不重复摊到 SKU 行。
- 生效时间之前的历史行不被增量任务覆盖。

集成测试：

- `calculate_line_profit()` 写入新来源字段。
- 实时大盘缺利润行时使用动态 resolver。
- `order_profit_summary` 返回手续费来源汇总和 watermark。
- `strategy_c_fallback` 同时进入实时大盘顶层 `data_quality.warnings/checks/status`。

验证命令按项目 targeted pytest 规则选择：

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

如脚本无目标，至少运行：

```bash
pytest tests/test_shopify_fee.py \
       tests/test_profit_calculation.py \
       tests/test_order_analytics_realtime_profit_details.py \
       tests/test_order_profit_aggregation.py -q
```

## 14. 发布与回滚

上线步骤：

1. 先部署 schema 与 resolver，但保持 `SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT` 为空或未来时间。
2. 导入最新 Payments CSV，生成动态快照。
3. 设置 `SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT` 为确认的上线时间。
4. 验证实时大盘来源汇总和新订单利润行。

回滚：

- 清空或延后 `SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT`。
- 新订单重新增量核算会回到旧策略 C。
- 历史旧行本来未回刷，不需要恢复。

## 15. 实施顺序

1. 增加动态快照表和 DAO。
2. 扩展 Payments CSV 导入后的快照生成。
3. 增加手续费 resolver。
4. 扩展 `order_profit_lines` 来源字段与持久化。
5. 接入 `profit_calculation.py`。
6. 接入 `realtime.py` 的未核算订单现场估算和来源汇总。
7. 增加数据质量提示与 focused tests。
