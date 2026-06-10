# 投放素材 AI 分析项目化设计

日期：2026-06-09

## 2026-06-10 功能拆分纠偏

`AI素材军师` 和 `投放素材AI分析` 是两个独立功能，不能再共用入口、路由、项目表、运行锁、前端脚本或 LLM use case。2026-06-10 之前的文档段落曾把“素材管理内 AI素材军师 子 Tab”和“左侧投放素材AI分析”写成同一工具入口；该写法作废，以本节为后续代码锚点。

- `AI素材军师` 保留原稳定入口：素材管理页子 Tab 文案为 `AI素材军师`，路由继续使用 `GET /medias/ai-material-strategist`、`GET /medias/ai-material-strategist/projects/<id>` 和 `/medias/api/ai-material-strategist/*`，后端继续使用 `appcore.ai_material_strategist`、`ai_material_strategist_projects`、`ai_material_strategist_product_results`、运行锁 `ai_material_strategist_single_running_project`。默认 LLM 绑定恢复为 OpenRouter `google/gemini-3.5-flash`。
- `投放素材AI分析` 是左侧菜单的独立入口：路由使用 `GET /medias/ad-material-ai-analysis`、`GET /medias/ad-material-ai-analysis/projects/<id>` 和 `/medias/api/ad-material-ai-analysis/*`，后端使用 `appcore.ad_material_ai_analysis`、`ad_material_ai_analysis_projects`、`ad_material_ai_analysis_product_results`、运行锁 `ad_material_ai_analysis_single_running_project`。默认 LLM 绑定为 GoogleWJ `gemini-3.5-flash`。
- 两个功能的项目列表、运行中互斥、分享 token、公开报告、前端脚本和 API 请求都必须各查各的命名空间。左侧菜单只进入 `投放素材AI分析`；素材管理子 Tab 只进入 `AI素材军师`。
- 已经误写进 `ai_material_strategist_projects` 且 `project_name LIKE '投放素材AI分析%'` 或 `provider_code='google_wj'` 的投放分析项目，迁移到 `ad_material_ai_analysis_*` 后从旧 AI素材军师列表移除，避免污染旧功能历史项目。
- `AI素材军师` 项目列表必须支持删除已完成或失败项目；运行中项目不能删除，避免后台执行器仍写入同一项目。删除接口为 `DELETE /medias/api/ai-material-strategist/projects/<id>`，必须登录、管理员和 `medias` 权限，并依赖外键级联清理 `ai_material_strategist_product_results`。

## 背景

运营每天需要把 `素材管理` 产品列表过一轮，从当前产品中找出“有量且 ROAS 好”的头部机会品，再结合广告、订单、国家投放、素材翻译反馈和明空选品中心素材，决定下一步补素材动作。

补素材动作分三类：

1. 同一条素材已在德国 / 法国等国家跑通后，继续扩到意大利、西班牙、日本等国家测试。
2. 某条素材在某个国家表现好，给同一国家补一条新素材继续放量。
3. 某些国家跑得差，但产品整体成立时，补新素材进一步验证该国家是否可跑。

本功能新增 `投放素材AI分析`，每次运行生成一个独立项目。项目保存当次输入数据、AI 提示词、模型输出、Top 20 产品、逐产品建议和可执行操作入口。项目详情页是完整独立路由，可随时回看当次结论。

## 文档锚点

- `AGENTS.md`：文档驱动代码、LLM 统一入口、`/medias` 蓝图、POST CSRF、定时任务登记规则。
- `docs/superpowers/specs/2026-06-07-weekly-ai-analysis-report-design.md`：已有周报数据包、逐产品 AI 推进评估、补素材建议、流程图与提示词可视化。
- `docs/superpowers/specs/2026-06-05-video-material-ad-performance-design.md`：视频素材广告表现、素材维度消耗 / ROAS / 国家情况口径。
- `docs/superpowers/specs/2026-05-18-mingkong-video-material-library-subtabs-design.md`：明空视频素材卡片、视频预览、加入素材库 / 做小语种操作入口。
- `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`：选品到任务中心到素材到推送的流程闭环。
- `db/migrations/2026_06_06_googlewj_vertex_provider.sql`：`google_wj` 通道和 `gemini-3.5-flash` 模型配置。
- `appcore/llm_use_cases.py`：投放素材 AI 分析统一走 `google_wj` 通道，模型为原生 Gemini ID `gemini-3.5-flash`。

