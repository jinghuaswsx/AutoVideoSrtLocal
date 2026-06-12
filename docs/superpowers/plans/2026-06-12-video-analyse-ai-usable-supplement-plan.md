# 投放素材AI分析可用版（素材补充计划）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/video-analyse-ai` 模块修成可信、可运维、直接输出「素材补充计划」的版本。

**Architecture:** 全部改动围绕 `appcore/ad_material_ai_analysis.py`（评估逻辑）、`web/routes/video_analyse_ai.py`（resume 接口 + 公开白名单）、`web/app.py`（启动恢复）、`web/static/ad_material_ai_analysis.js` + `web/templates/video_analyse_ai.html`（计划表 + 继续按钮）。评估逻辑改动原则：确定性规则给优先级，LLM 给解释和边界判断；国家评审 5 国合并为 1 次调用降本提质。

**Tech Stack:** Flask + MySQL + GoogleWJ Gemini（llm_client.invoke_generate）+ 原生 JS。

**验证命令:** `pytest tests/test_ad_material_ai_analysis.py tests/test_ad_material_ai_analysis_routes.py tests/test_video_analyse_ai_routes.py -q && node --check web/static/ad_material_ai_analysis.js`

---

### Task 1: 蛇形分桶修复排名锦标赛偏差

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py`（`_chunked` 调用处 :1768 附近）
- Test: `tests/test_ad_material_ai_analysis.py`

- [x] **Step 1: 写失败测试** — `_snake_batches` 把按分数降序的列表交错分配，每批都覆盖高中低段
- [x] **Step 2: 实现 `_snake_batches(items, batch_count)`**：`batches[i % n] 或蛇形 (i//n 偶数正序奇数反序)`，`_run_ai_ranking` 中 `_chunked(candidates, 20)` 替换为 `_snake_batches(candidates, 3)`（候选不足 20 时退化为 1 批）
- [x] **Step 3: 跑测试通过后 commit**

### Task 2: 排名输入加保本 ROAS

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py:786-795`
- Test: `tests/test_ad_material_ai_analysis.py`

- [x] `_rank_input` keys 加 `effective_breakeven_roas`，并派生 `roas_vs_breakeven = round(true_roas_30d / breakeven, 4)`（breakeven>0 且 roas 非空时，否则 None）；`_ranking_prompt` 加一句「roas_vs_breakeven >= 1 代表已过保本线；不同产品保本线不同，效率判断必须参照该字段而非绝对 ROAS」。测试断言两个字段存在与计算正确。

### Task 3: daily/realtime 同业务日双计修复

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py`（`_load_ad_rows` :476-541、`_load_product_ad_rows_for_materials` :1041-1107）
- Test: `tests/test_ad_material_ai_analysis.py`

- [x] 两处 realtime 查询加 `AND m.business_date > %s`（下限 = daily 路径已覆盖的最大业务日）。`_load_ad_rows`：下限 = `MAX(DATE(COALESCE(meta_business_date,report_date)))`（查询 meta_ad_daily_campaign_metrics，空表用 date_from-1）与 `date_from-1` 取大者。`_load_product_ad_rows_for_materials`：同理查 meta_ad_daily_ad_metrics（按 product_id）。测试用 fake db.query 断言 realtime SQL 含 `m.business_date > %s` 且参数含 daily max。

### Task 4: 产品优先级与单素材评审解耦

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py`（`_fallback_product_analysis` :2198、`_run_product_analysis` :2340-2353）
- Test: `tests/test_ad_material_ai_analysis.py`

- [x] 新增 `_derive_product_priority(product) -> str`：用保本线判断 `roas_ok = true_roas_30d >= effective_breakeven_roas`（breakeven<=0 时退化 `>=1.5`）；P0: spend30>=300 且 orders30>=30 且 roas_ok；P1: spend30>=100 且 orders30>=10 且 (roas_ok 或 profit_30d>0)；P2: spend30>=50 或 orders30>=8；其余 P3。`_fallback_product_analysis` 的 priority 改用它。
- [x] `_run_product_analysis` AI 分支：**删除** quality_score→priority 映射与「不通过→hold」降级；新逻辑：priority 保持确定性值；`material_review_result` 附 `reviewed_material_key`（mk_materials[0] 的 key）；`final_decision=='不通过'` 时给 fallback 的 material_actions[0] 加 `candidate_rejected: true` + reason 追加「AI评审不通过该候选素材，建议换条候选或自制新素材」，country_actions/primary_action 不动。
- [x] 测试：AI 返回 quality_score=20/不通过 时，priority 仍由规则决定、primary_action 不变为 hold、material_actions[0].candidate_rejected 为 True。

### Task 5: 5 国评审合并为 1 次调用 + LLM spacing + 心跳

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py`（国家评审段 :3398-3958、主循环 :3012-3035）
- Test: `tests/test_ad_material_ai_analysis.py`

- [x] 新 `COMBINED_COUNTRY_REVIEW_RESPONSE_SCHEMA = {"type":"object","properties":{"country_reviews":{"type":"array","items": <现有单国 schema>}},"required":["country_reviews"]}`。
- [x] 新 `_build_combined_country_review_input(...)`：`{current_date, product_global(原样), countries: [每国 {target_country, country_performance(+note 累计值语义), available_materials(该语言), existing_tasks}]}`。
- [x] 新 `_combined_country_review_prompt(payload)`：沿用单国 40/40/20 评分规则文本，要求一次性输出 5 国数组、跨国横向一致（同样商品全局分各国应一致）、每国结构同现有 schema。
- [x] 新 `_run_country_reviews(product, eval_countries, countries_by_code, local_materials, mk_materials, task_assignments, *, project_id, user_id, run_ai) -> dict[str, dict]`：1 次 invoke_generate（max_output_tokens=8192, timeout 180）；解析数组→按 country_code 入 dict；缺国/坏国用 `_fallback_country_review` 补；整体异常全 fallback 并带 ai_error。
- [x] 新 `_pace_llm()`：模块级 `_LAST_LLM_AT`，间隔 = env `AD_MATERIAL_AI_ANALYSIS_LLM_SPACING_SECONDS`（默认 2.0，<=0 关闭）；在 `_run_ai_ranking` 每批、`_run_product_analysis`、`_run_country_reviews` 调 LLM 前调用。
- [x] 主循环替换 5 国 for-loop 为单次 `_run_country_reviews`；产品内在素材评审前 / 国家评审前各 checkpoint 一次（message 标注当前产品当前阶段），缓解心跳空窗。
- [x] 测试：fake invoke 返回 5 国→dict 齐全且只调 1 次；返回 3 国→缺国 fallback 补齐；抛异常→5 国全 fallback。

### Task 6: market_expansion 接入任务拦截

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py:3960-4051`
- Test: `tests/test_ad_material_ai_analysis.py`

- [x] 分类后过滤：target 候选（eu_never+eu_weak、JP entry/retest）排除 `_task_blocks_recommendation(country_data.get("blocking_task"))` 的国家，被排除的记入推荐项 `blocked_countries: [{country_code, task_id, status_label}]`；target 全被排除则不输出该推荐。测试：DE 有 in_progress 任务时 eu_cluster_expansion 的 target_countries 不含 DE 且 blocked_countries 标注任务。

### Task 7: 项目级「素材补充计划」汇总

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py`（新函数 + `_summarize_project` :2508 + 主流程 summary 步骤）
- Test: `tests/test_ad_material_ai_analysis.py`

- [x] 新 `_build_supplement_plan(results, limit_per_product=3, limit_total=40) -> list[dict]`：
  - 数据源：每个产品的 `country_reviews`（action ∈ expand/supplement/retest 且 final_decision != 不通过）；国家有 blocking_task 的跳过；EN 不进计划。
  - 行结构：`{rank_no, product_id, product_code, product_name, priority, country_code, country_name, lang, action, country_quality_score, decision, reason, material_source, material_key, material_name, video_path, cancelled_task_id, entry_type, entry_url}`。
  - 素材选择：supplement→明空候选第一条未被 `candidate_rejected` 的（source=mingkong）；expand/retest→push_count 最高的本地 en 素材（source=local）；都没有→source=new。
  - entry：`create_translation_task` → `/medias/product/video_workbench/<pid>?target_lang=<lang>`。
  - 排序：priority（P0 最前）→ country_quality_score 降序；每产品截 3、总量截 40。
- [x] `_summarize_project` 返回值加 `"supplement_plan": _build_supplement_plan(products)`。
- [x] 测试：2 产品×多国 reviews 构造，断言阻塞国家被跳过、不通过国家被跳过、排序正确、每产品 cap、素材选择 source 正确。

### Task 8: resume 接口 + 启动恢复（P0-1）

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py`（新 `resume_project_checkpoint`、`mark_startup_interrupted_project_for_recovery`、`mark_project_interrupted`；`_progress_update` 写 `runner_heartbeat_at`）
- Modify: `web/routes/video_analyse_ai.py`（`POST /api/projects/<id>/resume`）
- Modify: `web/app.py`（`_run_startup_recovery` 加本模块恢复块）
- Test: `tests/test_ad_material_ai_analysis.py`、`tests/test_video_analyse_ai_routes.py`

- [x] `resume_project_checkpoint(project_id, *, user_id=None)`：仿 strategist :2517 — 锁 timeout 0，忙→`ProjectAlreadyRunningError(get_project(project_id))`；success 直接返回；否则 progress 标 `runner_state='checkpoint_resume_scheduled'` + recovery 日志，status 置 running、清 error，`_mark_other_running_projects_interrupted`，返回项目。
- [x] `mark_project_interrupted(project_id, reason, message)`：status='failed' + error_message + progress 日志（本模块沿用 failed，不引入新枚举）。
- [x] `mark_startup_interrupted_project_for_recovery()`：取最新 running 项目；无→None；progress 标 `recovery={reason:'service_restart',...}`、`runner_state='resume_scheduled'` 写库并返回行 dict；其余 running 项目交 `_mark_other_running_projects_interrupted`。
- [x] 路由 `POST /video-analyse-ai/api/projects/<int:project_id>/resume`（login+admin）：调 resume → `start_background_task(service.run_project, project_id, user_id=...)` → `{"success":true,"project":...}`；`ProjectAlreadyRunningError`→409；项目不存在→404。
- [x] `web/app.py::_run_startup_recovery` 末尾加 `_run_ad_material_ai_analysis_startup_recovery()`（结构同 strategist 块：mark→start_background_task(run_project)→失败 mark_project_interrupted）。
- [x] 测试：服务层（fake db）resume 在 success 项目上直接返回、锁忙 409 语义；路由层 POST resume 鉴权 302/200。

### Task 9: 公开分享白名单序列化（D2）

**Files:**
- Modify: `web/routes/video_analyse_ai.py:50-87`
- Test: `tests/test_video_analyse_ai_routes.py`

- [x] `_public_project_payload(project, share_token)` 重写为**白名单构造**（删除 `_strip_public_links` 黑名单方案）：
  - project 级：id/project_name/status/started_at/finished_at/progress{percent,current_step_label,message}/summary 白名单{top_product_count, priority_counts, action_counts, data_window, data_quality, ranking_mode, supplement_plan(去 entry_url/entry_type)}/public=true。
  - product 级：rank_no/product_code/product_name/metrics 白名单（spend_30d, spend_7d, spend_yesterday, orders_30d, orders_7d, true_roas_30d, meta_roas_30d, ad_count_30d）——**不含** profit/revenue/breakeven；country_summary 白名单（country_code,country_name,lang,lang_name,tier,item_count,pushed_video_count,ad_spend_usd,ad_roas,active_7d_ad_spend_usd,delivery_status）；local_materials（id,lang,display_name,duration_seconds,push_count,video_url=/medias/obj/<object_key>）；mingkong_materials（material_key,video_name,video_url 带 share_token,cover_url,cumulative_90_spend,video_ads_count,yesterday_spend_delta,video_duration_seconds）；ai_result 白名单（overall_judgement,priority,primary_action,country_actions[country_code,lang,action,reason,duplicate_suppressed,existing_task{task_id,status_label}],material_review_result{final_decision,quality_score,reviewed_material_key,score_breakdown,analysis_reason,material_plan},task_summary）；country_reviews（每国 final_decision,quality_score,score_breakdown,analysis_reason,recommended_action,mode）；market_expansion（type,source_countries,target_countries,priority,reason,blocked_countries）。**绝不包含**：data_snapshot、ranking_result、ranking_prompt、material_review_input、material_review_prompt_debug、action_items、payload/method/url 类字段。
- [x] 测试：构造含全部敏感字段的 project，断言公开 payload 序列化后文本不含 `material_review_prompt_debug`、`ranking_result`、`profit_30d`、`base_roas`、`data_snapshot`，且保留 supplement_plan 行与明空 video_url 带 token。

### Task 10: 前端 — 素材补充计划卡 + 继续未完成按钮

**Files:**
- Modify: `web/static/ad_material_ai_analysis.js`、`web/templates/video_analyse_ai.html`（JS 版本参数）
- Test: `node --check web/static/ad_material_ai_analysis.js`

- [x] `renderSupplementPlan(project)`：summary.supplement_plan 为空不渲染；表格列：优先级徽标/产品/国家/动作（expand→扩国家、supplement→补新素材、retest→二次验证）/推荐素材（source 徽标+名称）/评分/理由/操作（publicMode 隐藏操作列；entry_url 渲染为「去创建」链接）。插入 `renderProject` 的 `renderMetrics` 之后。
- [x] resume 按钮：`renderRunProgress` 内，条件 `project.status==='failed' || (project.status==='running' && progress.updated_at 距今 > 10 分钟)` 时渲染「继续未完成」（publicMode 不渲染）；`resumeProject()` POST `${API_BASE}/projects/<id>/resume`（csrfHeaders），成功 toast + `loadProject`，409 切到运行中项目。事件用 data-action 委托（沿用现有按钮模式）。
- [x] 模板 JS 引用 `?v=` 版本参数 bump。

### Task 11: 文档 + 全量验证收尾

**Files:**
- Modify: `docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md`（追加「2026-06-12 投放素材AI分析可用版收口」节）
- Verify: 跑 focused tests + node --check

- [x] spec 追加行为变化记录：蛇形分桶、保本线入排名、priority 解耦语义、5 国合并调用、spacing env、supplement_plan 字段、resume 接口、启动恢复、公开白名单。
- [x] `pytest tests/test_ad_material_ai_analysis.py tests/test_ad_material_ai_analysis_routes.py tests/test_video_analyse_ai_routes.py -q`；`node --check web/static/ad_material_ai_analysis.js`；修复回归。
- [x] commit。

## Self-Review

- 覆盖审查报告的 P0-1（Task 8）、D2（Task 9）、A1（Task 1）、A2（Task 2）、B1（Task 4）、B4（Task 3）、C6（Task 6）、降本合并+spacing（Task 5）、新交付物 supplement plan（Task 7/10）。C1/C2（真实买家国家、语言维度窗口数据）明确不在本轮（需要新数据管道，下轮做）。
- 类型一致性：`_run_country_reviews` 返回 `dict[str, dict]` 与原 `country_reviews` 落库结构一致，前端零兼容成本；supplement_plan 挂 summary_json，公开页通过 summary 白名单透出。
