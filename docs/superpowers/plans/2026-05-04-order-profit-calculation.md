# 订单利润核算 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现端到端单订单（与 SKU 级）边际利润核算，覆盖 Shopify Payments 手续费、广告费按 units 分摊、采购成本、小包物流、1% 退货占用，回填 15,244 条历史订单，并暴露看板与异常告警。

**Architecture:**
- 核心计算放在 `appcore/order_analytics/profit_calculation.py`（纯函数层）+ `cost_allocation.py`（数据装配层）
- 平台手续费规则独立到 `appcore/order_analytics/shopify_fee.py`，按 `docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md` 实施策略 C
- 成本完备性 gate 放在 `appcore/order_analytics/cost_completeness.py`，作为利润计算前置 gate（不完备的 SKU 不出数字、显式标记"成本未完备"）
- 持久化新增 `order_profit_lines`（SKU 行级）+ `order_profit_orders`（订单级聚合视图）
- 校验回路：新建 `shopify_payments_transactions` 表存导入 CSV，按月级反推真实 fee 与策略 C 对比，生成偏差报告
- 复用现有 `appcore.order_analytics.dashboard._aggregate_ads_by_product`（已按 product 聚合 spend）、`material_roas_rmb_per_usd` 配置常量、`appcore.scheduled_tasks` 调度框架
- 不依赖 Shopify Admin API；所有订单事实来自店小秘 `dianxiaomi_order_lines`

**Tech Stack:** Flask、PyMySQL（无 ORM，原生 SQL）、Jinja 模板、原生 JavaScript、pytest、MySQL migration SQL。

---

## 业务规则参考

- Shopify Payments 计费规则：`docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md`
- 完备性字段（业务方维护）：`media_products.purchase_price`（CNY）+ (`packet_cost_actual` ∨ `packet_cost_estimated`)（CNY）
- 汇率：`material_roas_rmb_per_usd` 配置（默认 6.83，admin 可改）
- 决策清单（13 项）：见末尾"附录 A"

## 核算公式

每订单 SKU 行边际利润（USD）：

```
revenue_usd       = line_amount + (该行摊到的运费收入)
                    # 运费摊法：order.shipAmount × (该行 line_amount / 订单内所有行 line_amount 之和)

shopify_fee_usd   = calculate_shopify_fee(
                        amount=revenue_usd,
                        presentment_currency=infer_pc_from_country(buyer_country),
                        card_country=buyer_country,
                    )["fee"]

ad_cost_usd       = (该 SKU 当日总广告 spend × 该行 units)
                    / 该 SKU 当日总 units
                    # 当日 = Asia/Shanghai 自然日（与现有 ROI 体系一致）
                    # 未匹配 product_id 的广告 spend 不进 SKU 分摊，单列 unallocated_ad_spend

purchase_usd      = purchase_price × quantity / rmb_per_usd

shipping_usd      = (packet_cost_actual ?? packet_cost_estimated) × quantity / rmb_per_usd

return_reserve    = revenue_usd × 0.01

profit_usd        = revenue_usd - shopify_fee_usd - ad_cost_usd
                  - purchase_usd - shipping_usd - return_reserve
```

订单级利润 = 该订单内所有 SKU 行 `profit_usd` 求和。

**完备性 gate**：某 SKU 行所属产品若 `purchase_price IS NULL` 或两个 packet_cost 字段都 NULL，则该行 `cost_completeness_status='missing'`、不写入 `profit_usd`、列出缺哪些字段；否则 `status='ok'`、计算 profit。

## 国家→货币推断（策略 C 用）

```python
COUNTRY_TO_CURRENCY = {
    # 欧元区
    "AT": "EUR", "BE": "EUR", "DE": "EUR", "ES": "EUR", "FI": "EUR",
    "FR": "EUR", "IE": "EUR", "IT": "EUR", "LU": "EUR", "NL": "EUR",
    "PT": "EUR", "GR": "EUR", "MT": "EUR", "CY": "EUR", "EE": "EUR",
    "LV": "EUR", "LT": "EUR", "SK": "EUR", "SI": "EUR", "HR": "EUR",
    # 单独货币
    "GB": "GBP", "US": "USD", "AU": "AUD", "CA": "CAD", "NZ": "NZD",
    "CH": "CHF", "SE": "SEK", "NO": "NOK", "DK": "DKK", "PL": "PLN",
    "JP": "JPY", "MX": "MXN", "BR": "BRL",
}
# 默认 fallback: USD
```