## 2026-06-09 只读数据基线

只读生产数据，未写库、未调用 LLM。

- 当前北京时间：2026-06-09 15:58。
- 当前 Meta 业务日：2026-06-08；昨天：2026-06-07。
- 日终广告数据最新业务日：2026-06-07。
- 实时广告数据最新快照：2026-06-09 15:40，覆盖业务日 2026-06-08。
- 订单盈亏最新更新时间：2026-06-09 15:40。
- 明空素材快照最新时间：2026-06-09 05:00。
- 活跃未归档产品：276 个；符合“有量评估”门槛产品：182 个。

### 本轮人工 Top 20

评分口径：最近 30 天和 7 天广告消耗、订单量、广告数、昨日消耗、真实 ROAS、Meta ROAS、利润综合评分。高 ROAS 但只有 1-2 单的产品不会靠 ROAS 单独进榜。

| 排名 | 产品 | 30天消耗 | 30天订单 | 30天真实 ROAS | 7天消耗 | 昨天消耗 | 初步动作 |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | 防噎仪 `emergency-choking-relief-kit-rjc` | 1381.85 | 177 | 4.07 | 1381.85 | 554.40 | 六国已跑好，优先补新素材；明空素材 90天消耗 61200、昨日增量 9800 |
| 2 | 运动拉链袜 `easy-on-zipper-compression-socks-rjc` | 5239.13 | 583 | 2.38 | 3116.04 | 754.62 | FR 好，DE/IT 弱；优先补 DE/IT 新素材并扩 ES/JP |
| 3 | 隐形眼镜清洗器 `sonic-lens-refresher-rjc` | 28782.50 | 1722 | 1.48 | 6887.86 | 1440.54 | 有量但利润为负；先补高胜率国家素材，不盲目扩预算 |
| 4 | 动态旋转LED投影灯 `magical-rotating-night-projector-rjc` | 7487.11 | 582 | 2.04 | 4629.93 | 1083.62 | 当前主要 EN；优先创建 DE/FR/IT/ES/JP 小语种任务 |
| 5 | 搓脚按摩垫 `solespa-hands-free-silicone-foot-scrubber-mat-rjc` | 3002.59 | 257 | 2.00 | 1145.11 | 220.71 | FR/DE/IT 好，ES 弱；明空多条高消耗素材，补 ES/JP 新素材 |
| 6 | 车载应急尿壶 `portable-car-urinal-bucket-rjc` | 2284.84 | 211 | 2.25 | 2023.80 | 440.19 | FR/IT/DE 好，ES 弱；明空素材 90天消耗 53400，补 ES/JP |
| 7 | 反头指甲剪 `effortless-precision-toenail-trimmer-rjc` | 2227.20 | 208 | 2.28 | 1654.78 | 441.57 | DE/FR 好；明空素材 50500 且昨日增量 2800，补 ES/IT/JP |
| 8 | 药片活页收纳包 `versatile-small-item-organizer-rjc` | 3549.27 | 268 | 1.85 | 1910.98 | 401.11 | 当前主要 EN；优先 DE/FR，再扩 ES/IT/JP |
| 9 | 马鞍式牵引遛狗绳 `reflective-dog-harness-set-rjc` | 6186.25 | 424 | 1.58 | 1662.11 | 320.17 | DE/FR 好，IT/ES 弱；明空素材 84700，补 IT/ES 新素材 |
| 10 | 遮阳布锁扣 `sunlock-windproof-tarp-clips-rjc` | 4332.49 | 240 | 1.73 | 3402.65 | 711.89 | IT/JP/DE 好；可补 FR/ES 或扩 NL/PT/SE |
| 11 | 一次性碘伏棉片 `instant-snap-iodine-swabs-rjc` | 359.99 | 63 | 4.28 | 359.99 | 158.79 | 新近表现强；DE/FR/IT/ES 好，优先补同国家新素材 |
| 12 | 五指洗车手套 `scratch-free-5-finger-wash-mitt-rjc` | 8287.15 | 491 | 1.39 | 1747.78 | 350.44 | 有量但利润负；DE/FR/IT/ES 可补素材，预算需谨慎 |
| 13 | 锡纸套 `insulated-foil-food-covers-rjc` | 2167.40 | 186 | 2.11 | 1595.21 | 411.32 | FR/ES/IT 好；明空素材 77400 且昨日增量 3200，补 DE/JP 或优质素材 |
| 14 | 多功能厨房防烫碗架夹 `multi-purpose-anti-scald-bowl-holder-clip-for-kitchen-rjc` | 3102.21 | 264 | 1.89 | 2085.11 | 459.71 | JP/IT/FR/ES 好；补 DE，后续扩 NL/PT/SE |
| 15 | 运动多功能钥匙扣 `3-in-1-kerosene-match-keychain-rjc` | 7441.72 | 424 | 1.45 | 1324.53 | 237.57 | FR/JP 好，其余弱；先补 DE/IT/ES 新素材 |
| 16 | 毛毛虫弹球猫玩具 `glow-in-the-dark-chase-ball-rjc` | 2115.84 | 213 | 2.19 | 1650.59 | 415.32 | IT/JP/DE/ES 好；明空素材 29600 且昨日增量 1500，补 FR |
| 17 | 可堆叠棒球帽收纳盒 `baseball-cap-organizer-rjc` | 5968.11 | 249 | 1.45 | 828.98 | 166.82 | FR 好，IT/JP 临界；明空素材 318000，优先补 IT/JP 新素材 |
| 18 | 纯色发夹 `solid-color-hair-clip-rjc` | 1622.57 | 174 | 2.30 | 1583.03 | 482.84 | IT 好，DE/FR/ES 弱；明空素材多，补 DE/FR/ES |
| 19 | 大眼钢针套装 `easy-thread-sewing-kit-rjc` | 2938.89 | 228 | 1.67 | 2194.80 | 449.16 | IT 好，FR/DE/ES 弱；明空素材 49100 / 25000，补弱国家 |
| 20 | 太空人平衡树 `balance-spaceman-stacking-game-rjc` | 4273.05 | 293 | 1.48 | 1012.80 | 203.47 | DE 好；明空素材 82200，补 FR/ES/IT 或验证 JP |

