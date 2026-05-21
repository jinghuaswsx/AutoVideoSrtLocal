# TTS 音色大模型排名 V1

日期：2026-05-20

## 目标

在多语种视频翻译、全能视频翻译的音色选择阶段，对现有音色相似度匹配出的前 10 个候选音色追加一次大模型评估排名。V1 只做侧边评估，不改写原有相似度排序、不自动替用户选音色、不做二次复核。

## 输入

- 原始视频抽取的说话人音频样本，优先使用音色匹配阶段已有的短样本。
- 当前音色匹配前 10 个候选，包括 voice_id、name、provider、gender、locale、相似度、语速匹配分、预览语速等结构化信息。
- 候选音色的 preview 音频：优先读取音色库已经本地化存储的 mp3；缺失时才从 ElevenLabs preview_url 下载兜底。随后生成 3-10 秒 mp3 样本。截断优先选 3-10 秒之间的气口/静音点，找不到时才在 10 秒处硬截。
- 任务语言、原始文案片段、目标语言等上下文信息。

## 模型与通道

- use case：`voice_selection.assess`
- 默认 provider：`openrouter`
- 默认 model：`google/gemini-3.5-flash`
- 可配置通道：`openrouter`、`gemini_vertex_adc`、`gemini_aistudio`
- 调用方式：单轮结构化 JSON 输出。

## 输出

每个可评估候选输出：

- `voice_id`
- `llm_rank`
- `reason_summary`：30 字以内中文摘要，用于音色卡片胶囊展示。

结果写入任务 state，并回灌到候选音色对象的 `llm_rank`、`llm_reason_summary` 字段。前端在原音色匹配排名后展示 `AI #N · 原因摘要` 胶囊。

同时写入 `voice_ai_rank_debug`：

- 请求 Tab：模型、参数、原始 prompt、音频文件列表、候选结构化信息、脱敏后的 OpenRouter 报文。
- 结果 Tab：大模型返回原文、结构化排名、每个候选的可视化排名卡片。

音色选择标题行显示「大模型音色选择排名」按钮；点击按钮或任一 AI 排名胶囊打开同一个弹窗。

## 护栏

- 大模型失败、缺少音频、preview 下载失败时，不影响现有音色选择流程。
- 只对当前 Top10 做排名，忽略模型返回的非候选 voice_id 和重复 voice_id。
- 原有 `voice_match_candidates`、fallback voice、手动选择逻辑保持兼容。

## 2026-05-20 调通补充

- 为排查结构化输出问题，音色大模型排名支持 `candidate_limit`，默认先取 Top3；需要恢复 Top10 时设置 `VOICE_AI_RANK_CANDIDATE_LIMIT=10` 或调用接口传 `candidate_limit=10`。
- English Redub 增加管理员 POST 接口 `/api/english-redub/<task_id>/voice-ai-ranking`，用于对已生成的 `voice_match_candidates` 直接重跑大模型排名并写回任务 state；调通顺序为 Top3 先验证，再 Top10。
- 模型返回缺少 `llm_rank` 时，后端按模型返回顺序补齐 rank；缺少 `reason_summary` 时填充「模型未给原因」，避免原始 JSON 有 voice_id 但可视化结果为空。
- Prompt 明确要求每条 ranking 必须包含 `candidate_key`、`llm_rank`、`reason_summary`，并给出 JSON 示例，降低 OpenRouter/Gemini 忽略 schema 的概率。
- Live Top3 smoke found Gemini may truncate long ElevenLabs `voice_id`; the request now assigns stable short `candidate_key` values (`C1`..`C10`), and backend normalization maps the key back to the real `voice_id`.
- Live Top10 smoke found low output-token caps could stop before all rows; the request now uses a per-call response schema with `minItems`/`maxItems` equal to the candidate count and raises the output cap to 4096.

## 2026-05-20 配置化补充