## 文件结构

### 新建

- `db/migrations/2026_05_04_order_profit_tables.sql` — 利润核算表 + 完备性日志表 + Shopify Payments CSV 导入表
- `db/migrations/2026_05_05_dianxiaomi_field_repair.sql` — UPDATE 历史 dianxiaomi_order_lines 列字段
- `appcore/order_analytics/shopify_fee.py` — calculate_shopify_fee / verify_fee / classify_tier / infer_pc_from_country
- `appcore/order_analytics/cost_completeness.py` — 完备性纯函数 + 缺失字段返回
- `appcore/order_analytics/cost_allocation.py` — 广告费分摊 + 运费摊到行
- `appcore/order_analytics/profit_calculation.py` — 核心利润纯函数 + 订单级聚合
- `appcore/order_analytics/profit_repository.py` — order_profit_lines 持久化
- `appcore/order_analytics/shopify_payments_import.py` — Payments CSV 导入解析
- `appcore/order_analytics/profit_validation.py` — 真实 fee 反推 + 与策略 C 偏差对比
- `tools/order_profit_backfill.py` — 历史 15,244 条回填脚本
- `tools/order_profit_incremental.py` — 增量同步脚本（注册到 scheduled_tasks）
- `web/routes/order_profit.py` — 利润 API + 完备性 API + Payments CSV 上传
- `web/templates/order_profit_dashboard.html` — 利润看板
- `web/templates/cost_completeness_dashboard.html` — 成本完备性看板
- `web/static/order_profit.js` — 看板交互
- `tests/test_shopify_fee.py` — 含规则文档第 6 节 6 条验证用例
- `tests/test_cost_completeness.py`
- `tests/test_cost_allocation.py`
- `tests/test_profit_calculation.py`
- `tests/test_profit_validation.py`
- `tests/test_order_profit_routes.py`
- `tests/test_dianxiaomi_field_repair.py`

### 修改

- `tools/dianxiaomi_order_import.py` — 修字段映射，把 raw_order_json 里的 `logisticFee` / `refundAmount` / 折算 `amount_cny` 写到 DB 列
- `appcore/order_analytics/dianxiaomi.py` — 同上 import 链路修复
- `appcore/order_analytics/dashboard.py` — 利润字段加到现有 product 看板
- `web/templates/order_analytics.html` — 利润视图入口
- `appcore/scheduled_tasks.py` — 注册 order_profit_incremental 任务（默认每 10 分钟一次）
- `main.py` 或 `web/app.py` — 注册新路由

---

## Task 列表

### 阶段 0：数据修复（dianxiaomi 同步字段）

> 现状：100 条样本里 `logistic_fee` / `amount_cny` / `refund_amount` 列 0% 填充，但 `raw_order_json` 里 `logisticFee` 84% 有值。下游 SQL 不能直接用 raw_json，必须修。

#### Task 0.1: 修同步脚本字段映射

- [ ] **Step 1**：在 `tests/test_dianxiaomi_field_repair.py` 写测试：给定一个真实 raw_order_json（来自 `tests/fixtures/dianxiaomi_sample_order.json`，从生产摘一条脱敏），断言 `parse_order_to_db_columns()` 返回的 dict 含 `logistic_fee=61.61`、`amount_cny=252.32`（USD 36.94 × 6.83）、`refund_amount=0`。
- [ ] **Step 2**：跑 `pytest tests/test_dianxiaomi_field_repair.py -q`，应失败。
- [ ] **Step 3**：在 `tools/dianxiaomi_order_import.py` 与 `appcore/order_analytics/dianxiaomi.py` 中找到 raw → DB 列的映射函数（约 `_parse_*` 系列），补 `logistic_fee` / `amount_cny` / `refund_amount` 三个字段；汇率读 `material_roas_rmb_per_usd`。
- [ ] **Step 4**：跑测试通过；跑 `pytest tests/test_order_analytics_dianxiaomi.py -q` 确认现有同步链路 baseline 没破。

