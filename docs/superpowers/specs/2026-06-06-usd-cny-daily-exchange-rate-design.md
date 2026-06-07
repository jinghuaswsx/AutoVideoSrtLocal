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
- 配置兜底汇率不再长期固定；每天用最近 30 天已归档基准汇率平均值更新，并保留历史。
- 后台汇率入口需要展示当前兜底汇率、计算逻辑、更新日期和兜底历史。

## 目标

1. 新增每日 USD/CNY 基准汇率归档表，每个北京时间自然日最多一个有效基准。
2. 每天 06:00 自动拉取三个独立来源：一个主源 + 两个校验源。
3. 三个来源都返回有效 USD/CNY 且三源之间最大相对差异不超过 5% 时，才写入当天基准。
4. 订单利润核算按每行 `meta_business_date` 读取当天汇率，写入 `cost_basis.rmb_per_usd` 和汇率来源字段。
5. SKU 实际保本 ROAS 的 30 天窗口按每条订单行的 `meta_business_date` 读取当天汇率；显式传入 `--rmb-per-usd` 时保留手工覆盖能力。
6. 新任务必须登记到 `appcore/scheduled_tasks.py`，并提供 systemd service/timer。
7. 提供只读 URL 入口查看最近归档的汇率数据。
8. 新增动态兜底汇率：每天基准汇率同步成功后，用最近 30 天 `usd_cny_daily_exchange_rates.usd_to_cny` 计算算术平均值并归档为当前兜底。
9. 当某业务日缺少当天归档汇率时，优先使用最新动态兜底汇率；动态兜底也缺失时才退回 `system_settings.material_roas_rmb_per_usd`。

## 非目标

- 不改素材管理手工 ROAS 表单的即时测算口径；该页面没有业务日期上下文，继续使用 `material_roas_rmb_per_usd`。
- 不回填历史所有日期的真实汇率；历史缺档日期使用动态兜底汇率，并在结果里标记 fallback。
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

新增表 `usd_cny_fallback_exchange_rates`：

- `fallback_date DATE NOT NULL`：兜底汇率更新日期，北京时间自然日；唯一键。
- `usd_to_cny DECIMAL(12,6) NOT NULL`：兜底汇率，1 USD = N CNY。
- `window_start DATE NOT NULL` / `window_end DATE NOT NULL`：参与平均的基准汇率日期窗口。
- `sample_count INT NOT NULL`：实际参与平均的归档天数。
- `source_rate_ids_json JSON NOT NULL`：参与平均的 `usd_cny_daily_exchange_rates.id` 列表。
- `calculation_method VARCHAR(64) NOT NULL DEFAULT 'daily_archive_30d_average'`。
- `source_run_id BIGINT DEFAULT NULL`：触发本次兜底更新的定时任务 run id。

兜底计算规则：

- 默认窗口是 `fallback_date - 29` 到 `fallback_date`，最多覆盖最近 30 个北京时间自然日。
- 只使用已写入 `usd_cny_daily_exchange_rates` 的基准汇率，不直接用校验源，不使用失败尝试。
- 样本数允许小于 30；新上线初期只有 1~29 条时，用已有样本平均，`sample_count` 如实展示。
- 没有任何样本时不写 `usd_cny_fallback_exchange_rates`，查询端继续退回旧配置值。
- 同一天重复运行时按 `fallback_date` upsert，保留最新平均值、样本数和更新时间。

## 核算口径

订单利润：

- `tools/order_profit_backfill.py` 在未传 `rmb_per_usd` 时，对每行用 `meta_business_date` 查询 `usd_cny_daily_exchange_rates.rate_date`。
- 命中归档时，`calculate_line_profit(..., rmb_per_usd=当天汇率)`。
- `cost_basis` 追加：
  - `exchange_rate_source = "daily_archive"`
  - `exchange_rate_date = "YYYY-MM-DD"`
  - `exchange_rate_source_id`
- 若缺少当天归档，优先兜底到 `usd_cny_fallback_exchange_rates` 最新记录，并写：
  - `exchange_rate_source = "fallback_30d_average"`
  - `exchange_rate_date = fallback_date`
  - `exchange_rate_source_id`
- 如果动态兜底也不存在，再历史兼容兜底到 `system_settings.material_roas_rmb_per_usd`，并写：
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

基准汇率同步成功后，同一 runner 立即刷新当天动态兜底汇率，并把结果并入 summary：

```json
{
  "fallback": {
    "fallback_date": "2026-06-07",
    "usd_to_cny": 6.7656,
    "window_start": "2026-05-09",
    "window_end": "2026-06-07",
    "sample_count": 2,
    "calculation_method": "daily_archive_30d_average",
    "logic": "最近 30 天已归档 USD/CNY 基准汇率的算术平均值"
  }
}
```

## 查看入口

新增只读 JSON 入口：

```text
GET /order-analytics/exchange-rates?limit=30
```

权限：`@login_required + @permission_required("data_analytics")`。

响应包含最近 `limit` 天归档记录，字段包括当天基准汇率、主源 quote、两个校验源 quote、最大相对误差、容差和同步时间。`limit` 限制在 1 到 365。

同一入口还必须包含兜底汇率信息：

- `current_fallback`：当前生效兜底汇率、计算方法、窗口、样本数、更新日期。
- `fallback_history`：最近 `limit` 条兜底汇率历史。
- `fallback_logic`：面向后台查看的固定说明，内容为“最近 30 天已归档 USD/CNY 基准汇率的算术平均值；缺当天基准时优先使用该值；无样本时退回系统配置汇率”。

超级管理员设置入口：

- `/admin/settings?tab=general` 的“素材 ROAS 汇率”设置旁增加“USD/CNY 动态兜底汇率”卡片。
- 卡片展示当前兜底汇率、更新日期、计算窗口、样本数、计算逻辑和最近兜底历史。
- 该卡片只读，不保存任何新字段；原 `material_roas_rmb_per_usd` 输入框继续保留，作为动态兜底无样本时的最后兜底。

## 测试计划

- 汇率服务单测：
  - 三个来源通过时写入当天基准。
  - 三源最大差异超过 5% 时抛错且不 upsert。
  - 查某日期汇率命中归档；缺档时 fallback 到配置汇率。
  - 同步成功后用近 30 天归档基准计算动态兜底汇率。
  - 缺当天归档时优先使用动态兜底汇率；动态兜底缺失时再用旧配置值。
  - 最近归档列表返回主源、两个校验源、最大相对误差和同步时间。
  - 兜底历史列表返回当前兜底、计算逻辑、窗口、样本数和更新时间。
  - `/admin/settings` 展示当前动态兜底、计算逻辑和历史表。
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
