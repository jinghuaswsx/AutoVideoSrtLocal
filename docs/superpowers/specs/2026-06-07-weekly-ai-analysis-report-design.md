# 每周 AI 分析报告

## 背景

用户在 `数据分析 -> 实时大盘` 发现 2026-06-01 至 2026-06-07 这一周的业务节奏异常：以往周四到周日通常更好、周一到周三较弱，但本周反过来。人工核对实时大盘、广告分析、订单分析、产品销量后，问题集中在周五到周六的广告放量效率下滑，且主要发生在 Newjoy。

当前页面已有实时大盘、广告分析、产品销量、订单盈亏明细和 ROAS 周报，但缺少一个能把这些数据串起来的业务解释层。用户需要在数据分析模块新增一个子 tab：`每周 AI 分析`，每周输出结构化业务报告，回答：

- 现在的业务有没有问题。
- 商品方向应该怎么调整。
- 广告层面应该怎么调整。

本功能目标不是替代实时大盘，而是在同一数据口径上生成周度诊断报告，并把 AI 结论和支撑数据可视化展示。

## 锚点

- `AGENTS.md`：数据分析模块、LLM 统一入口、定时任务必须登记、禁止本地 MySQL。
- `appcore/order_analytics/CLAUDE.md`：实时大盘业务日、广告费分摊、店铺筛选和数据质量硬规则。
- `docs/analytics-data-quality-guardrails.md`：数据分析接口顶层必须带 `data_quality`，异常不能静默展示。
- `docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md`：店铺筛选和店铺到账户映射。
- `docs/superpowers/specs/2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md`：实时 / 日终广告费选源。
- `appcore/weekly_roas_report.py`：周报快照、落库和 scheduler 注册模式；本功能只复用工程模式，不复用 ISO 周周期口径。

## 范围

### 做

1. 在 `数据分析` 顶部 tab 增加 `每周 AI 分析`。
2. 新增专用 API 聚合上一完整业务周或用户指定周的数据。
3. 汇总以下数据源：
   - `/order-analytics/realtime-overview` 同口径的每日全局和店铺数据。
   - `product_profit_list.generate_list` 的产品利润、收入、广告费和 ROAS。
   - 实时大盘 `campaigns` 的广告计划、账户、花费、购买价值、结果数和匹配产品。
   - `product_sales_stats` 的每产品每日订单量、销量、销售额。
4. 生成结构化 AI 报告，覆盖业务健康、商品方向、广告动作和数据质量风险。
5. 报告落库，支持读取最近周、手动生成 / 重新生成。
6. 每周自动生成上一完整业务周报告，并登记到 `appcore/scheduled_tasks.py`。

### 不做

1. 不改实时大盘现有 KPI 口径。
2. 不新建广告账户映射规则；继续使用 `meta_ad_accounts.site_account_map`。
3. 不把全量订单明细塞进 LLM prompt；只传压缩后的结构化周报数据包。
4. 不在前端散落业务判断公式；判断规则集中在后端 service。
5. 不直接操作生产服务重启；发布验证按项目发布流程另行执行。

## 周期口径

- 默认周期：上一完整业务周，周日到周六。每周日中午 12:00 运行时，统计当前周日前面的 7 个完整业务日，也就是上周日整天到本周六整天。
- 业务日口径：Meta 业务日，北京时间 16:00 切日。
- 指定 `week_start` 时如果不是周日，后端会自动归一化到该日期所在业务周的周日。
- 当前业务周未完整时，页面允许预览，但必须在 `data_quality` 和 UI 中标记为 `realtime_snapshot` 或 `mixed`，不得按最终周报展示。
- 定时生成：每周日 12:00。

## 数据包

新增 `appcore/order_analytics/weekly_ai_report.py`，核心函数：

```python
build_weekly_data_package(week_start: date, week_end: date) -> dict
generate_ai_report(week_start: date, week_end: date, *, user_id: int | None, force: bool = False) -> dict
get_report(week_start: date) -> dict | None
list_recent_reports(limit: int = 12) -> list[dict]
```

`build_weekly_data_package` 输出：