## 目标

1. 在左侧菜单的任务中心下方新增管理员可见入口 `投放素材AI分析`，指向独立项目化分析页；素材管理内 `AI素材军师` 子 Tab 保留旧功能入口，不再指向同一工具。
2. 每次运行生成一个项目，项目保存完整结论和快照，不被后续数据变化覆盖。
3. 找出当前产品里综合表现最好的 20 个产品，必须同时考虑“量”和“ROAS”，不能让 1-2 单高 ROAS 产品冒头。
4. 对 Top 20 每个产品单独分析投放、订单、国家、素材翻译反馈、明空素材候选，并给出补素材建议。
5. 所有 LLM 调用统一走 GoogleWJ 通道：provider `google_wj`，model `gemini-3.5-flash`。不要把 OpenRouter 的 `google/gemini-3.5-flash` 模型 ID 传给 GoogleWJ。
6. 页面提供可执行入口：看明空视频、查看翻译后视频反馈数据、加入素材库、创建小语种翻译任务。
7. 页面可视化、项目化、可回看，且展示提示词、输入数据、调用参数和模型输出。

## 非目标

- 不改现有 `每周 AI 分析` 业务周报入口；本功能是素材管理下的日常执行工具。
- 不自动改预算、不自动创建广告计划。
- 不绕过已有素材入库、任务中心和小语种任务创建服务。
- 不把广告命名国家当作真实买家国家；两者必须分别展示。

## 2026-06-10 投放素材 AI 分析评审契约

本轮需求新增独立的 `投放素材AI分析` 模块，用于“评估哪些产品需要补素材”。模型最终评审采用四段输入：

```json
{
  "current_date": "YYYY-MM-DD",
  "product_brief": {},
  "creator_brief": {},
  "candidate_video": {},
  "stage1_visual_brief": {}
}
```

### 数据适配原则

不要为了贴合提示词硬造字段。仓库没有某类数据时，服务端必须显式压缩事实、标注缺失，并调整提示词让模型按“未参与评分”处理。

- `product_brief` 是主依据，必须尽量从现有商品、广告、订单和素材数据构造，结构对齐 `data.matrix`。
- `creator_brief` 只在能从 Tabcut / 明空 / 外部候选视频中取得达人或视频商业数据时参与评分；否则传入 `{}`，并在提示词中要求 `creator_data.score=null, included=false`。
- `candidate_video` 只在存在明确候选视频时传入。候选视频描述、发布时间、播放/互动/销量等字段必须区分“达人整体表现”和“这条候选视频贡献”，不能把达人总销量写成候选视频销量。
- `stage1_visual_brief` 优先复用 `video_ai_reviews` 或素材 AI 视频分析结果；没有结果时传 `{}`，并要求视频模块不参与或只轻权重解释。
- `trend` 不从商品近期 ROAS、达人播放增长或候选视频热度推导。没有明确的未来 45 天季节、节日或外部趋势输入时，必须输出 `included=false`。

