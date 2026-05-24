# AI 精细评估供应商 Profile 配置设计

日期：2026-05-23

## 背景

明孔选品里的“AI 精细评估”有两条入口：

- 前端按钮触发的手动评估，包括单品新建、外链评估、国家重跑。
- `mingkong_fine_ai_auto_evaluation` worker 池触发的自动 AI 精细评估。

现状评估客户端在代码里固定使用 `gemini_vertex_adc` + `gemini-3.5-flash`。这让前端按钮和定时任务无法分别选择供应商，也无法在 API 配置页面统一维护。

## 目标

- 在后台 `/settings` 的“服务商接入 / API 配置”中增加 AI 精细评估模型配置。
- 支持两个独立 profile：
  - 前端按钮点击：默认 `GOOGLE AI STUDIO`。
  - 自动 AI 精细评估 worker：默认 `GOOGLE VERTEX AI ADC`。
- 两个 profile 都只能选择以下供应商：
  - `OPENROUTER`
  - `GOOGLE AI STUDIO`
  - `GOOGLE VERTEX AI`
  - `GOOGLE VERTEX AI ADC`
- 模型统一为 Gemini 3.5 Flash：
  - OpenRouter 使用 `google/gemini-3.5-flash`。
  - Google AI Studio / Vertex / Vertex ADC 使用 `gemini-3.5-flash`。
- 评估 run 的 metadata、进度快照和 LLM trace 必须记录实际 provider/model，便于排查。

## 非目标

- 不新增数据库 schema；配置存入已有 `system_settings`。
- 不改通用 `llm_use_case_bindings` 的语义；该绑定无法区分“前端按钮”和“定时任务”两种入口。
- 不在本地 Windows MySQL 上做任何验证。

## 设计

新增 `appcore.fine_ai_evaluation_model_config` 作为唯一配置入口，负责：

- 定义允许供应商、展示标签、默认 profile。
- 根据 provider 归一化 Gemini 3.5 Flash 的模型 id。
- 从 `system_settings` 读取/保存两个 profile 的 provider。
- 从 `system_settings` 读取/保存国家评估执行模式和国家并发数。
- 对非法 profile/provider 做校验或回退。

`FineAiGeminiClient` 不再硬编码 provider/model，而是在实例化时接收 profile 或 provider/model，并把选择写入请求 payload、billing extra、响应 metadata 和 trace。

`FineAiEvaluationService` 在创建 run 时把 profile/provider/model 写入 run metadata。执行、重跑国家时优先使用 run metadata 里的 provider/model，保证同一个评估 run 后续步骤不会因为设置页改动而漂移。

`mingkong_fine_ai_auto_evaluation` worker 创建 run 时显式使用 `scheduled` profile。前端按钮入口沿用默认 `manual` profile。

### 国家评估并发配置

AI 精细评估分为商品事实提取和多国家评估两段。商品事实提取始终只请求一次；国家评估支持以下配置：

- `fine_ai_evaluation.parallel_mode`：`serial` / `parallel`，默认 `serial`。
- `fine_ai_evaluation.country_concurrency`：国家评估并发数，范围 `1..5`，默认 `1`。

默认保持串行，避免在 Vertex ADC 共享额度下因为突然并发导致 `429`。当后台设置切换为 `parallel` 且并发数大于 `1` 时，`FineAiEvaluationService` 使用任务内线程池并发跑国家评估；实际 worker 数为 `min(country_concurrency, countries_count)`。

并发控制适用于所有 AI 精细评估供应商，包括 `gemini_vertex_adc`。系统不再在代码里针对 ADC 强制回退串行，是否并发由后台配置决定。每个国家仍保持现有失败重试语义：单国家失败后最多重试一轮，第二次不论成功失败都结束该国家并进入汇总。

评估 run metadata 需要记录本轮国家评估执行参数，至少包含配置模式、实际执行模式和实际并发数，便于对比 ADC 通道是否出现 `429` 或耗时变化。

### 请求超时

AI 精细评估不同阶段使用不同的单次 LLM 请求超时：

- 商品事实提取：40 秒。
- 单国家评估主请求：60 秒。2026-05-24 生产自动任务样本中，成功的首次国家评估 median 约 39 秒、P75 约 53 秒、P90 约 76 秒；原 40 秒会截断一批本可完成的请求，因此先放宽到 60 秒作为保守调整。
- JSON 修复请求：40 秒。

这里的超时是传给统一 `llm_client.invoke_generate` 的单次请求超时；服务层的单国家重试和 provider adapter 内部重试可能让 progress 中看到的国家步骤真实耗时高于该值。

## 验收

- 设置页能看到并保存两个 AI 精细评估供应商 profile。
- 未配置时，前端按钮 profile 默认为 AI Studio，定时 profile 默认为 Vertex ADC。
- 未配置时，国家评估模式为串行，并发数为 `1`。
- 设置页能保存国家评估模式和国家并发数；切换为并发后不再因 ADC provider 被代码强制串行。
- OpenRouter 选项保存后，实际 LLM 请求使用 `provider_override=openrouter` 和 `model_override=google/gemini-3.5-flash`。
- 自动 worker 创建的评估 run metadata 使用 scheduled profile。
- 相关 pytest 通过。