- `period`：`week_start`、`week_end`、timezone、cutover hour、是否完整业务周。
- `data_quality`：汇总所有日、店铺、产品盈亏数据质量，最差状态上浮。
- `daily_global`：每天销售额、订单、销量、广告费、手续费、采购、物流、退货预留、利润、利润率、True ROAS、Meta ROAS、保本 ROAS。
- `daily_by_store`：`all` / `newjoy` / `omurio` 每天同款指标。
- `segments`：周日、周一到周三、周四到周六、周五到周六等分段对比。
- `product_rows`：产品维度收入、订单、销量、广告费、ROAS、利润、利润率、活跃天数、每日订单分布。
- `campaign_rows`：账户、campaign、匹配产品、每日 spend / purchase value / ROAS、周累计、首个出量日、活跃天数。
- `low_order_products`：1-2 单、3-5 单产品汇总，标记是否有广告消耗。
- `rule_findings`：后端规则先产出的确定性异常，如预算放大 ROAS 下滑、店铺亏损集中、数据质量 mismatch。

## AI 输出契约

注册 LLM use case：`order_analytics.weekly_ai_analysis`。

默认模型：

- provider：`openrouter`
- model：`google/gemini-3-flash-preview`
- usage service：`openrouter`
- units：`tokens`

AI 必须输出 JSON：

```json
{
  "business_health": {
    "status": "ok|watch|problem|critical",
    "summary": "中文结论",
    "evidence": ["基于数据的证据"]
  },
  "product_direction": {
    "scale": [{"product_code": "...", "reason": "...", "action": "..."}],
    "watch": [{"product_code": "...", "reason": "...", "action": "..."}],
    "cut": [{"product_code": "...", "reason": "...", "action": "..."}]
  },
  "ad_actions": {
    "increase": [{"campaign": "...", "reason": "...", "action": "..."}],
    "reduce": [{"campaign": "...", "reason": "...", "action": "..."}],
    "pause": [{"campaign": "...", "reason": "...", "action": "..."}]
  },
  "risk_flags": [{"level": "info|warning|error", "message": "..."}],
  "executive_summary": ["中文要点"]
}
```

如果 LLM 返回无法解析的 JSON，后端保留 raw text，并返回 `status=failed`，页面显示失败原因和可重新生成按钮。

## 落库

新增迁移表 `weekly_ai_analysis_reports`：

- `id`
- `week_start_date` unique，固定为业务周周日。
- `week_end_date`
- `generated_at`
- `generated_by`：`scheduler` / `manual`
- `status`：`success` / `failed`
- `data_snapshot_json`
- `ai_report_json`
- `raw_text`
- `data_quality_json`
- `usage_log_id`
- `error_message`
- `created_at`
- `updated_at`

只保存压缩后的数据包，不保存全量订单明细。

## API

挂在 `web/routes/order_analytics.py`：

- `GET /order-analytics/weekly-ai-analysis/report?week_start=YYYY-MM-DD`
  - 有快照返回快照；无快照可实时计算数据包但不自动调用 AI。
- `POST /order-analytics/weekly-ai-analysis/generate`
  - JSON body：`week_start`、`force`。
  - 需要 `@login_required + @permission_required("data_analytics")`。
  - POST 必须走 `X-CSRFToken`。
- `GET /order-analytics/weekly-ai-analysis/weeks`
  - 最近 12 周报告列表。

所有响应顶层带 `data_quality`。

## UI

在 `web/templates/order_analytics.html` 新增顶层 tab：

- 顶部周选择、生成 / 重新生成按钮、报告时间。
- 数据质量条。
- KPI 区：销售额、订单、广告费、利润、利润率、True ROAS、Meta ROAS、保本 ROAS。
- 分段对比：周一到周三 vs 周四到周六，并单列周日与周五到周六压力段，突出利润和 ROAS 变化。
- 店铺拆分：全局 / Newjoy / Omurio。
- 商品方向表：加码、观察、降预算 / 停投。
- 广告动作表：加预算、降预算、暂停。
- 低单量产品区：1-2 单、3-5 单产品统计，展示消耗与出单。
- AI 总结区：业务有没有问题、商品方向、广告动作。

页面样式沿用数据分析现有卡片、表格、subtab 和数据质量条，不做营销式 hero。

## 产品稳定分级（2026-06-07 追加）

用户追加要求：除了周度经营分析，还要在报告里看清所有在跑量 / 有广告数据产品的稳定状态，并在 `素材管理` 产品列表增加一列，让头部产品能直接被识别。

### 口径