### product_brief 构造

`product_brief` 输出为：

```json
{
  "code": 0,
  "data": {
    "matrix": {
      "slug": "",
      "total_medias": 0,
      "product_name": "",
      "product_desc": "",
      "base_roas": null,
      "today": "YYYY-MM-DD",
      "active_days": 0,
      "total_spend": 0,
      "total_sales": 0,
      "overall_roas": null,
      "recent_7d_roas": null,
      "recent_7d_sales": 0,
      "recent_7d_spend": 0,
      "cold_media_count": 0,
      "active_media_count": 0,
      "effective_media_count": 0,
      "hit_rate": null,
      "medias": []
    }
  },
  "message": ""
}
```

字段来源：

- `media_products`：`slug/product_code/product_name/product_desc`。
- `appcore.product_roas.calculate_break_even_roas()`：`base_roas`，缺成本/售价时允许为 `null`。
- `media_product_ad_summary_cache`：`total_spend/overall_roas/active_days`。
- `order_profit_lines` + `dianxiaomi_order_lines`：`total_sales/recent_7d_sales`，以现有美元收入口径为准。
- `meta_ad_daily_ad_metrics` + `meta_ad_realtime_daily_ad_metrics`：按 `media_items.filename/display_name/object_key` 匹配素材广告，聚合为 ISO 自然周 `medias[].insights[]`。
- `media_items`：`total_medias` 和素材基础信息。

衍生统计：

- `active_media_count`：最近 7 天或当前开放业务日有消耗的素材数。
- `effective_media_count`：历史任一周有消耗且 ROAS 达到 `base_roas` 的素材数；`base_roas` 缺失时使用有销售且 ROAS>0 的素材数。
- `cold_media_count`：无广告消耗或无有效周记录的素材数。
- `hit_rate`：`effective_media_count / total_medias`，无素材时为 `null`。

### 评审提示词适配

服务端提示词必须包含用户提供的业务规则，并额外加入以下适配条款：

1. 只根据输入 JSON 判断，不得补全不存在的数据。
2. 如果 `creator_brief`、`candidate_video`、`stage1_visual_brief` 或趋势依据为空，按缺失模块规则输出 `score=null, included=false`，再按参与评分模块折算 `quality_score`。
3. 不要把“数据缺失”解释成“表现差”；只能写“输入未提供该数据，未参与评分”。
4. 商品历史数据应是最详细分析段；近期断档不得直接判死，必须结合历史强度和最近有花费周距离 `current_date` 判断。
5. 风险扫描要二次检查 `candidate_video.desc`、`stage1_visual_brief.copy_extraction.original_copy` 和 `stage1_visual_brief.risk_alerts`，但没有候选视频或取证结果时输出空数组。

### 输出和落库

单产品模型输出仍保存到 `ai_material_strategist_product_results.ai_result_json`。当使用本契约评审时，`ai_result_json` 必须保留：

- `material_review_input`：本次传给模型的四段输入快照。
- `material_review_result`：模型返回的严格 JSON。
- `material_review_prompt_debug`：provider、model、use case、提示词版本、缺失模块列表。

页面优先展示 `material_review_result.final_decision`、`quality_score`、商品历史分析、风险提示和剪辑方案；旧的 `priority/primary_action/country_actions` 可继续作为操作建议来源。

## 数据窗口

默认运行窗口基于当前 Meta 业务日：

- `today`: `current_meta_business_date()`，开放业务日用实时广告快照。
- `yesterday`: `today - 1`，优先日终广告。
- `last_7d`: 含 today 最近 7 个 Meta 业务日。
- `last_30d`: 含 today 最近 30 个 Meta 业务日。
- 明空素材：读取最新成功 `mingkong_material_daily_snapshots`，按产品 code 去掉本地 `-rjc` 后匹配。

数据源：

