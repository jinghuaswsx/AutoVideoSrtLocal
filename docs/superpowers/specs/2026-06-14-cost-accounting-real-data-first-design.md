# 成本核算「真实数据优先」改造设计

最后更新：2026-06-14
状态：设计方向已与用户确认，待写实施计划

## 1. 背景

2026-06-14 直连生产审查 + 数据源勘探确认：订单利润核算的成本降级链代码**基本都已实现**，但真实数据链路存在「开关没开 / 历史没回填 / 数据断更」的缺口，导致成本大量退回估算口径。勘探事实（生产库，2026-06-14）：

- **汇率**：`usd_cny_daily_exchange_rates` 仅 9 天（6/6–6/14）；`order_profit_lines` 中 61%（21590 行）`cost_basis.exchange_rate_source` 为空、实为汇率体系上线前用固定 `system_settings.material_roas_rmb_per_usd=6.83` 算的；frankfurter 历史端点实测可回填到最早订单日 2/24。
- **手续费**：真实优先 resolver（`shopify_fee_resolver.resolve_shopify_fee_for_order`）已实现，但开关 `SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT` 为空 → `is_dynamic_fee_effective` 恒 False → **0 行**用真实 `actual_payment`；动态快照表 `shopify_fee_rate_snapshots`=0；`shopify_payments_transactions` 与订单匹配率 99.98%、覆盖约 63% GMV、断更于 6/6。
- **物流（订单级）**：92.4%（32601 行）已用真实 `logistic_fee` 按 line_amount 摊到 SKU 行；仅约 3% 走 20% fallback。
- **SKU 采购/物流**：采购 79%（28039 行）有订单快照真实价；SKU 级物流（云仓 `packet_cost_actual_sku`）覆盖低（478/2069）。

实测影响：2026-05 整月 ok 行确认利润 −$1,639（亏损），靠 incomplete 行估算利润 +$3,866 拉回；用真实手续费替代估算后，历史利润预计上升约 $2 万。

## 2. 与既有 spec 的关系（重要）

- 本 spec **更新并覆盖** `docs/superpowers/specs/2026-06-13-dynamic-shopify-fee-rate-recalculation-design.md` 第 3 节目标 2、第 4 节非目标、第 5 节生效边界中「**新机制只影响新订单、不回刷历史利润表**」的约定。2026-06-14 用户重新拍板：**按「有真实数据就用」原则全量重算历史（含手续费）**，接受历史利润报表数字变动。该 spec 的 resolver 设计（手续费来源优先级、动态快照、来源字段、店铺隔离）**继续有效**，仅历史口径从「只影响新订单」改为「全量重算」。
- 复用既有设计：`2026-06-06-usd-cny-daily-exchange-rate-design.md`（日汇率体系）、`2026-05-04-order-level-shipping-cost-design.md`（订单级物流摊派）、`2026-06-13-accounting-reconcilable-analytics-profit-remediation.md`（会计可对账总纲、`data_quality` 披露）。

## 3. 目标

把「有真实/更准数据就用，没有才估算」落实到四项成本，并全量重算历史：

1. **汇率**：回填 2/24 起历史日汇率；全量重算让每行按订单 `business_date` 的真实日汇率换算采购/物流。
2. **手续费**：打开真实链路（开关 + 快照 + payments 持续供给），全量重算历史；有真实 payment 用真实 fee，缺失估算并标注、payments 到位后自动替换。
3. **物流（订单级）**：维持真实 `logistic_fee` 优先；缺口在看板暴露督促。
4. **SKU 采购/物流**：维持 SKU 级真实优先降级链；缺失集中暴露督促补全。
5. **统一**：每项成本输出带 `source`；前端 KPI 区分「真实 vs 估算」；真实数据到位后自动替换重算。

## 4. 非目标

- 退款真实化（单独排期；`refund_verification` 模块已建、`refund_verifications`=0 待启用）。
- 核算公式 / 模块架构重构。
- Shopify 订单 / 广告同步管道、Payments Admin API 自动化（本期手动 CSV）。
- 改变采购/物流/广告分摊/退货占用的**算法**（只改「取数优先级 + 历史回填 + 来源标注」）。

## 5. 总原则（贯穿四项）

真实源优先 → 缺失才估算并打 `source` 标 → 真实数据到位后自动替换重算 → 历史全量重算对齐。所有面向数据分析/订单利润的 JSON 顶层带 `data_quality`（沿用会计可对账总纲）；前端明确区分「已对账真实成本 vs 估算成本」。

## 6. 设计详情

### 6.1 汇率

