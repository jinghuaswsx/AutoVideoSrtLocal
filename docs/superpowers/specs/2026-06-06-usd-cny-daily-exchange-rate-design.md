# USD/CNY 每日基准汇率归档与核算口径

## 文档锚点

- 全局开发规则：[AGENTS.md](../../../AGENTS.md) 的“文档驱动代码”和“定时任务一律登记”。
- 订单利润核算规则：[appcore/order_analytics/CLAUDE.md](../../../appcore/order_analytics/CLAUDE.md)。
- 订单利润表结构：[2026-05-04 order profit tables](../../../db/migrations/2026_05_04_order_profit_tables.sql)。
- 订单级小包成本换算：[2026-05-04-order-level-shipping-cost-design.md](2026-05-04-order-level-shipping-cost-design.md)。
- SKU 实际保本 ROAS 快照：[2026-05-10-sku-actual-breakeven-roas-design.md](2026-05-10-sku-actual-breakeven-roas-design.md)。
- 调度登记规则：[2026-06-04-ad-order-sync-schedule-design.md](2026-06-04-ad-order-sync-schedule-design.md)。

## 背景

订单利润、SKU 实际保本 ROAS 等数据分析里，采购价、小包成本等人民币成本会换算成 USD。历史实现使用 `system_settings.material_roas_rmb_per_usd` 作为单一配置汇率；这会导致跨日期窗口里所有订单被同一个汇率重算，无法保留当天的汇率基准。

用户确认的新要求：

- 每天北京时间 06:00 同步最新 USD/CNY 汇率。
- 同步结果作为当天的基准汇率。
- 每天存档，后续可追溯当天用了哪个汇率。
- 核算与数据分析按各自业务日期使用当天汇率计算。
- 汇率必须交叉验证，不能只信单一来源。

## 目标

1. 新增每日 USD/CNY 基准汇率归档表，每个北京时间自然日最多一个有效基准。
2. 每天 06:00 自动拉取三个独立来源：一个主源 + 两个校验源。
3. 三个来源都返回有效 USD/CNY 且三源之间最大相对差异不超过 5% 时，才写入当天基准。
4. 订单利润核算按每行 `meta_business_date` 读取当天汇率，写入 `cost_basis.rmb_per_usd` 和汇率来源字段。
5. SKU 实际保本 ROAS 的 30 天窗口按每条订单行的 `meta_business_date` 读取当天汇率；显式传入 `--rmb-per-usd` 时保留手工覆盖能力。
6. 新任务必须登记到 `appcore/scheduled_tasks.py`，并提供 systemd service/timer。
7. 提供只读 URL 入口查看最近归档的汇率数据。

## 非目标

- 不改素材管理手工 ROAS 表单的即时测算口径；该页面没有业务日期上下文，继续使用 `material_roas_rmb_per_usd`。
- 不回填历史所有日期的真实汇率；历史缺档日期仍可用旧配置汇率兜底，并在结果里标记 fallback。
- 不把 LLM/API 账单里的 `config.USD_TO_CNY` 一并改成每日汇率；账单历史需要单独迁移设计。

## 汇率来源与交叉验证

默认主源：

- Frankfurter：`https://api.frankfurter.app/latest?from=USD&to=CNY`
- 响应字段：`date`、`rates.CNY`

默认校验源：

- Open ER API：`https://open.er-api.com/v6/latest/USD`
- 响应字段：`time_last_update_utc`、`rates.CNY`
- FloatRates：`https://www.floatrates.com/daily/usd.json`
- 响应字段：`cny.date`、`cny.rate`

验收规则：

- 三个来源都必须成功返回正数。
- `base` 必须是 USD，`quote` 必须是 CNY。
- 对三源做两两比较；最大相对差异 `abs(a - b) / ((a + b) / 2)` 必须 `<= 0.05`（5%）。
- 通过验证后，写入当天北京时间日期 `rate_date`；实际 `source_date` 单独保存，允许公共源在周末或节假日返回最近一个交易日。
- 默认采用主源汇率作为当天基准，两个校验源只用于异常保护。
- 未通过验证时不写入当天基准，定时任务写 `scheduled_task_runs.status='failed'`，summary 带三个来源原始结果和最大差异。

## 数据模型

新增表 `usd_cny_daily_exchange_rates`：