- 产品：`media_products`。
- 素材：`media_items`、`media_item_mk_bindings`。
- 翻译 / 推送反馈：`media_product_lang_ad_summary_cache`、`media_push_logs`。
- 任务中心：`tasks`，按产品、源素材、目标国家同步已分派任务，避免重复建议。
- 广告：`meta_ad_daily_ad_metrics` + `meta_ad_realtime_daily_ad_metrics` 最新 `(business_date, ad_account_id)` 快照。
- 订单利润：`order_profit_lines` + `dianxiaomi_order_lines`。
- 明空素材：`mingkong_material_daily_snapshots`、`mingkong_material_daily_top100`、`mingkong_material_products`。

## 后端模型

新增表：

### `ai_material_strategist_projects`

- `id`
- `project_name`
- `status`: `running` / `success` / `failed`
- `generated_by`
- `generated_at`
- `data_window_json`
- `data_snapshot_json`
- `ai_report_json`
- `workflow_debug_json`
- `data_quality_json`
- `error_message`
- `created_at`
- `updated_at`

### `ai_material_strategist_product_results`

- `id`
- `project_id`
- `rank_no`
- `product_id`
- `product_code`
- `product_name`
- `score`
- `metrics_json`
- `country_summary_json`
- `local_materials_json`
- `mingkong_materials_json`
- `ai_result_json`
- `action_items_json`
- `created_at`
- `updated_at`

项目表保存全量压缩快照；产品表方便列表、筛选和操作入口快速渲染。

## Top 20 选品流程

### Step 1: 确定性预筛

先用规则筛掉无量产品：

- 最近 30 天广告消耗 >= 50 美元，或
- 最近 30 天订单 >= 8，或
- 最近 7 天广告消耗 >= 25 美元，或
- 昨天广告消耗 >= 10 美元。

### Step 2: 规则打分

规则打分只做候选排序，不直接作为最终结论：

- 量：30 天广告消耗、30 天订单、30 天广告数、7 天消耗、昨日消耗。
- 效率：30 天真实 ROAS、Meta ROAS，ROAS 封顶后计分，避免极端小样本。
- 利润：利润为正加分，利润明显为负扣分。
- 新鲜度：昨天仍在消耗 / 今天开放日有订单加分。

取前 60 个进入 AI 复评。

### Step 3: LLM 分批复评

注册 use case：`medias.ad_material_ai_analysis_rank_products`。

- provider: `google_wj`
- model: `gemini-3.5-flash`
- usage service: `google_wj`
- units: `tokens`

将候选按 20 个一批分 3 次调用。每批输入压缩指标，输出每批 Top 10 与理由。最后再把 3 批候选合并调用一次总排名，输出最终 Top 20。

提示词要求：

- 你是跨境电商素材投放军师。
- 只根据输入 JSON 判断，不编造数据。
- “表现好”必须同时有量和效率；1-2 单高 ROAS 不得排前。
- 优先选择有持续消耗、订单、广告数和可补素材空间的产品。
- 输出严格 JSON。

输出契约：

```json
{
  "ranked_products": [
    {
      "product_id": 0,
      "product_code": "",
      "rank": 1,
      "score": 0,
      "volume_reason": "",
      "efficiency_reason": "",
      "risk_reason": "",
      "why_selected": ""
    }
  ],
  "rejected_high_roas_low_volume": []
}
```

## 逐产品分析流程

注册 use case：`medias.ad_material_ai_analysis_product_analysis`。

每个 Top 20 产品单独调用，不合并成一次调用。单产品数据包包含：

- `identity`
- `performance_windows`: today / yesterday / 7d / 30d。
- `orders_profit`: 订单、收入、利润、真实 ROAS。
- `ads`: 广告数、消耗、成效、Meta ROAS、国家分布。
- `country_summary`: EN/DE/FR/ES/IT/JP/SE/NL/PT 的素材数、推送数、消耗、ROAS、活跃 7 天消耗；EN 是英语源语言/英语投放分析，不归入小语种翻译任务。
- `local_materials`: 本地视频素材、语言、推送次数、明空绑定线索、视频预览 URL。
- `translated_feedback`: 翻译后素材对应广告反馈数据。
- `mingkong_material_candidates`: 明空素材候选，含视频名、路径、封面、90 天消耗、广告数、昨日消耗增量。
- `task_assignments`: 任务中心已分派任务，含任务 ID、目标国家、源素材、状态、负责人、任务详情链接。
- `target_country_tiers`: 源语言 EN；第一阶梯 DE/FR，第二阶梯 ES/IT/JP，第三阶梯 SE/NL/PT。

### 任务中心同步与去重

AI素材军师必须把任务中心已排程任务作为决策输入，而不是只作为页面备注。

