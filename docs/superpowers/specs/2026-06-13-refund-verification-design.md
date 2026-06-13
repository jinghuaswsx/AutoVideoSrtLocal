# 2026-06-13 退款核验（Refund Verification）

## Anchors

- `AGENTS.md`：数据分析模块改动必须文档先行，非 hotfix 在 worktree 中开发。
- `web/templates/CLAUDE.md`：新 POST / fetch 必须带 CSRF；新页面和 API 需要登录、权限守卫。
- `web/static/CLAUDE.md`：Ocean Blue 后台视觉、三态、移动端适配；任务流转 UI 必须闭环。
- `appcore/order_analytics/CLAUDE.md`：数据分析模块边界；实时快照按 `(business_date, ad_account_id)` 取；本工具走 override 层，不污染现有分析数据源。
- 关联背景：`docs/superpowers/specs/2026-06-10-shopify-unsettled-payout-ledger-design.md`（同为 Shopify Payments 导出工具，但那只做存档、不回写核算）。

## Background / Problem

订单利润核算里的「退款」当前是按 `营收 × 固定 1%` 计提（`return_reserve`，见 `appcore/order_analytics/profit_calculation.py`），**实际退款不进核算**——`realtime.py:1662` 的二选一 `profit_deduction = return_reserve if has_package_profit_lines else refund_deduction` 让正常已核算订单一律走固定 1%。

2026-06-13 直连生产核实：`dianxiaomi_order_lines` 约 3.29 万成交单仅 15 笔有 `refund_amount_usd`、`order_state` 字段全空，店小秘侧退款数据基本没同步。但 **Shopify Payments 导出里带真实退款交易**（`type='refund'`，含 `order_name` 与金额），且这些行在导过 Payments 的情况下已落在 `shopify_payments_transactions` 表中，目前只用于手续费对账、未用于退款核算。

本功能把这份真实退款数据，经人工核验后接进大盘核算，修正退款口径。

## Goal

在 `/order-analytics` 顶栏新增「退款核验」TAB（与「实时大盘」同级）：

1. admin 上传 Shopify Payments 导出（必）+ Shopify 订单导出（选），系统解析出每个订单的真实退款金额与退款状态。
2. 通过 `order_name = dianxiaomi_order_lines.extended_order_id` 关联到店小秘订单/包裹，生成一个**待核验批次**，展示核对结果（匹配/未匹配/异常、实测退款总额 vs 当前 1% 计提的差额）。
3. admin 确认「应用」后，写入独立 override 层；大盘实时大盘与产品盈亏的退款扣减改为 `max(实测退款, 1% 计提)`，利润 / ROAS 随之修正。
4. 支持回滚已应用批次，恢复到 1% 口径。
5. 原始数据源（`dianxiaomi_order_lines`、`shopify_orders`、`shopify_payments_transactions`、`order_profit_lines`）一行不改。

## Non-Goals

- 不自动从 Shopify API 拉取退款（仍走 admin 手动上传 CSV）。
- 不修复店小秘 `order_state` / `refund_amount_usd` 同步链路（属于另一项工作）。
- 不重算 `order_profit_lines` 预存值；覆盖只发生在查询组装层。
- 不下沉退款到产品级广告费分摊等其它口径。

## Confirmed Decisions

1. **数据源**：Payments 导出（出真实退款金额）+ 订单导出（出退款状态），两者交叉补全。Payments 是金额唯一可信来源。
2. **核算口径**：每个订单 `max(实测退款, 该订单 1% 计提)`。1% 退化为最低退货准备金保险，对冲退款滞后与导入不全导致的利润虚高。
3. **生效方式**：核验确认后才写入大盘（导入 → 展示核对 → admin 确认「应用」→ 生效），可回滚。

## Architecture

采用**独立 override 表 + 核算层覆盖**（评审中称方案 A）。

不选「直接回填 `dianxiaomi_order_lines.refund_amount_usd`」（方案 B），原因：
- B 会污染店小秘原始导入数据，下次店小秘重新同步订单可能覆盖核验结果；
- 大盘对已核算订单走的是 `return_reserve` 分支（`realtime.py:1662`），回填 `refund_amount_usd` 根本不会被读到，除非再改核算逻辑。

方案 A 直接在「算 1%」那一步做 `max` 覆盖，对症、可追溯、可回滚。