- `rate_date DATE NOT NULL`：北京时间自然日，也是核算查询 key。
- `usd_to_cny DECIMAL(12,6) NOT NULL`：当天基准汇率，1 USD = N CNY。
- `primary_source` / `primary_rate` / `primary_source_date`。
- `validator_quotes_json`：两个校验源的标准化 quote 数组。
- `max_relative_diff_ratio` / `tolerance_ratio`。
- `source_payload_json`：保留三个来源的裁剪后原始响应。
- 唯一键：`(rate_date)`。

只把验证通过的基准写入本表；失败尝试留在 `scheduled_task_runs.summary_json`。

## 核算口径

订单利润：

- `tools/order_profit_backfill.py` 在未传 `rmb_per_usd` 时，对每行用 `meta_business_date` 查询 `usd_cny_daily_exchange_rates.rate_date`。
- 命中归档时，`calculate_line_profit(..., rmb_per_usd=当天汇率)`。
- `cost_basis` 追加：
  - `exchange_rate_source = "daily_archive"`
  - `exchange_rate_date = "YYYY-MM-DD"`
  - `exchange_rate_source_id`
- 若缺少当天归档，历史兼容兜底到 `system_settings.material_roas_rmb_per_usd`，并写：
  - `exchange_rate_source = "configured_fallback"`
  - `exchange_rate_date = null`
- 显式传入 `rmb_per_usd` 时作为手工覆盖，写 `exchange_rate_source = "manual_override"`。

SKU 实际保本 ROAS：

- 未显式传 `rmb_per_usd` 时，按每条订单行的 `meta_business_date` 查每日归档汇率。
- 显式传 `rmb_per_usd` 时保持固定汇率覆盖，用于临时调试。
- 输出 summary 记录汇率模式和 fallback 次数。

## 定时任务

新增任务：

- `task_code = "usd_cny_exchange_rate_sync"`
- systemd timer：`autovideosrt-usd-cny-exchange-rate-sync.timer`
- `OnCalendar=*-*-* 06:00:00`
- runner：`tools/usd_cny_exchange_rate_sync.py`
- `log_table = "scheduled_task_runs"`

任务 summary 至少包含：

```json
{
  "rate_date": "2026-06-06",
  "usd_to_cny": 6.7656,
  "primary": {"source": "frankfurter", "rate": 6.7656, "source_date": "2026-06-05"},
  "validators": [
    {"source": "open_er_api", "rate": 6.792761, "source_date": "2026-06-06"},
    {"source": "floatrates", "rate": 6.768848, "source_date": "2026-06-05"}
  ],
  "max_relative_diff_ratio": 0.0040,
  "tolerance_ratio": 0.05
}
```

## 查看入口

新增只读 JSON 入口：

```text
GET /order-analytics/exchange-rates?limit=30
```

权限：`@login_required + @permission_required("data_analytics")`。

响应包含最近 `limit` 天归档记录，字段包括当天基准汇率、主源 quote、两个校验源 quote、最大相对误差、容差和同步时间。`limit` 限制在 1 到 365。

## 测试计划

- 汇率服务单测：
  - 三个来源通过时写入当天基准。
  - 三源最大差异超过 5% 时抛错且不 upsert。
  - 查某日期汇率命中归档；缺档时 fallback 到配置汇率。
  - 最近归档列表返回主源、两个校验源、最大相对误差和同步时间。
- 订单利润回填测试：
  - 同一窗口不同 `meta_business_date` 使用不同汇率。
  - `cost_basis` 写入 `exchange_rate_source` 与 `exchange_rate_date`。
- SKU 实际保本 ROAS 测试：
  - 未传手工汇率时按行日期使用不同汇率。
  - 传 `rmb_per_usd` 时保持固定覆盖。
- 调度测试：
  - `appcore/scheduled_tasks.py` 登记 `usd_cny_exchange_rate_sync`。
  - systemd timer 为每天 06:00。
  - `/order-analytics/exchange-rates` 登录后可返回归档 JSON。
- 迁移 smoke test：
  - 表名、唯一键、三源验证字段、最大差异字段存在。

验证命令：

```bash
pytest tests/test_usd_cny_exchange_rates.py \
       tests/test_order_profit_backfill.py \
       tests/test_sku_actual_roas.py \
       tests/test_appcore_scheduled_tasks.py \
       tests/test_server_browser_runtime.py -q
```

本次验证不得连接 Windows 本机 MySQL `127.0.0.1:3306`。