- 任务匹配口径：优先按 `media_product_id + media_item_id + country_code` 匹配具体源素材和目标国家；如果明空候选尚未入库、没有本地 `media_item_id`，至少按 `media_product_id + country_code` 识别该国家已存在排程。
- 状态归一：
  - `pending`: 待处理，含 `pending`、`blocked`。
  - `in_progress`: 进行中，含 `raw_in_progress`、`raw_review`、`raw_done`、`assigned`、`review`。
  - `completed`: 已完成，含 `done`、`all_done`。
  - `cancelled`: 已取消，含 `cancelled` 或存在取消时间。
- 去重规则：
  - `pending` / `in_progress` / `completed` 任务都视为已有安排，不再生成同产品、同国家、同素材的 `创建小语种翻译任务` 建议。
  - `completed` 即使尚未推送，也不能重复排程；后续动作应提示先查看任务结果或推送反馈。
  - `cancelled` 可重新安排，但页面必须标注“曾取消任务 #ID”供复查。
  - 如果模型输出仍建议重复创建任务，服务端在生成 `action_items` 时必须二次拦截，改为 `查看任务 #ID` 操作入口。
- 页面展示：
  - 国家矩阵、产品建议和操作区必须显示已有任务状态，例如 `任务 #123 · 进行中`。
  - 任务 ID 链接到 `/tasks/detail/<task_id>`。
  - 被已有任务拦截的建议应显示“已有任务，不重复排程”的原因。

单产品提示词要求：

- 只分析当前产品。
- 先判断产品阶段：已跑通、放量中、利润风险、国家验证不足、素材不足。
- 给出三类补素材建议之一或组合：
  - `expand_country`
  - `same_country_new_material`
  - `weak_country_retest`
- 如果建议用明空素材，必须选择具体 `material_key` / `video_path`。
- 如果建议创建小语种任务，必须指定源素材和目标国家 / 语言。
- 必须读取 `task_assignments`，已有非取消任务的国家 / 素材只标注任务，不再建议重复排程；已取消任务可以建议重排并说明取消任务 ID。
- 如果数据不足，明确缺什么，不能输出空泛建议。

### 运行进度与单任务锁

AI素材军师每次运行都是一个项目任务，必须像全能视频翻译详情页一样有独立任务页、进度条和步骤卡片，而不是只显示“运行中”。

- 独立详情路由仍使用 `GET /medias/ai-material-strategist/projects/<id>`；项目列表只负责切换项目，不承载运行详情。
- 项目表新增 `progress_json`，刷新页面后仍能看到当前执行到哪一步、完成百分比、当前产品和最近日志。
- 运行步骤固定为：
  1. `snapshot`: 读取数据窗口和数据新鲜度。
  2. `candidate_score`: 规则预筛和候选打分。
  3. `ai_ranking`: Top 20 AI 分批复评。
  4. `material_context`: 读取国家反馈、本地素材、明空素材和任务中心排程。
  5. `product_analysis`: 逐产品 AI 分析，显示 `current_product_index / total_products`。
  6. `persist`: 落库项目结果。
  7. `summary`: 汇总项目结论。
- `progress_json` 至少包含 `percent`、`current_step`、`current_step_label`、`message`、`steps[]`、`logs[]`、`product_progress`。
- `status=running` 时，页面首屏必须显示 sticky 运行状态卡：状态、进度条、百分比、当前动作、当前产品进度。
- 移动端或项目已完成/失败时，运行状态卡不能 sticky 遮挡后续数据；只允许桌面端 `running` 项目使用 sticky 运行卡。
- 移动端成功态项目应优先展示报告主体，可隐藏已完成步骤卡片和日志明细，避免完成进度信息占满首屏。
- 步骤卡片展示 `等待中 / 运行中 / 已完成 / 失败` 四类状态，失败时显示错误信息。
- 同一时间只能有一个 AI素材军师项目运行。创建新项目前必须检查是否已有 `status='running'` 项目：
  - 如果有，API 返回 `409`，payload 带 `running_project` 和其详情路由。
  - 前端不创建新项目，提示用户正在运行的项目，并跳转到该项目独立页。
- 项目成功或失败都必须把最终进度写入 `progress_json`，避免页面长期停留在运行中。

### 断点续跑与恢复

AI素材军师项目必须能从同一个 `project_id` 恢复执行，不能因为 Web 进程重启、后台线程中断或 LLM 临时失败就要求重新创建项目、重新从头跑。