```
[退款核验 Tab]
   │ 上传 Payments CSV(必) + 订单 CSV(选)
   ▼
[解析]  Payments 取 type∈{refund,chargeback} 按 order_name 聚合真实金额(负数取绝对值);
   │     订单 CSV 取 financial_status∈{refunded,partially_refunded} 补状态
   ▼
[关联]  order_name 规整后 = dianxiaomi_order_lines.extended_order_id → 落到 dxm_package_id 集合
   ▼
[核验批次]  pending: 展示 匹配/未匹配/异常 + 实测总额 vs 当前 1% 差额
   ▼  admin 确认「应用」
[refund_verifications 表]  status: pending → applied (记录来源/金额/核验时间)
   ▼
[大盘核算]  realtime.py + order_profit_aggregation.py 的 return_reserve 那步
            经 _apply_refund_verification_adjustments 改为 max(实测, 1%);失效受影响业务日缓存
```

## Data Model

新增 migration `db/migrations/2026_06_13_refund_verification_tables.sql`（MySQL / InnoDB / utf8mb4，与现有迁移一致）。

### `refund_verification_batches`（一次导入 = 一个批次）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | BIGINT PK AUTO_INCREMENT | |
| `status` | VARCHAR(16) | `pending` / `applied` / `discarded` |
| `source_files` | JSON | `{payments_csv, orders_csv}` 文件名 |
| `site_code` | VARCHAR(16) | 可空，导入时可指定店铺 |
| `matched_count` | INT | 匹配上的退款订单数 |
| `unmatched_count` | INT | 订单号不在店小秘库 |
| `anomaly_count` | INT | 有退款状态但缺金额 / 退款>营收 |
| `total_refund_usd` | DECIMAL(12,4) | 实测退款合计 |
| `current_reserve_usd` | DECIMAL(12,4) | 这些订单当前 1% 计提合计 |
| `delta_usd` | DECIMAL(12,4) | `total_refund_usd − current_reserve_usd` |
| `created_by` | VARCHAR(64) | |
| `created_at` / `applied_at` | DATETIME | |

### `refund_verifications`（批次内每个退款订单一行）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | BIGINT PK AUTO_INCREMENT | |
| `batch_id` | BIGINT | FK → batches |
| `extended_order_id` | VARCHAR(128) | 关联键（规整后） |
| `site_code` | VARCHAR(16) | |
| `refund_amount_usd` | DECIMAL(12,4) | Payments 聚合的真实退款；缺金额时为 NULL |
| `refund_source` | VARCHAR(16) | `payments` / `order_status` / `both` |
| `order_financial_status` | VARCHAR(32) | 订单 CSV 来的状态，可空 |
| `matched_package_ids` | JSON | 关联到的 `dxm_package_id` 列表 |
| `match_status` | VARCHAR(16) | `matched` / `unmatched` / `anomaly` |
| `note` | VARCHAR(255) | |
| `status` | VARCHAR(16) | 随批次：`pending` / `applied` / `discarded` |
| `created_at` | DATETIME | |

索引：`refund_verifications(extended_order_id, status)`、`refund_verifications(batch_id)`。

核算只读「每个 `extended_order_id` 最新一条 `status='applied'`」记录。

## Data Flow

1. **解析**
   - Payments：`type IN ('refund','chargeback')`，金额取绝对值，按 `order_name` 求和。chargeback 默认计入（导入时可关）。
   - 订单 CSV：`financial_status IN ('refunded','partially_refunded')` 的订单号，仅补状态。
   - 缺必需列（Payments 的 `Type`/`Order`/`Amount`）→ 400，列名 trim 后精确匹配。
2. **关联**：`order_name` 去 `#`、trim 后匹配 `extended_order_id`；命中得到该订单 `dxm_package_id` 集合。匹配不上 → `unmatched`，绝不模糊匹配。
3. **分类**
   - `matched`：Payments 有金额且关联成功。
   - `anomaly`：Shopify 显示退款但 Payments 无金额（金额留空，核算回退 1%，UI 提示补 Payments）；或退款 > 订单营收。
   - `unmatched`：订单号不在店小秘库。
4. **核验批次**：写 `pending` 批次 + 明细行，返回核对摘要（不触碰核算）。
5. **应用**：admin 确认 → 该批次 `pending → applied`；失效受影响业务日的 `realtime_cache`。
6. **回滚**：`applied` 批次 → `discarded`；核算回退到该订单次新 `applied` 记录或 1%；失效相应缓存。

## Reconciliation Override Mechanism

新增 `_apply_refund_verification_adjustments`，与现有 `_apply_realtime_ad_cost_adjustments`（按包裹加 delta）同构，在 `realtime.py` 与 `order_profit_aggregation.py` 两条链路出口调用：