#### Task 0.2: 历史回填迁移

- [ ] **Step 1**：写 `db/migrations/2026_05_05_dianxiaomi_field_repair.sql`，对 15,244 条历史订单 UPDATE：
  - `logistic_fee = JSON_EXTRACT(raw_order_json, '$.logisticFee')` (when not null)
  - `amount_cny = order_amount * <rmb_per_usd_from_settings>`
  - `refund_amount = JSON_EXTRACT(raw_order_json, '$.refundAmount')`
- [ ] **Step 2**：先 dry-run 跑 SELECT 版本验证条数和样例值；然后正式 UPDATE。
- [ ] **Step 3**：复检 SQL：`SELECT COUNT(*) FROM dianxiaomi_order_lines WHERE logistic_fee IS NOT NULL` 应 ≥12,000。

---

### 阶段 1：Shopify Payments 策略 C（4 档手续费纯函数）

#### Task 1.1: calculate_shopify_fee 纯函数

- [ ] **Step 1**：在 `tests/test_shopify_fee.py` 写规则文档第 6 节 6 条验证用例：

```python
# 来自 docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md §6
@pytest.mark.parametrize("amount,currency,country,expected_fee,expected_tier", [
    (19.94, "USD", "US", 0.80, "A"),
    (30.94, "USD", "GB", 1.38, "B"),
    (22.13, "EUR", "US", 1.19, "C"),
    (22.13, "EUR", "DE", 1.41, "D"),
    (20.94, "EUR", "DE", 1.35, "D"),
    (47.79, "EUR", "US", 2.21, "C"),
])
def test_calculate_shopify_fee_matches_real_data(amount, currency, country, expected_fee, expected_tier):
    result = calculate_shopify_fee(amount=amount, presentment_currency=currency, card_country=country)
    assert abs(result["fee"] - expected_fee) <= 0.02
    assert result["tier"] == expected_tier
```

- [ ] **Step 2**：跑测试，应失败。
- [ ] **Step 3**：在 `appcore/order_analytics/shopify_fee.py` 实现 `calculate_shopify_fee`、`verify_fee`、`classify_tier`、`estimate_net_income`，按规则文档第 5 节伪代码。
- [ ] **Step 4**：测试通过。

#### Task 1.2: 国家→货币推断

- [ ] **Step 1**：写测试覆盖欧元区（DE/IT/FR/ES/PT/IE）→ EUR、GB → GBP、US → USD、未知国家 → USD。
- [ ] **Step 2**：实现 `infer_presentment_currency_from_country(country: str) -> str`。
- [ ] **Step 3**：测试通过。

#### Task 1.3: card_country 代理（buyerCountry）

- [ ] 集成测试：从 dianxiaomi raw_json 取 `dxmPackageAddr.country`（即 buyerCountry），传入 `calculate_shopify_fee`。说明性 docstring 标注 "本店首版假设客户用本地卡，未来可由 CSV 反推校准"。

---

### 阶段 2：成本完备性 gate

#### Task 2.1: 完备性纯函数

- [ ] **Step 1**：在 `tests/test_cost_completeness.py` 写测试：
  - 完备：`purchase_price=15.50, packet_cost_actual=20.50` → `{ok: True, missing: []}`
  - 缺采购价：`purchase_price=None, packet_cost_actual=20.50` → `{ok: False, missing: ['purchase_price']}`
  - 缺包装实际、有预估：`purchase_price=15.50, packet_cost_actual=None, packet_cost_estimated=18` → `{ok: True, missing: [], using: 'estimated'}`
  - 全缺：→ `{ok: False, missing: ['purchase_price', 'packet_cost']}`
- [ ] **Step 2/3**：实现 `appcore/order_analytics/cost_completeness.py:check_sku_cost_completeness(product_row)`。

#### Task 2.2: 完备性看板查询

- [ ] **Step 1**：写测试 `get_completeness_overview()` 返回所有产品 + 状态 + 缺失字段 + 该产品过去 30 天的订单数 / GMV（让业务方按"待补录订单影响"排序，先补热销）。
- [ ] **Step 2/3**：实现 `appcore/order_analytics/cost_completeness.py:get_completeness_overview()`。

---