- 项目记录是断点载体：
  - `data_window_json`、`data_snapshot_json` 存在时，恢复时复用已生成数据窗口和候选输入，不重新读取快照。
  - `ranking_prompt_json`、`ranking_result_json` 存在时，恢复时复用 Top 20 排名结果，不重复调用排名模型。
  - `ai_material_strategist_product_results` 已存在的产品结果视为已完成，恢复时从下一个未完成产品继续逐产品分析。
- 逐产品分析必须边跑边落库。完成一个产品就写入 `ai_material_strategist_product_results`，并更新 `progress_json.product_progress`；不能等 20 个产品全部跑完才统一写入。
- 恢复入口必须支持接回历史 `status='running'` 项目：
  - 运维/后台恢复时传入指定 `project_id`，服务层校验项目存在且未成功完成。
  - 如果项目处于 `failed`，允许清空 `error_message` 并按已落库断点继续。
  - 如果项目已 `success`，恢复入口只返回现有结果，不重复执行。
- 单任务锁只限制同一时间一个执行器真正运行；锁释放后，已有 `running` 项目可以被恢复执行，但新建项目仍需被 `409` 拦截。
- 恢复后的进度日志必须明确显示“从断点恢复”，并把跳过的阶段标记为已完成，让页面能解释为什么没有重新调用前置步骤。
- Web 服务启动时必须扫描 `status='running'` 的 AI素材军师项目，将其标记为 `service_restart` 恢复原因并自动拉起同一个 `project_id` 的执行器；当前业务只允许一个项目运行，因此只恢复最新一条 running 项目。
- 启动自动恢复不得打开通用任务 runner 自动重跑策略，避免影响翻译、字幕、批量任务等历史上只做状态标记的启动恢复逻辑。
- 启动恢复写入 `progress_json.recovery` 和最新日志，页面刷新后能看到“检测到服务重启/从断点恢复”状态；执行器每次 checkpoint 更新 `progress_json.runner_heartbeat_at`，作为判断运行线程是否仍在推进的轻量心跳。

输出契约：

```json
{
  "product_id": 0,
  "product_code": "",
  "overall_judgement": "",
  "priority": "P0|P1|P2|P3",
  "primary_action": "expand_country|same_country_new_material|weak_country_retest|hold|investigate",
  "country_actions": [
    {
      "country_code": "DE",
      "decision": "scale|test|retest|hold|stop",
      "reason": "",
      "material_action": "reuse_existing|localize_mingkong|create_new|none"
    }
  ],
  "recommended_materials": [
    {
      "source": "local|mingkong|new",
      "material_id": "",
      "material_key": "",
      "filename": "",
      "video_path": "",
      "target_countries": ["DE"],
      "reason": ""
    }
  ],
  "operation_entries": [
    {
      "type": "view_video|view_feedback|import_to_library|create_translation_task|view_task",
      "label": "",
      "payload": {}
    }
  ],
  "evidence": [],
  "risks": [],
  "next_steps": []
}
```

## 页面设计

### 项目列表

路由：`GET /medias/ai-material-strategist`

- 顶部按钮：`新建项目 / 运行一轮`。
- 项目列表：项目名、状态、生成时间、Top 20 数量、P0/P1 建议数、失败原因。
- 点击项目进入详情页。

### 项目详情

路由：`GET /medias/ai-material-strategist/projects/<id>`

首屏展示：

- 项目状态、数据窗口、数据质量、新鲜度。
- 运行中项目的进度条、当前步骤、逐产品分析进度和步骤卡片。
- Top 20 总览卡。
- 图表：
  - 30 天消耗 vs 真实 ROAS 散点图，点大小为订单量。
  - Top 20 订单 / 消耗 / ROAS 横向条形图。
  - 国家矩阵：EN/DE/FR/ES/IT/JP/SE/NL/PT，颜色表示 scale / test / retest / hold / stop。

产品详情区：

- 左侧产品卡：主图、名称、code、30天/7天/昨天指标。
- 中间国家表现：素材数、推送数、活跃消耗、ROAS。
- 右侧 AI 建议：主动作、推荐素材、下一步。
- 素材卡片：
  - 本地素材卡：可播放视频，展示翻译后广告反馈。
  - 明空素材卡：封面、视频名、90 天消耗、广告数、昨日增量、播放入口。

操作入口：