- **锚点**：`appcore/exchange_rates.py`、表 `usd_cny_daily_exchange_rates` / `usd_cny_fallback_exchange_rates`、`tools/order_profit_backfill.py`。
- **回填历史日汇率**：新增一次性回填能力，对 2/24~今天每个缺失交易日调 frankfurter **历史端点**（`https://api.frankfurter.app/{YYYY-MM-DD}?from=USD&to=CNY`，实测可用；周末/节假日顺延最近交易日），经现有三源交叉校验（`validate_cross_rates`，diff>5% 拒绝）后 upsert 入 `usd_cny_daily_exchange_rates`。注意现有 `fetch_frankfurter_usd_cny` 用 `/latest`，需支持传入日期走历史端点。
- **降级链（已实现，保持）**：`get_usd_to_cny_for_date`：日汇率(`daily_archive`) → 30 天均值(`fallback_30d_average`) → 配置值 6.83(`configured_fallback`)，`source` 如实标注。
- **全量重算**：`backfill` 在 `manual_rate=None` 模式下已按 `business_date` 调 `get_usd_to_cny_map` 取日汇率；回填后以该模式重跑历史，让 61% 固定 6.83 的行换成真实日汇率。
- **持续**：日汇率同步任务每日跑 + 断更告警（见 §9）。

### 6.2 手续费

- **锚点**：`appcore/order_analytics/shopify_fee_resolver.py`、`shopify_fee_dynamic.py`、`shopify_payments_import.py`、`config.SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT`、表 `shopify_fee_rate_snapshots` / `shopify_payments_transactions`、`tools/order_profit_backfill.py`。
- **启用真实链路**：
  1. 设 `SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT` ≤ 最早订单日，让**全历史订单**都进入真实优先链路（`is_dynamic_fee_effective` 恒 True）。
  2. 导入最新 payments CSV，生成 `shopify_fee_rate_snapshots`（沿用 dynamic-shopify-fee spec §7 样本门槛）。
- **全量重算（覆盖既有「不回刷」）**：调整 `tools/order_profit_backfill.py::_should_skip_for_dynamic_fee_boundary`——当前它对「已有 `existing_profit_line_id` 的历史行」跳过覆盖（即「不回刷」的实现），改为**允许全量重算覆盖历史 `shopify_fee_usd` 及来源字段**。每行优先级：真实 payment(`actual_payment`) → 动态区域费率(`dynamic_region_rate`) → 策略C(`strategy_c_fallback`)，写 `shopify_fee_source`。
- **渐进替换**：未结算/未导入订单先走估算并标「待对账」；每周导入新 payments 后，对新匹配到真实 fee 的订单重算替换（经 `order_profit_recompute_queue` 或定期重算窗口）。
- **影响**：历史手续费回刷为真实值（总体偏低）→ 历史利润上升约 $2 万。

### 6.3 物流（订单级）

- **锚点**：`tools/order_profit_backfill.py::_process_line`（物流降级链）、`dianxiaomi_order_lines.logistic_fee`。
- **保持**：实付 `logistic_fee` 按 line_amount 摊到 SKU 行（最真实），优先级最高；缺失 → 云仓 SKU(`yuncang_sku_actual`) → 产品 `product_actual` → 产品 `product_estimated` → 20% fallback。
- **补缺口**：约 3% 缺失行对应产品在看板暴露，督促维护物流成本（数据治理）。基本无需改算法。

### 6.4 SKU 采购/物流

- **锚点**：`tools/order_profit_backfill.py::_LINE_QUERY`（采购 4 级 COALESCE）、`media_products`、`media_product_skus`、`dianxiaomi_yuncang_skus`。
- **保持降级链**：采购 = 订单快照价(`order_snapshot`) → SKU 手工价(`manual_unit_price_rmb`) → 云仓 SKU 价(`yuncang_sku`) → 产品级(`media_product`)；缺失 → 10% fallback 并标。
- **督促补全**：缺采购价 / SKU 物流的产品经 `get_order_profit_incomplete_products` / 看板集中暴露 + 维护入口（压低 fallback）。以数据治理为主。

## 7. Payments 持续供给（手动周导）

- **方式**：用户**每周手动**导入 Shopify **Payments → Transactions** 全量 CSV（含 `fee`/退款），经 `shopify_payments_import.py` 写入 `shopify_payments_transactions` 并刷新 `shopify_fee_rate_snapshots`。（与订单 Orders 导出是两份不同文件，订单导出不含每笔手续费。）
- **监控**：新增「最新 payments 交易日 / 导入时间」巡检，超过约定周期（如 9 天）未更新 → `data_quality` 告警 + 提醒导入（登记到 `appcore/scheduled_tasks.py`）。
- **结算延迟**：Shopify payout 结算 T+2~T+5，最近 1–2 周订单常无真实 fee，按估算兜底 + 待对账，后续导入替换。