### 阶段 3：广告费分摊（按 units）

#### Task 3.1: 当日 SKU units 聚合

- [ ] **Step 1**：写测试 `get_sku_daily_units(product_id, business_date)` 返回该 SKU 当日 units 总数。
- [ ] **Step 2/3**：实现，复用 `dianxiaomi_order_lines.product_id` + `paid_at`。注意时区按 Asia/Shanghai 算自然日。

#### Task 3.2: 当日 SKU 广告 spend 聚合

- [ ] **Step 1**：写测试 `get_sku_daily_ad_spend(product_id, business_date)` 复用现有 `_aggregate_ads_by_product` 的逻辑，但改成单日。
- [ ] **Step 2/3**：实现。注意：如果 `product_id IS NULL` 的 spend 单独求和，作为 `unallocated_ad_spend_daily`。

#### Task 3.3: SKU 行分摊广告费纯函数

- [ ] **Step 1**：写测试：
  - 当日 SKU X 总 spend = $100，总 units = 10，订单行 units = 2 → 该行分摊 = $20
  - 当日 SKU X 总 units = 0（数据异常，理论不该发生）→ 返回 0 + 报警
  - 当日 SKU X spend = 0（没投广告） → 返回 0
- [ ] **Step 2/3**：实现 `appcore/order_analytics/cost_allocation.py:allocate_ad_cost_to_line()`。

#### Task 3.4: 运费摊到行

- [ ] **Step 1**：写测试：订单 `shipAmount=6.99`，订单内 2 个 SKU 行 `line_amount=29.95, 19.95` → 摊运费 `4.19, 2.80`（按 line_amount 比例）。
- [ ] **Step 2/3**：实现 `cost_allocation.py:allocate_shipping_to_line()`。

---

### 阶段 4：核心利润核算

#### Task 4.1: 单 SKU 行 profit 纯函数

- [ ] **Step 1**：写测试，输入完整字典（含所有公式输入），断言 `profit_usd` 与逐项分解（`revenue_usd / shopify_fee_usd / ad_cost_usd / purchase_usd / shipping_usd / return_reserve`）。固定汇率 6.83 写测试。
- [ ] **Step 2/3**：实现 `appcore/order_analytics/profit_calculation.py:calculate_line_profit()`。
- [ ] **Step 4**：边界测试：完备性失败时返回 `{status: 'incomplete', missing: [...]}`，**不**计算 profit_usd。

#### Task 4.2: 订单级聚合

- [ ] **Step 1**：写测试 `aggregate_order_profit(order_lines: list)` 求和。如订单内有 1 个 SKU 行 incomplete，订单层标记 `partially_complete` 并返回已知行的 profit 求和 + 未知行的列表。
- [ ] **Step 2/3**：实现。

#### Task 4.3: 持久化层

- [ ] **Step 1**：先写 migration `db/migrations/2026_05_04_order_profit_tables.sql`：