- `看视频`：复用明空视频代理或本地对象 URL。
- `查看反馈数据`：打开素材广告表现详情。
- `加入素材库`：复用明空入库接口和进度弹窗。
- `创建小语种翻译任务`：复用现有素材管理创建任务弹窗，预填产品、源素材、目标语言和紧急任务选项；EN 建议只展示英语素材表现和补素材方向，不生成小语种翻译任务入口。
- `查看任务 #ID`：当建议目标已有待处理、进行中或已完成任务时展示，跳转任务详情，不重复创建任务。

### 公开分享报告

项目详情页需要支持生成公开分享链接，方便把 AI素材军师报告发给未登录用户查看。

- 后台详情页保留鉴权：`GET /medias/ai-material-strategist/projects/<id>` 仍需登录、管理员和 `medias` 权限。
- 分享生成接口需鉴权：登录管理员在报告详情页点击“分享”后，接口生成或复用一个不可枚举的 `share_token`。
- 公开报告页不鉴权：`GET /medias/ai-material-strategist/share/<share_token>` 不需要登录，也不检查用户权限。
- 公开报告 JSON 不鉴权：`GET /medias/api/ai-material-strategist/share/<share_token>` 只按 token 返回单个项目报告，不提供项目列表。
- 公开链接不能使用连续项目 ID 作为访问凭据；必须使用随机 token，避免外部枚举历史项目。
- 公开页面不能显示主站左侧菜单栏、顶部后台操作区、运行按钮、刷新按钮或素材操作入口；页面应单纯呈现分析报告。
- 公开页面里的任务、产品、素材等内部入口只做文本展示，不跳转内部工作台，避免分享页暴露后台操作路径。

## 流程图与提示词可视化

项目详情页展示运行流程：

1. 读取数据窗口。
2. 聚合产品广告 / 订单 / 利润。
3. 聚合国家与素材反馈。
4. 读取明空素材候选。
5. 规则预筛和打分。
6. 分批 Top 20 AI 复评。
7. 逐产品 AI 分析。
8. 汇总项目结论。
9. 落库。

LLM 节点提供 `提示词` 按钮，展示：

- use case
- provider / model
- system prompt
- user prompt
- input JSON
- request payload
- response schema
- raw response / parsed response summary

## 验证

后端 focused tests：

- Top 20 规则打分不会让低量高 ROAS 产品进榜。
- `meta_ad_realtime_daily_ad_metrics` 按 `(business_date, ad_account_id)` 取最新快照。
- 本地产品 code 去掉 `-rjc` 后能匹配明空素材快照。
- `投放素材AI分析` 两个 AI use case 当前默认 provider 都是 `google_wj`，model 都是 `gemini-3.5-flash`；`AI素材军师` 原 use case 保持 OpenRouter。
- 单产品 prompt 包含 EN + 8 小语种阶梯、明空素材候选、本地素材和翻译反馈。
- AI 返回的操作入口能序列化到项目详情。
- 已有待处理 / 进行中 / 已完成任务时，服务端不会生成重复 `create_translation_task`，而是输出任务链接。
- 已取消任务允许重新建议排程，同时保留已取消任务标注。
- 运行项目会持续写入 `progress_json`，序列化接口返回进度。
- 已有 running 项目时，新建接口返回 `409` 并带可跳转项目。

前端 focused tests：

- 左侧菜单任务中心下方存在管理员可见 `投放素材AI分析` 入口，素材管理子 Tab 显示 `AI素材军师` 且指向旧入口。
- 项目列表、项目详情路由未登录 302，登录有权限 200。
- `AI素材军师` 项目列表提供删除按钮，删除请求带 `X-CSRFToken`，运行中项目删除返回 `409`。
- 项目详情渲染 Top 20 表、图表容器、国家矩阵、素材卡片和操作按钮。
- POST 创建项目带 `X-CSRFToken`。

验证命令首选：

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

若脚本无直接覆盖，改跑：

```bash
pytest tests/test_ai_material_strategist.py tests/test_ai_material_strategist_routes.py tests/test_ad_material_ai_analysis.py tests/test_ad_material_ai_analysis_routes.py tests/test_llm_use_cases_registry.py tests/test_ai_billing.py -q
node --check web/static/ai_material_strategist.js
node --check web/static/ad_material_ai_analysis.js
```

本功能不需要全量 pytest，除非后续实现触碰 pytest fixture、鉴权、部署、LLM provider 基础设施或共享调度逻辑。