1. 取每个 `extended_order_id` 最新 `applied` 的 `refund_amount_usd = R`（缺金额跳过，维持 1%）。
2. 该订单各包裹当前 1% 之和 `reserve_sum`。`R` 先经 `min(R, 订单营收)` 兜底。
3. `effective = max(R, reserve_sum)`，`delta = effective − reserve_sum ≥ 0`。
4. 按包裹营收比例把 `delta` 摊回各包裹 `return_reserve_usd`（营收全 0 时按包裹数均摊），`profit` / `profit_with_estimate` 同步下调。
5. 覆盖落在**包裹所属业务日**（`return_reserve` 计提日），不是退款发生日。

幂等：核算读「最新 applied」+ `max` 覆盖（非累加），重复导入/应用不叠加。

未核算订单（走 `refund_deduction` 分支）：同样喂实测退款取 `max`，两个分支口径统一。

## UI / Tab

- `order_analytics.html` 的 `oa-tab` 一排新增 `data-tab="refundVerify"`「退款核验」（置于「未结算货款」旁）；`page()` 加 `active_tab='refundVerify'`，新增 view 路由 `/order-analytics/refund-verify-view`。
- Tab 三区：**上传区**（Payments 必、订单选 +「上传并核对」）、**核对预览区**（汇总卡 + 已匹配/未匹配/异常三个分页表 +「应用到大盘」/「放弃此批次」）、**历史批次区**（已应用列表，可查看明细、可回滚）。
- 闭环：上传→loading→核对摘要+`batch_id`；应用→loading→成功+「去实时大盘看修正后数字」入口；失败就地显示接口名与错误原因。
- Ocean Blue 视觉、三态、移动端适配。

## API Endpoints

全部 `@login_required + @permission_required("data_analytics")`；POST 带 `X-CSRFToken`；JSON 顶层带 `data_quality`。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/order-analytics/refund-verify/import` | 上传两 CSV，建 pending 批次，返回核对摘要 |
| GET | `/order-analytics/refund-verify/batches` | 历史批次列表 |
| GET | `/order-analytics/refund-verify/batches/<id>` | 批次明细（已匹配/未匹配/异常） |
| POST | `/order-analytics/refund-verify/batches/<id>/apply` | 应用，失效缓存 |
| POST | `/order-analytics/refund-verify/batches/<id>/discard` | 放弃 pending 批次 |
| POST | `/order-analytics/refund-verify/batches/<id>/revert` | 回滚已应用批次 |

## Error Boundaries & Data Safety

- **幂等**：见上，`max` 覆盖 + 最新 applied，重复不叠加。
- **退款 > 营收**：`min(退款, 订单营收)` 兜底 + 标 `anomaly`。
- **业务日归属**：覆盖跟包裹业务日走，修正历史那天利润。
- **订单号规整**：去 `#`、trim 后精确匹配。
- **多包裹分摊除零**：营收全 0 时按包裹数均摊。
- **缓存失效**：apply / revert 只失效受影响业务日的 `realtime_cache`，不全量清。
- **权限/CSRF**：未登录 302、无权限 403、POST 无 CSRF 拒绝。

## Testing Plan

- 解析层：refund/chargeback 负数取绝对值并求和、`financial_status` 提取、订单号规整匹配、未匹配/异常分类、缺必需列 400。
- 聚合层：多包裹营收比例分摊、退款>营收 min 兜底、营收 0 防除零。
- 核算覆盖：`max(实测,1%)`、delta 摊回、profit 同步下调、幂等、revert 恢复、业务日归属正确。
- 端点：登录/权限/CSRF/`data_quality`。
- 回归：`tests/test_order_profit_aggregation.py`、`tests/test_product_profit_report.py`、`tests/test_order_analytics_realtime_site_filter.py`、`tests/characterization/test_order_analytics_baseline.py`。

## Affected Files

- 新增 migration：`db/migrations/2026_06_13_refund_verification_tables.sql`
- 新增模块：`appcore/order_analytics/refund_verification.py`（解析、关联、批次、覆盖）
- 改：`appcore/order_analytics/realtime.py`、`appcore/order_analytics/order_profit_aggregation.py`（出口调用 `_apply_refund_verification_adjustments`）
- 改：`web/routes/order_analytics.py`（page active_tab + 6 个端点）
- 改：`web/templates/order_analytics.html`（Tab + 三区 UI）
- 改：`web/static/`（退款核验 JS / 样式）
- 定时任务：本功能无后台定时任务，无需登记 `appcore/scheduled_tasks.py`。

## Rollback

- 功能级：回滚的批次 `applied → discarded`，核算自动回退到次新 applied 或 1%。
- 代码级：override 表与覆盖逻辑独立，下线只需停止调用 `_apply_refund_verification_adjustments`，原始数据与 1% 口径不受影响。