- Top10 调通后，默认 `candidate_limit` 恢复为 10；临时 smoke 仍可用 `VOICE_AI_RANK_CANDIDATE_LIMIT` 或重跑接口 body 覆盖。
- 音色大模型排名不再硬编码 provider/model，运行时读取 `/settings?tab=bindings` 的 `voice_selection.assess`。
- `voice_selection.assess` 在模块模型分配里只允许三个通道：`openrouter`、`gemini_vertex_adc`、`gemini_aistudio`；默认仍为 `openrouter` + `google/gemini-3.5-flash`。
- 切到 `gemini_vertex_adc` 或 `gemini_aistudio` 时，运行时会把 `google/gemini-3.5-flash` 规范化为 `gemini-3.5-flash`，避免 Google 原生通道收到 OpenRouter 模型 ID。

## 2026-05-20 API 调用日志与账单追踪

- 音色大模型排名继续走统一入口 `appcore.llm_client.invoke_generate`，由 `appcore.ai_billing.log_request` 写入 `usage_logs`，并将脱敏请求/响应写入 `usage_log_payloads`。
- `llm_client` 会把本次调用写入后的 `usage_log_id` 放回结果；业务层写入 `voice_ai_rank_usage_log_id`，并同步放入 `voice_ai_rank_debug.usage_log_id` 与结果原始调试信息，方便从任务页面反查 API 调用日志。
- 账单 `billing_extra` 固定标记 `source=voice_ai_ranking`，并携带 `task_id`、`candidate_limit`、`candidate_count`、`media_count`。use case 仍为 `voice_selection.assess`，价格表沿用 `openrouter / google/gemini-3.5-flash` token 计费；若 OpenRouter 响应返回 cost，则账单优先使用响应成本。

## 2026-05-20 Multi / Omni 补充

- Multi-language video translation and Omni video translation use the same
  `voice_ai_ranking` sidecar as English Redub after TTS voice-match candidates
  are available.
- The LLM ranking is reference-only: it writes `voice_ai_rankings`,
  `voice_ai_rank_debug`, and `llm_rank` / `llm_reason_summary` on candidate
  rows for display, but it does not change the timbre/speed recommendation
  order, auto-select a voice, resume the pipeline, or alter TTS generation.
- Admin rerun endpoints are available at
  `/api/multi-translate/<task_id>/voice-ai-ranking` and
  `/api/omni-translate/<task_id>/voice-ai-ranking`, matching the English Redub
  rerun contract and accepting optional `candidate_limit`.

## 2026-05-20 性别筛选缓存补充

- 音色选择器存在 3 个 AI 排名场景：全部音色、男声、女声。每个场景最多触发一次大模型排名，总计最多 3 次；后续再点同一场景的 AI 排名按钮只读取项目状态缓存。
- 项目 state 新增 `voice_ai_rank_cache`，按 `all` / `male` / `female` 存储该场景的候选签名、排名结果、带 `llm_rank` 的候选列表、debug 报文、模型、provider 和状态。
- 点击男声或女声时，后端先重算当前性别候选，再查对应缓存；命中时把缓存里的 `llm_rank` / `llm_reason_summary` 回填到候选卡片。未命中专属缓存时，先从 `all` 的第一轮 AI 排名里筛出当前性别候选并重新压缩排名，例如第一轮 Top10 里有 5 个女声，则切换女声后展示 `AI #1..#5`；仍无可用排名时才展示无 AI 标签候选，等待用户点击「重新AI排名」。
- 取消男声或女声筛选回到全部音色时，使用 `all` 缓存；如果初始自动 AI 排名已经完成，要在首次切换前回填到 `all` 缓存，避免切换后丢失原始排名。
- 标题行的「大模型音色选择排名」按钮和新增「重新AI排名」按钮必须常驻。前者打开当前场景的排名/报文弹窗，没有结果时展示空态；后者对当前场景执行“缓存优先、未命中才调用大模型”的排名动作。

## 2026-05-20 Multi/Omni 共享后端补充

- Multi 和 Omni 的音色选择接口必须共用同一个后端服务处理 `voice_ai_rank_cache`：
  缓存命中、从 `all` 派生男声/女声排名、未命中状态、手动重跑 AI 排名和
  `usage_log_id` 同步都不能在两个路由里分别实现。
