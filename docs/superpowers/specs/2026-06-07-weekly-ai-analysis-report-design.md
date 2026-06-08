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

- 默认周期：上一完整业务周，周日到周六。每周日北京时间 20:00 运行时，统计当前周日前面的 7 个完整业务日，也就是上周日整天到本周六整天。
- 业务日口径：Meta 业务日，北京时间 16:00 切日。
- 指定 `week_start` 时如果不是周日，后端会自动归一化到该日期所在业务周的周日。
- 当前业务周未完整时，页面允许预览，但必须在 `data_quality` 和 UI 中标记为 `realtime_snapshot` 或 `mixed`，不得按最终周报展示。
- 定时生成：每周日 20:00。此前 12:00 会早于周六 Meta 广告业务日完整收盘，容易让周六广告数据不完整。

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
- `product_tier_order_share`：按稳定品、潜力品、其他品汇总订单量占比，包含每周汇总和每天明细。稳定品读取 `product_stability.buckets.stable`；潜力品读取 `secondary_stable` 和历史兼容 `potential`；其他品为本周有订单但不属于前两类的所有产品。占比分母使用同一周期内 `product_sales_stats` 的产品订单量合计。
- `potential_new_products`：统计所选业务周内 `media_products.created_at` 落在 `week_start` 到 `week_end` 的新品，并只从周报分级为 `测试中` 的产品里选出表现最好的 10 个。排序仅使用本周日均单量和 ROAS，不读取上广告时间、产品位置或产品属性。
- `campaign_rows`：账户、campaign、匹配产品、每日 spend / purchase value / ROAS、周累计、首个出量日、活跃天数。
- `low_order_products`：1-2 单、3-5 单产品汇总，标记是否有广告消耗。
- `rule_findings`：后端规则先产出的确定性异常，如预算放大 ROAS 下滑、店铺亏损集中、数据质量 mismatch。

## AI 输出契约

注册 LLM use case：`order_analytics.weekly_ai_analysis`。

默认模型：