```sql
CREATE TABLE IF NOT EXISTS order_profit_lines (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  dxm_order_line_id BIGINT NOT NULL,
  product_id INT,
  business_date DATE NOT NULL,
  paid_at DATETIME,
  buyer_country VARCHAR(8),
  presentment_currency VARCHAR(8),
  shopify_tier VARCHAR(8),
  -- 收入侧
  line_amount_usd DECIMAL(12,4),
  shipping_allocated_usd DECIMAL(12,4),
  revenue_usd DECIMAL(12,4),
  -- 成本侧
  shopify_fee_usd DECIMAL(12,4),
  ad_cost_usd DECIMAL(12,4),
  purchase_usd DECIMAL(12,4),
  shipping_cost_usd DECIMAL(12,4),
  return_reserve_usd DECIMAL(12,4),
  -- 结果
  profit_usd DECIMAL(12,4),
  status ENUM('ok','incomplete','error') NOT NULL,
  missing_fields JSON,
  cost_basis JSON,  -- {purchase_price_cny, packet_cost_using, rmb_per_usd, ...}
  computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source_run_id BIGINT,
  UNIQUE KEY uk_profit_line_dxm (dxm_order_line_id),
  KEY idx_profit_business_date (business_date),
  KEY idx_profit_product_status (product_id, status),
  KEY idx_profit_buyer_country (buyer_country),
  KEY idx_profit_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SKU 行级订单利润核算结果';

CREATE TABLE IF NOT EXISTS order_profit_runs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_code VARCHAR(64) NOT NULL,
  status ENUM('running','success','failed','partial') NOT NULL,
  window_start_at DATETIME,
  window_end_at DATETIME,
  rmb_per_usd DECIMAL(10,4),
  return_reserve_rate DECIMAL(6,4),
  lines_total INT DEFAULT 0,
  lines_ok INT DEFAULT 0,
  lines_incomplete INT DEFAULT 0,
  lines_error INT DEFAULT 0,
  unallocated_ad_spend_usd DECIMAL(14,4) DEFAULT 0,
  error_message MEDIUMTEXT,
  summary_json JSON,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME,
  KEY idx_profit_runs_started (started_at),
  KEY idx_profit_runs_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS shopify_payments_transactions (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  payout_id VARCHAR(64),
  transaction_id VARCHAR(64),
  type VARCHAR(32),
  order_name VARCHAR(64),
  presentment_currency VARCHAR(8),
  amount_usd DECIMAL(12,4),
  fee_usd DECIMAL(12,4),
  net_usd DECIMAL(12,4),
  card_brand VARCHAR(32),
  inferred_card_origin VARCHAR(16),
  inferred_tier VARCHAR(8),
  matches_standard TINYINT,
  imported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source_csv VARCHAR(255),
  raw_row_json JSON,
  UNIQUE KEY uk_shopify_payments_txn (transaction_id),
  KEY idx_shopify_payments_order (order_name),
  KEY idx_shopify_payments_imported (imported_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2**：写 `appcore/order_analytics/profit_repository.py:upsert_profit_line(line_dict)`，按 `dxm_order_line_id` upsert。
- [ ] **Step 3**：测试覆盖 insert / 重跑覆盖 / status=incomplete 也能写入。

---

### 阶段 5：未匹配广告费单列

#### Task 5.1: 单列 unallocated_ad_spend

- [ ] **Step 1**：写测试 `get_unallocated_ad_spend(business_date)` 返回当日 `product_id IS NULL` 的 spend 之和。
- [ ] **Step 2/3**：实现，写到 `order_profit_runs.unallocated_ad_spend_usd`。
- [ ] **Step 4**：在看板上单独显示这个数字（"未分摊广告成本：$XX，待补 campaign-product 配对"）。

> **后续扩展（不在本 plan 内）**：增加 admin 页"未匹配 campaign 列表 + 手工配对"功能，把未匹配的 campaign 名手工绑定到 product，回填 `meta_ad_daily_campaign_metrics.product_id`。

---

### 阶段 6：历史回填

#### Task 6.1: 回填脚本

- [ ] 写 `tools/order_profit_backfill.py`，支持 `--from YYYY-MM-DD --to YYYY-MM-DD --dry-run`。
- [ ] 按月分批（避免一次跑爆内存）；每批先 SELECT 出该月 dxm_order_line + product_row + 当日聚合 spend/units，调 `calculate_line_profit`，批量 upsert 到 `order_profit_lines`。
- [ ] dry-run 模式只打印 N 条/月示例 + 不写 DB。
- [ ] 写到 `order_profit_runs` 一条 task_code='backfill' 的运行记录。

#### Task 6.2: 跑全量回填（手工触发）

- [ ] 先小窗 dry-run：`--from 2026-04-01 --to 2026-04-07 --dry-run`，目检 30 行打印结果是否合理（特别是 incomplete 行数比例与 35.5% 完备 GMV 一致）。
- [ ] 全量：`--from 2026-02-25 --to 2026-05-04`。预期 `lines_ok ≈ 6,310`、`lines_incomplete ≈ 8,934`、占比 41.4% / 58.6%。
- [ ] **Note**：**业务方先把成本完备性补到目标产品后再跑全量**，否则大量行 status=incomplete。可以先小窗 dry-run 看缺失分布。

---

### 阶段 7：增量同步

#### Task 7.1: 增量脚本

- [ ] 写 `tools/order_profit_incremental.py`：
  - 找出 `dianxiaomi_order_lines.updated_at > last_run.window_end_at` 的订单行
  - 对应当日 SKU spend/units 重新算（避免漏更新当天）
  - upsert 到 `order_profit_lines`
- [ ] 注册到 `appcore/scheduled_tasks.py`，默认每 10 分钟一次（与现有 ROI 同步频率一致）。

#### Task 7.2: 完备性变化触发重算

- [ ] 监听 `media_products` 的 `purchase_price` / `packet_cost_*` 变化（通过 admin 保存路径埋点），把对应 product_id 的近 N 天订单行加入"待重算"队列；增量脚本下次运行时重跑。
- [ ] 简单实现：在 `web/routes/medias` 保存路径里调一个 `enqueue_product_recompute(product_id, lookback_days=90)`，写到一个轻量 `order_profit_recompute_queue` 表（可包含在 task 4.3 的 migration 里）。

---

### 阶段 8：报表 + 看板 UI

#### Task 8.1: 利润看板路由

- [ ] `web/routes/order_profit.py` 新增 endpoint：
  - `GET /api/order_profit/summary?from=...&to=...&store=...&country=...&product_id=...` → 时段聚合
  - `GET /api/order_profit/lines?...` → 明细分页
  - `GET /api/order_profit/loss_alerts` → 亏损订单列表（profit_usd < 0）
  - `GET /api/cost_completeness/overview` → 完备性看板数据

#### Task 8.2: 利润看板前端

- [ ] `web/templates/order_profit_dashboard.html` 4 卡片 + 1 表格：
  - 卡片：总营收、总利润、毛利率、未分摊广告成本
  - 表格：按 SKU 分组 profit / units / orders，亏损 SKU 红字高亮
  - 时间筛选：复用 `order_analytics.html` 的时段控件
- [ ] `web/templates/cost_completeness_dashboard.html`：
  - 表格列：产品名 / 缺哪些字段 / 待补订单数 / 待补 GMV / 跳转到 `素材管理 → ROAS` 维护
  - 缺失越多、订单越多的产品排在最前

#### Task 8.3: 异常告警

- [ ] 看板顶部一个 banner，显示当月：
  - 亏损订单数 / 占比
  - 未分摊广告成本占比
  - 不完备 SKU 数

---

### 阶段 9：定期校验回路（Shopify Payments CSV）

#### Task 9.1: CSV 上传 + 解析

- [ ] `web/routes/order_profit.py` 加 endpoint `POST /api/order_profit/payments_csv/import`，接受 multipart 上传。
- [ ] 解析 Shopify Payouts CSV（标准列：`Transaction Date / Type / Order / Amount / Fee / Net / Card Brand / Presentment Amount / Presentment Currency`）。
- [ ] 写到 `shopify_payments_transactions` 表，按 `transaction_id` upsert。

#### Task 9.2: 反推 + 偏差对比

- [ ] 写 `appcore/order_analytics/profit_validation.py:reconcile_against_csv(month)`：
  - 对该月所有 `shopify_payments_transactions` 行调 `verify_fee()` 反推 card_origin / matches_standard
  - 跟同月 `order_profit_lines.shopify_fee_usd`（策略 C 估算）对比
  - 输出报告：估算总额、真实总额、偏差、按档位分组、按国家分组
  - 给出"参数调整建议"（比如 "策略 C 在 EUR/D 档低估 ~3%，可考虑 buyerCountry 假设保留率改 X%"）

#### Task 9.3: 偏差报告 admin 页

- [ ] 简单页面 `web/templates/payment_fee_reconciliation.html`，显示 reconcile 报告 + 历史偏差走势。

---

## 风险与已知约束

1. **退款不处理**：单订单利润不含真实退款数据，仅扣 1% 退货占用作为风险准备。如未来退货率显著偏离 1%，可在 admin 配置项调整 `return_reserve_rate`。
2. **VAT/IOSS 不处理**：欧盟订单不扣 VAT，假设 DDU 模式（客户自付）。`raw_order_json.iossVat` 100% false 支持此假设。如未来切 DDP，需新增 VAT 字段并扩展公式。
3. **广告归因错配**：当日 spend 摊到当日订单，跟 Meta 7 天点击 + 1 天浏览归因窗不严格对齐。这是行业普遍的粗糙做法，不在本 plan 修复。
4. **未匹配广告费单列**：第一版只展示数字、不强制摊到订单。后续需要"campaign-product 人工配对"功能（独立 plan）。
5. **采购价币种 = RMB**：依赖 `material_roas_rmb_per_usd` 配置常量；CSV 校验阶段可反推真实加权汇率，业务方手工调整该配置。
6. **`tk_sea_cost` / `tk_air_cost` 未维护**：第一版不纳入；如未来要加头程，公式扩展即可（增加一项 `head_haul_usd`）。
7. **业务方需先补成本数据**：不补则 58.6% GMV 的订单 status=incomplete，看板会显示大量"待补录"。完备性看板可帮业务方按订单影响排序优先补哪些产品。
8. **历史套餐变化**：本 plan 假设全部 15,244 历史订单都用 2.5%+0.30 套餐。若实际有套餐切换日（业务方提供切换日期）需在 calculate_shopify_fee 里加分段逻辑。
9. **店小秘 16% 订单 logisticFeeErr**：这部分订单的 `logisticFee` 缺失，但本 plan 用 `media_products.packet_cost_actual` 作为成本来源（不依赖订单维度的 logistic_fee），所以**不阻塞核算**。但要在完备性日志里把这部分标出来，方便对账。
10. **Shopify Payments CSV 校验是离线流程**：不实时，业务方按月手工导入；阶段 9 不阻塞前面阶段上线。

---

## 实施顺序与里程碑

| 里程碑 | 包含 Task | 产出 |
|---|---|---|
| **M1 数据基础** | 阶段 0 | dianxiaomi 字段修好；历史回填正常 |
| **M2 计算核** | 阶段 1-4 | 单 SKU 行能算出 profit；持久化跑通 |
| **M3 历史回填** | 阶段 5-6 | 15,244 条订单全部入 order_profit_lines（含 incomplete）|
| **M4 看板上线** | 阶段 7-8 | 业务方能看利润看板 + 完备性看板 |
| **M5 校验闭环** | 阶段 9 | Shopify Payments CSV 导入 + 反推对账 |

**第一个 PR 范围建议**：M1 + M2 阶段 1（Shopify Fee 纯函数 + 测试），约 5-6 个文件、~300 行代码、独立可合并。

---

## 附录 A：决策清单（13 项）

| # | 项 | 决策 |
|---|---|---|
| 路线 | 实施深度 | C 路线（精确版），退货逆向先不算（用退货率 1% 估算占用） |
| Q1 | 核算粒度 | SKU 级 + 订单级（订单级 = SKU 级求和） |
| Q2 | 订单数据源 | 店小秘 API（Shopify Admin API 路线放弃，因没现成凭证、也不想新建 OAuth 流程） |
| Q3 | 业务平台 | 实质只有 Shopify（newjoy + omurio 两店；店小秘 100% platform=shopify） |
| Q4 | 无成本订单 | 强约束：成本未完备 → 不出利润数字、显式标"待补录"，不估算 |
| Q5 | 采购价维护入口 | 业务方走"素材管理 → ROAS 按钮"已有入口自维护，开发不做 |
| Q6 | 平台扣点 | 按 `docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md`：4 档判定 + 策略 C 前向预估 |
| Q7 | 校验回路 | 业务月/周从 Shopify 后台导出 Payments CSV 给开发导入数据库，反推真实 fee 校验策略 C，仅校验不影响日常实时计算 |
| Q8 | 广告费分摊算法 | 按数量（units）比例分摊到 SKU 级订单行 |
| Q9 | 未匹配广告费 | 单列 `unallocated_ad_spend`，不进单订单核算；后续做 campaign-product 人工配对兜底 |
| Q10 | 时间范围 | 历史全量回填（15,244 条订单） |
| Q11 | 完备性字段 | 完备 = `purchase_price` + (`packet_cost_actual` ∨ `packet_cost_estimated`) |
| 退货 | 退货成本占用 | 营收 × 1% 直接扣减；不做退货明细处理 |
| 汇率 | RMB↔USD | 复用现有 `material_roas_rmb_per_usd`（默认 6.83，admin 可改），不建汇率历史表 |
| VAT | VAT/IOSS | 第一版不处理，假设 DDU 模式 |