- 统计对象：`media_products.deleted_at IS NULL` 的产品；素材管理列表继续受已有归档筛选控制。
- 订单口径：沿用素材管理单量列的 `order_profit_lines -> dianxiaomi_order_lines.meta_business_date` 业务日订单计数，按 `dxm_package_id` 去重。
- 广告口径：优先读取 `media_product_ad_summary_cache` 的总体消耗、近 7 天活跃消耗、ROAS、投放状态和投放起止时间。
- 更新时间：新增独立缓存表，每 6 小时刷新一次；报告只读取缓存，不在前端临时计算。

### 分级规则

- 稳定品：
  - `7天稳定`：仍在投放，并满足以下任一条件：
    - 最近 7 个业务日每天至少 10 单，且 7 天累计不少于 140 单。
    - 最近 7 个业务日累计不少于 210 单。
  - `30天稳定`：仍在投放，最近 30 个业务日每天至少 10 单，且累计不少于 600 单。
  - 同时满足 7 天和 30 天时两个细分标记都保留。
- 潜力品：仍在投放或有广告数据，未达到稳定品，但最近 7 天日均不少于 5 单；其中日均超过 10 单但波动未达稳定条件的产品仍归入潜力品，并在明细中显示最低日单量。
- 测试品：仍在投放或有广告数据，未达到稳定品 / 潜力品，最近 7 天日均低于 5 单。
- 已停投：历史有广告消耗，但 `media_product_ad_summary_cache.delivery_status = stopped`。
- 未投放：无广告消耗且 `delivery_status = never`，只进入后台统计，不作为重点经营表默认展示。

### 展示策略

- `素材管理` 增加 `稳定分级` 列；当前只对稳定品展示标签：`稳定品` + `7天稳定` / `30天稳定`。潜力品、测试品和已停投暂不打前端标签，避免列表噪声。
- `每周 AI 分析` 增加 `稳定产品分级` 可视化区：
  - 汇总稳定品总数、7 天稳定数、30 天稳定数、潜力品数、测试品数、已停投数。
  - 明细表展示头部稳定品和潜力品的产品、7 天 / 30 天订单、日均、最低日单量、ROAS、投放状态。
  - 这部分进入 LLM prompt，辅助商品方向和素材补充建议。

## 定时任务

新增 `appcore/weekly_ai_analysis_report.py` 或放入 `appcore/order_analytics/weekly_ai_report.py` 的 `register(scheduler)`：

- task code：`weekly_ai_analysis_report`
- schedule：每周日 12:00
- runner：`appcore.order_analytics.weekly_ai_report.run_scheduled_report`
- log table：`scheduled_task_runs`
- 必须登记到 `appcore/scheduled_tasks.py`，并在 `appcore/scheduler.py` 注册。

新增 `appcore/media_product_stability_scheduler.py`：

- task code：`media_product_stability_refresh`
- schedule：每 6 小时
- runner：`appcore.media_product_stability_scheduler.tick_once`
- log table：`scheduled_task_runs`
- 必须登记到 `appcore/scheduled_tasks.py`，并在 `appcore/scheduler.py` 注册。

## 验证

新增或更新测试：

- `tests/test_order_analytics_weekly_ai_report.py`
  - 默认上一完整业务周（周日到周六）。
  - 数据包汇总每日、店铺、产品、广告、低单量产品。
  - LLM JSON 成功 / 失败。
  - 落库 upsert 和读取。
- `tests/test_order_analytics_tab_routes.py`
  - `/order-analytics/weekly-ai-analysis-view` 未登录 302，登录 200，`active_tab=weeklyAiAnalysis`。
- `tests/test_order_analytics_template_layout.py`
  - 顶部和移动 tab 均包含 `每周 AI 分析`。
  - 面板包含数据质量条、KPI、商品建议、广告建议。
- `tests/test_llm_use_cases_registry.py`
  - use case 注册。
- `tests/test_appcore_scheduled_tasks.py`
  - task definition 登记。

回归：

```bash
pytest tests/test_order_analytics_weekly_ai_report.py \
       tests/test_order_analytics_tab_routes.py \
       tests/test_order_analytics_template_layout.py \
       tests/test_llm_use_cases_registry.py \
       tests/test_appcore_scheduled_tasks.py -q
```

涉及实时大盘口径时补跑：

```bash
pytest tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_data_quality.py \
       tests/test_order_profit_aggregation.py \
       tests/test_order_analytics_ads.py \
       tests/test_product_profit_report.py -q
```
