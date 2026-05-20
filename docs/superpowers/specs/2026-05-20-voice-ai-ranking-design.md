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