## 8. 全量重算编排

- **顺序**：① 回填历史日汇率 → ② 导入 payments + 生成快照 + 设开关 → ③ 全量重跑 `tools/order_profit_backfill.py`（2/24 起，**不传 `--rmb`、以 `manual_rate=None` 运行，强制逐单走日汇率链路**；否则会沿用固定 6.83、白回填）覆盖 `order_profit_lines` → ④ 之后增量（open day 实时兜底 `_open_day_freshness` + 日终重算 + 每周 payments 后的待对账替换）。
- **可重入**：backfill 按月分批、可重跑；`order_profit_runs.summary_json` 记录策略版本 / 汇率模式 / 来源计数。

## 9. 数据质量与前端披露

- 每项成本 output 带 `source`（真实 / 各类估算 / 兜底）。沿用 `get_order_profit_status_summary` 的 `estimate_marks`，并**修正现有缺陷**：当前 `estimate_marks.shopify_fee` 无条件标「策略C估算」，应改为按 `shopify_fee_source` 真实区分 `actual_payment` / `dynamic_region_rate` / `strategy_c`。
- 前端（`web/templates/order_profit_dashboard.html` / `_data_quality_bar.html`）展示：真实手续费多少单、估算多少单、待对账多少单；汇率真实覆盖率；缺采购价/物流的产品数。
- 顶层 `data_quality`：payments 断更、汇率断更、动态快照 stale、estimate 占比高 → 至少降级 `warning`（集中在 `appcore/order_analytics/data_quality.py`）。

## 10. 历史数字变动与公告

全量重算后历史利润报表数字会变动（手续费真实→利润升约 $2 万；汇率微调）。需：① 重算前后留存 `order_profit_runs` 对照；② 对业务方一次性公告口径切换日与影响范围。

## 11. 验收标准

- **汇率**：2/24 起历史交易日 `usd_cny_daily_exchange_rates` 无缺口；重算后 `cost_basis.exchange_rate_source=daily_archive` 占比大幅提升、固定 6.83 行清零。
- **手续费**：开关生效后历史订单 `shopify_fee_source` 中 `actual_payment` 覆盖匹配到 payments 的订单（约 63% GMV）；其余标估算/待对账；KPI 区分三类来源。
- **物流/SKU**：fallback 行数下降；缺失产品在看板可见。
- **监控**：payments / 汇率断更告警可触发。
- 全量重算完成、focused 测试通过。

## 12. 测试范围

- **汇率**：历史端点回填 + 三源交叉校验；`get_usd_to_cny_map` 按 `business_date` 取值；回填后重算行 `source` 正确。
- **手续费**：复用 dynamic-shopify-fee spec §13；**新增**：开关设到最早日时历史订单走真实优先、`_should_skip_for_dynamic_fee_boundary` 允许全量覆盖、`estimate_marks` 按真实 `source` 区分。
- **物流/SKU**：降级链取数优先级、缺失暴露。
- 按 `scripts/pytest_related.py --base origin/master --run` 选 focused；至少 `tests/test_profit_calculation.py tests/test_order_profit_aggregation.py tests/test_shopify_fee.py tests/test_order_analytics_data_quality.py`。

## 13. 风险与回滚

- 全量重算改历史数字 → 留 `order_profit_runs` 快照对照；可按窗口重跑回退旧口径（保留旧 backfill 参数）。
- frankfurter 历史端点限流/失败 → 分批 + 重试 + 降级 30 天均值；不阻塞重算。
- payments 匹配店铺串单 → 沿用 dynamic-shopify-fee spec §8 的店铺前缀隔离。
- 手续费回刷使部分历史单变「待对账」→ `data_quality` 披露，随每周 payments 收敛。

## 14. 实施顺序

1. 汇率历史端点回填 + 三源校验 + 一次性回填能力。
2. 手续费：设开关、payments 导入生成快照、改 `_should_skip_for_dynamic_fee_boundary` 允许全量覆盖。
3. `estimate_marks` 按真实 `source` 区分 + 前端披露。
4. payments / 汇率断更巡检告警（登记 `scheduled_tasks`）。
5. SKU/物流缺失暴露与督促入口。
6. 全量重算 + 业务方公告。
7. focused tests。

## 15. 待实施验证点

- frankfurter 历史端点批量回填的稳定性 / 限流阈值。
- 全历史开关开启后，`shopify_fee_rate_snapshots` 对早期月份（payments 可能未覆盖）的样本充足度；不足时按 spec 回退策略C。
- 全量重算耗时与对线上读路径的影响（建议低峰跑）。