- provider：`openrouter`
- model：`google/gemini-flash-1.5`（OpenRouter Gemini 1.5 Flash，2026-06-07 核对 model slug）
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
  "material_supplement": {
    "country_expansion": [{"product_code": "...", "current_good_countries": ["DE"], "recommended_countries": ["FR"], "reason": "..."}],
    "material_fill": [{"product_code": "...", "target_country": "DE", "material_name": "...", "reason": "..."}]
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

- 产品评估范围：
  - 周报经营评估只纳入所选业务周内连续 7 天都有广告活跃数据的产品。活跃数据来自周报同口径广告计划日数据，任一天有广告花费或购买结果即视为当天活跃。
  - 只有投放开始距周结束满 7 天但本周未连续 7 天活跃的产品，周报中归为 `测试中`，不得进入商品方向、低单量、广告动作、补素材建议和逐产品 AI 评估。
  - `delivery_status = stopped` 的产品在周报中归为 `终止投放`，不得进入商品方向、低单量、广告动作、补素材建议和逐产品 AI 评估。
- 稳定品：
  - `7天稳定`：仍在投放，并满足以下任一条件：
    - 最近 7 个业务日每天至少 10 单，且 7 天累计不少于 140 单。
    - 最近 7 个业务日累计不少于 210 单。
  - `30天稳定`：仍在投放，最近 30 个业务日每天至少 10 单，且累计不少于 600 单。
  - 同时满足 7 天和 30 天时两个细分标记都保留。
- 二级稳定品（潜力稳定品）：先判断稳定品；如果未达到稳定品，但仍在投放、已满 7 天、最近 7 个业务日每天至少 5 单，且最近 7 天日均超过 10 单，则归入二级稳定品，并在明细中显示最低日单量。
- 潜力品：历史兼容口径，报告展示和统计以 `二级稳定品` 为准，不再把普通日均 5 单以上产品单独标为潜力品。
- 测试品：仍在投放或有广告数据，已满 7 天但未达到稳定品 / 二级稳定品。
- 已停投：历史有广告消耗，但 `media_product_ad_summary_cache.delivery_status = stopped`。
- 未投放：无广告消耗且 `delivery_status = never`，只进入后台统计，不作为重点经营表默认展示。

### 展示策略

- `素材管理` 增加 `稳定分级` 列；当前只对稳定品展示标签：`稳定品` + `7天稳定` / `30天稳定`。潜力品、测试品和已停投暂不打前端标签，避免列表噪声。
- `每周 AI 分析` 增加 `稳定产品分级` 可视化区：
  - 汇总稳定品总数、7 天稳定数、30 天稳定数、二级稳定品数、测试品数、已停投数、投放未满 7 天数。
  - 明细表展示头部稳定品和二级稳定品的产品、7 天 / 30 天订单、日均、最低日单量、ROAS、投放状态。
  - 这部分进入 LLM prompt，辅助商品方向和素材补充建议。
- `每周 AI 分析` 增加 `产品分层订单占比` 表：
  - 第一行展示整周汇总订单量和稳定品 / 潜力品 / 其他品订单占比。
  - 后续行展示每天的同口径订单量和占比。
  - 该数据进入 LLM prompt，用于判断增长或下滑是否由稳定品、潜力品还是长尾其他品驱动。
- `每周 AI 分析` 增加 `潜力新品情况` 可视化区：
  - 只统计所选业务周内上线的新品；上线以 `media_products.created_at` 为准，不使用 `product_ad_launch_dates` 的上广告时间。
  - 候选必须属于周报分级中的 `测试中`，避免稳定品 / 潜力稳定品重复进入该卡片。
  - 判断表现只看同一周的本周订单、7 天日均单量和 ROAS；不考虑投放时间、产品位置、上架状态、产品属性等额外维度。
  - 默认展示前 10 个，按日均单量降序、ROAS 降序排序。
  - 展示形式与稳定产品分级保持一致，包含产品主图、产品名 / Code、标签和产品分级；标签固定为 `潜力新品`，产品分级固定显示 `测试中`。

## 稳定 / 潜力品逐产品 AI 推进评估（2026-06-07 追加）

用户追加要求：每周 AI 分析不应对 200+ 全量产品逐个调用模型，只针对 `稳定品` 和 `潜力品` 做逐产品推进评估。逐产品评估用于回答运营下一步应该怎么打：继续补素材、扩国家、收缩预算、迁移明空素材，或先保守观察。

### 评估对象

- 只读取 `product_stability.buckets.stable`、`product_stability.buckets.secondary_stable` 和历史兼容的 `product_stability.buckets.potential`。
- `测试品`、`已停投`、`未投放` 不进入逐产品 Gemini 调用；它们仍可在稳定分级表展示，但不产生逐产品 AI 推进建议。
- 候选产品由后端从稳定分级缓存生成，按稳定品优先、近 7 天订单和近 30 天订单降序排序；每条候选产品一次 LLM 调用。
- 如果稳定分级缓存不可用或候选为空，周报整体 AI 仍可生成，逐产品建议区显示空态，不让周报失败。

### 国家阶梯

本功能固定面向 8 个目标国家，模型只能在以下国家内做推进建议：

- 第一阶梯：`DE` 德国、`FR` 法国。
- 第二阶梯：`ES` 西班牙、`IT` 意大利、`JP` 日本。
- 第三阶梯：`SE` 瑞典、`NL` 荷兰、`PT` 葡萄牙。

推进原则：

- 第一阶梯未验证充分时，默认优先补德 / 法素材和广告承接，不直接跳到二、三阶梯。
- 德 / 法已有稳定订单、ROAS 或素材证明后，再建议二阶梯扩张。
- 前两阶梯都表现不错、且素材储备足够时，才建议三阶梯测试。
- 如果订单国家分布和广告国家分布冲突，必须在 `risk_flags` 标明“订单国家”和“广告命名市场国家”口径不同，不得把两者混成同一事实。

### 每产品数据包

后端为每个候选产品构造压缩 JSON，不传全量订单明细。数据源包括：

- `identity`：`product_id`、`product_code`、中文产品名、产品主图 URL、素材管理搜索 URL。
- `stability`：稳定分级、7/30 天订单、日均、最低日单量、稳定标记、总体广告消耗、总体 ROAS、投放状态。
- `weekly_product`：本周订单、收入、广告费、利润、利润率、ROAS、成本完整性、每日订单序列。
- `campaigns`：本周匹配该产品的广告计划、账户、花费、购买价值、结果数、ROAS、每日花费。
- `order_country_distribution`：本周订单按 `buyer_country` / `buyer_country_name` 的分布，含订单数、收入、利润。
- `ad_country_distribution`：本周广告按 `meta_ad_daily_ad_metrics.market_country` 的分布，含花费、购买价值、结果数、ROAS。该字段是广告命名解析出的市场国家，不等同于真实买家国家。
- `material_summary_by_lang`：本地素材库按语言的素材数、已推视频数、广告花费、购买价值、ROAS、近 7 天活跃花费。
- `local_material_candidates`：本地已有素材候选，包含语言、文件名、展示名、推送次数、最近推送时间和明空绑定线索，用于判断是否已有可本地化素材。
- `mingkong_summary`：明空侧产品素材总数、有路径视频数、90 天总花费、广告数。
- `mingkong_material_candidates`：明空侧可搬运素材候选，按 90 天花费 / 广告数 / 昨日增量筛选，包含素材名、路径、封面、累计 90 天花费、广告数、昨日花费增量。
- `target_country_tiers`：上述 8 国三阶梯配置。
- `data_quality_notes`：数据缺失、缓存不可用、口径差异等说明。

所有补充数据查询必须 best-effort：任一附加数据源失败时只写入 `data_quality_notes`，不得中断周报整体生成。

### LLM use case

新增 LLM use case：`order_analytics.weekly_product_action_evaluation`。

- provider：`openrouter`
- model：`google/gemini-3.5-flash`
- usage service：`openrouter`
- units：`tokens`
- 调用入口：`appcore.llm_client.invoke_generate`
- `response_schema`：强制 JSON schema；解析失败时该产品返回 failed 状态并保留错误摘要，不让其他产品和整体周报失败。

### 提示词策略

系统提示词要求模型扮演电商投放运营分析师，输出严格 JSON，禁止编造不存在的数据、国家、素材、广告计划或订单。用户提示词必须明确：

- 只评估当前这一条产品，不要输出组合产品或全站建议。
- 先依据本周投放、订单、利润、国家分布和历史素材判断当前阶段。
- 按三阶梯国家策略决定动作：补素材、扩国家、保守观察、降预算 / 暂停、排查数据。
- 如果建议补素材，必须从 `local_material_candidates` 或 `mingkong_material_candidates` 中选择具体素材；不能只说“补素材”。
- 如果建议搬明空素材，必须说明搬哪个素材、为什么、优先本地化到哪个语言 / 国家。
- 如果建议扩国家，必须说明先扩哪一阶梯、哪些国家、前置条件和止损线。
- 如果数据不足，必须明确缺什么数据和临时动作。

### AI 输出契约

逐产品评估输出 JSON：

```json
{
  "product_id": 0,
  "product_code": "",
  "product_name": "",
  "status": "success|failed",
  "primary_action": "supplement_material|expand_country|hold|reduce_budget|pause|investigate",
  "action_label": "中文短标签",
  "confidence": 0,
  "stage": {
    "current_tier": "tier1|tier2|tier3|none",
    "next_tier": "tier1|tier2|tier3|none",
    "reason": "中文说明"
  },
  "country_plan": [
    {
      "country_code": "DE",
      "tier": "tier1",
      "decision": "scale|test|hold|stop|localize_first",
      "reason": "中文说明",
      "budget_action": "keep|increase_small|test_small|reduce|pause",
      "material_action": "reuse_existing|localize_mingkong|create_new|none"
    }
  ],
  "material_plan": {
    "needs_material": true,
    "priority_country_codes": ["DE", "FR"],
    "recommended_source": "local|mingkong|new|none",
    "recommended_material": {
      "source": "local|mingkong",
      "material_id": "",
      "filename": "",
      "display_name": "",
      "video_path": "",
      "lang": "",
      "evidence": "为什么选它"
    },
    "localization_steps": ["中文动作"]
  },
  "budget_plan": {
    "summary": "中文说明",
    "increase": [],
    "reduce": [],
    "pause": []
  },
  "evidence": ["中文证据"],
  "risk_flags": [{"level": "info|warning|error", "message": "中文风险"}],
  "next_steps": ["中文动作，按执行顺序"]
}
```

`confidence` 必须为 0-100 整数。`primary_action` 和国家 `decision` 只能使用 schema 枚举值。模型输出的 `product_id` / `product_code` 必须与输入一致。

### UI

`每周 AI 分析` 页面调整：

- 稳定产品分级表新增 `产品主图` 列，固定 200 × 200 框显示主图；无图显示空态。
- 产品列拆为两行：第一行中文产品名，第二行产品 Code。每行末尾都有复制图标按钮。
- 产品 Code 行额外增加放大镜按钮，点击打开 `/medias/?q=<product_code>`，让素材管理自动搜索该产品 Code。
- 新增 `AI 推进建议` 表，只展示稳定品 / 潜力品逐产品评估结果。核心列包括产品主图、产品、分级、AI 动作、国家阶梯、推荐素材、下一步、置信度。
- 图标按钮使用 inline SVG / Lucide 风格，不使用 emoji。按钮必须有 `title` / `aria-label`，复制失败时退回 textarea。

### 验证补充

新增或更新测试：

- 后端：候选产品只包含 stable / potential；测试品 / 已停投不触发逐产品 LLM。
- 后端：逐产品 prompt 包含 8 国三阶梯、订单国家分布、广告国家分布、本地素材和明空素材候选。
- 后端：`generate_ai_report` 在整体周报 JSON 成功后附加 `product_action_evaluations`，单产品失败不影响整体成功。
- use case：`order_analytics.weekly_product_action_evaluation` 注册为 OpenRouter `google/gemini-3.5-flash`。
- 前端：稳定分级表包含产品主图列、复制按钮、素材管理搜索链接；AI 推进建议表存在并能渲染空态。

## 补素材建议（2026-06-07 追加）

用户追加要求：周报里的商品方向需要明确告诉运营“哪些产品、哪些国家、补哪个英语素材”。补素材只针对跑得还可以、且已满足投放满 7 天评估范围的产品。

### 国家扩展

- 候选产品：稳定品和二级稳定品优先；测试品默认不进入国家扩展建议。
- 当前表现好的国家 / 语种：读取 `media_product_lang_ad_summary_cache`，优先看近 7 天仍有消耗、ROAS 不差、推送视频数或素材数已形成投放记录的语种。
- 扩国家阶梯：
  - 第一阶段：德国 `DE`、法国 `FR`。
  - 第二阶段：西班牙 `ES`、意大利 `IT`、日本 `JP`。
  - 第三阶段：葡萄牙 `PT`、荷兰 `NL`、瑞典 `SE`。
- 规则：如果当前只有少量国家在跑且产品表现不错，先补齐第一阶段；第一阶段都跑得不错后，再补第二阶段；第一阶段和第二阶段都跑得不错后，再补第三阶段。周报必须展示当前已跑好的国家和下一步建议国家。

### 优质素材补位

- 只考虑英语版素材源，运营后续自行本土化翻译。
- 素材源：优先使用本地明控快照 `mingkong_material_daily_snapshots` 中该产品最新成功同步的素材；按 `cumulative_90_spend` 和 `video_ads_count` 判断素材质量。
- 建议对象：当产品在某国家 / 语种跑得好，同时明控素材存在高 90 天消耗或广告数足够多的视频时，报告输出：
  - 产品。
  - 目标国家 / 语种。
  - 建议补投的英语素材名称 / 路径 / material key。
  - 素材 90 天消耗、广告数、建议原因。
- 页面新增 `补素材建议` 可视化区，分成 `扩国家` 和 `补素材` 两张表。AI prompt 同步携带这部分确定性建议，AI 可以补充解释，但不能编造不存在的素材。

## 定时任务

新增 `appcore/weekly_ai_analysis_report.py` 或放入 `appcore/order_analytics/weekly_ai_report.py` 的 `register(scheduler)`：

- task code：`weekly_ai_analysis_report`
- schedule：每周日 20:00
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