- Omni 页面应呈现与 Multi 页面一致的推荐音色体验：推荐候选置顶、相似度排名、
  AI 排名原因、语速参考、男声/女声筛选和“只看推荐”行为都由共享选择器与共享
  后端契约驱动。

## 2026-05-21 共享音色选择器排序方案 B

- 共享音色选择器覆盖 English Redub、Multi-language video translation 和 Omni
  video translation；三者只通过同一份 `web/static/voice_selector_multi.js`
  决定候选展示顺序，避免模块间排序漂移。
- 当当前场景（`all` / `male` / `female`）存在已完成的 AI 排名，且候选行带有
  正整数 `llm_rank` 时，只重排推荐候选里的前 10 名：
  `llm_rank` 1..10 按升序展示。
- 第 11-20 名保持原有共享选择器顺序，也就是继续沿用 voice match /
  speed-aware 输出的原始顺序；不能因为存在 `llm_rank` 就把 11-20 名也改成
  AI 顺序。
- 没有 `llm_rank`、`llm_rank` 非法、AI 排名未完成、当前筛选场景没有命中 AI
  缓存时，排序完全保持原有逻辑：推荐候选按 `voice_match_candidates` 顺序置顶，
  非推荐候选仍按名称排序。
- AI 排名只影响推荐候选列表顺序，不改变：
  已选音色、voice match 相似度徽标、语速参考展示、手动重跑 AI 排名入口、
  后端缓存结构、自动启动流程或 TTS 生成逻辑。

## 2026-05-21 AI 结果门禁与兜底排序

- 默认排序模式为 `ai_rank`。在该模式下，当前场景（`all` / `male` /
  `female`）只有当 AI 排名状态已经完成并可用于当前候选时，用户才可以选择
  TTS 音色并点击“开始处理”。
- AI 排名未完成、仍在运行、失败、当前筛选场景没有可用排名时，音色卡片仍可浏览、
  试听和查看 AI 胶囊，但选择动作必须被阻断；文案要明确提示用户等待 AI 评估，
  或使用兜底按钮继续。
- 共享音色选择器标题按钮区在“重新AI排名”后新增常驻按钮
  “强制音色语速匹配排序”。
- 点击该按钮后，当前前端会切到 `speed_fallback` 模式：推荐候选恢复旧的
  voice match + speed-aware 原始顺序，不再按 AI 排名改写顺序；如果候选上已有
  `llm_rank` / `llm_reason_summary`，仍继续显示在卡片上，仅作辅助参考。
- `speed_fallback` 模式的目标是让任务在 AI 判断异常、AI 排名失败或 AI 排名
  长时间未完成时可以继续推进，避免音色选择卡死。
- 兜底按钮只改变共享选择器前端的排序和选择门禁，不改后端缓存、候选数据结构、
  rerank 接口契约，也不删除已有 AI 评估结果。

## 2026-05-21 AI Top1 自动确认与设置开关

- 视频翻译设置模块（`/settings?tab=omni_preset`，设置页 tab 名称显示为“视频翻译设置”）新增全局开关“自动音色选择”，默认开启。
- 开关开启时，Multi-language video translation、Omni video translation 和
  English Redub 共享音色选择器在当前场景 AI 排名完成后，必须自动选择
  `llm_rank=1` 的音色，并直接复用现有 `/confirm-voice` 流程进入后续步骤，
  不再等待人工点击“开始处理”。
- 自动确认只在 `ai_rank` 模式下生效；若用户主动点击“强制音色语速匹配排序”
  进入 `speed_fallback` 模式，则继续保留人工选择 / 人工确认路径。
- 开关关闭时，行为恢复为上一版：AI 排名完成后挂起等待人工干预，允许用户手动选
  音色或使用兜底排序按钮继续。
- 该开关虽然放在“视频翻译设置”入口下，但仍是共享音色选择器的全局行为开关；
  Multi、Omni、English Redub 三个模块继续共用这一配置，不拆分成模块级独立开关。
- 自动确认逻辑不新增独立后端接口，不改 `confirm-voice` 契约，不跳过现有字幕样式
  参数写回，也不改变 `selected_voice_id` / `task_state` / resume step 的后端语义。
