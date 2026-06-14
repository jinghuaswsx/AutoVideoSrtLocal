# 文案封面重选模型后生成设计

日期：2026-06-10
状态：已确认

## 背景

`docs/superpowers/specs/2026-05-14-video-cover-generation-design.md` 已定义文案封面项目的四步自动流程，`docs/superpowers/specs/2026-05-15-video-cover-image-provider-concurrency-design.md` 已定义第四步封面生成的供应商、模型池和执行方式。

生产使用中，第四步封面生成完成后，运营需要在不重跑视频分析、产品分析、文案创作的前提下，重新选择图片模型并生成封面。入口位置为详情页第四步「封面生成」卡片标题行右侧操作区。

## 目标

- 在详情页第四步「封面生成」卡片中增加「重选模型后生成」按钮。
- 点击后弹出模型选择弹窗，仅选择第四步封面生成的供应商和模型。
- 弹窗可选范围复用文案封面默认配置中的第四步图片模型池。
- 新增 `GOOGLEWJ` 图片通道，模型池只开放：
  - `nano_banana_2`：`gemini-3.1-flash-image-preview`
  - `nano_banana_pro`：`gemini-3-pro-image-preview`
- 「重选模型后生成」触发后只清空并重跑第四步 `cover_generation`；前三步结果和文案保持不变。
- 本入口统一按串行执行，不展示也不提交并发选项。

## 非目标

- 不改变新建项目和「默认配置」弹窗的四步配置结构。
- 不重跑前三步分析与文案。
- 不新增单张封面重试；仍按项目当前 `image_count` 重新生成全部封面。
- 不新增新的凭据存储行；`GOOGLEWJ` 复用现有 `google_wj_image` / `google_wj_text` 配置。

## 2026-06-13 步骤级模型重选与项目快照保留修订

用户要求把第四步「重选模型后生成」推广到四步卡片，并修复「强制重新开始」误读全局默认配置的问题。

- 步骤级模型重选是单个步骤自己的运行配置：详情页四张步骤卡片都必须提供「重选模型后运行」入口；第四步仍可显示为「重选模型后生成」。
- 点击步骤级入口后只选择当前步骤的供应商和模型 ID，可选范围复用默认配置弹窗中该步骤的模型池。
- 提交后服务端必须把新选择写回当前项目 `state_json.model_defaults[step]`，再从该步骤启动后台链路。
- 从上游步骤重跑时，服务端必须清空该步骤及所有下游步骤的结果、报文、耗时和实际模型记录；上游已完成步骤保持不变。
- 文本步骤重跑按所选文本模型执行；第四步重选模型入口继续强制 `execution_mode="serial"`，避免一次重选触发并发图片请求。
- 「强制重新开始」是项目级重新执行：必须保留当前项目已有的 `state_json.model_defaults` 作为项目基础模型快照，不能重新读取 `system_settings.video_cover_model_defaults`。
- 历史项目如果缺少 `model_defaults`，但已有 `state_json.models` 实际运行记录，强制重新开始和后续后台链路应先从这些历史实际模型恢复项目快照；只有项目内完全没有历史模型记录时，才允许回退到当前全局默认配置。
- 第四步旧接口 `POST /video-cover/api/<task_id>/regenerate-cover` 保留兼容，但前端可统一使用通用步骤运行接口。

## 2026-06-14 步骤重选模型请求参数适配修订

生产项目在第三步「文案创作」从 OpenRouter 重选为 `GOOGLEWJ / gemini-3.5-flash` 后，旧的 OpenRouter `response_format={"type":"json_object"}` 被直接传到 Vertex Gemini，导致 Vertex 把 `json_object` 当成 `generation_config.response_schema.type` 并返回 `400 INVALID_ARGUMENT`。

- 步骤级重选模型后，服务端只保留业务层的“需要 JSON 输出”语义；运行时必须按当前目标 provider / adapter 重新适配请求参数。
- OpenRouter 可以继续使用 `response_format={"type":"json_object"}`。
- Google AI Studio / Google Vertex / GOOGLEWJ 必须把 OpenAI 风格 `json_object` 语义转换为 Gemini 支持的 JSON object schema，例如 `{"type":"object"}`，不得把 `json_object` 原样写入 Gemini `response_schema.type`。
- 回归测试必须覆盖 `google_wj` 文案创作通道，确保步骤重选后不会复用上一个 provider 的参数结构。

## 2026-06-14 默认配置重新开始修订

用户要求在文案封面详情页项目级重跑按钮组中增加「默认配置重新开始」，用于显式丢弃当前项目旧的模型快照，并使用全局默认配置里的最新模型配置重跑全流程。

- 详情页项目级重跑按钮组必须在现有重跑入口右侧增加按钮，按钮文案精确为「默认配置重新开始」。
- 「强制重新开始」继续保留当前项目 `state_json.model_defaults`，不得读取当前全局默认配置。
- 「默认配置重新开始」是新的显式项目级入口：服务端必须在清空中间状态前调用 `video_cover_settings.get_model_defaults()`，用最新全局默认配置覆盖当前项目 `state_json.model_defaults`。
- 「默认配置重新开始」和「强制重新开始」一样按当前选择的 `image_count` 清空四个步骤的中间产物、请求报文、返回报文、耗时、错误和实际运行模型记录，并从 `video_analysis` 开始执行。
- 后续四个步骤运行时必须从刚写入项目状态的最新 `state_json.model_defaults[step]` 取供应商、模型 ID 和封面执行模式，而不是继续使用项目原始保存的供应商和模型区块。
- 回归测试必须覆盖该入口会覆盖旧项目快照、保留用户选择张数、清空旧 `models` / 结果数据，并启动第 1 步。

## 2026-06-14 大模型失败详情前端展示修订

生产项目在 `product_analysis` 使用 `GOOGLEWJ / gemini-3.5-flash` 时，Vertex Gemini 返回 HTTP 200 但 SDK 文本为空，后端只保存了「模型未返回内容」，页面无法看到候选、finish reason、安全反馈、用量或异常原文。

- 文本步骤和封面步骤的大模型调用失败时，后端必须在 `state_json.step_errors[step]` 写入可 JSON 序列化的错误详情，至少包含 `message`、`exception_type`、`provider`、`model_id`、`usage`、`raw_response` 和 `response_text`；有 Python 异常链时保留 `cause` / `cause_type`。
- “模型返回空内容”也属于失败详情场景：业务错误消息保持人话，但必须附带该次 LLM 调用返回的 `raw` / `usage` / `text` / `json`，避免 HTTP 200 空输出被压成无法排查的通用错误。
- `_clear_step_outputs()`、强制重新开始和默认配置重新开始必须同步清空对应步骤及下游的 `step_errors`，避免旧错误污染重跑后的状态。
- 详情页失败步骤的「可视化展现」区域必须直接展示错误摘要和完整错误详情；「提示词」Modal 的结果页和「全部报文预览」也必须包含同一份 `error_detail`。
- 历史任务如果失败时尚未保存 `step_errors`，前端只能展示既有 `step_messages[step]`；新失败必须完整可见。

## 2026-06-14 Gemini thinking 预算修订

同一生产项目在 `product_analysis` 使用 `GOOGLEWJ / gemini-3.5-flash` 继续重跑时，前端错误详情显示 Vertex Gemini 候选 `finish_reason=MAX_TOKENS`，`usage_metadata.thoughts_token_count` 接近业务层传入的 `max_output_tokens=3600`，正文候选 token 极少，导致 SDK `resp.text` 为空并触发「模型未返回内容」。

- 文案封面产品分析是信息抽取和营销判断任务，不需要模型消耗大量 hidden thinking；Google AI Studio / Google Vertex / GOOGLEWJ 多模态生成请求必须支持按业务传入 `thinking_budget`，并映射为 Gemini SDK `GenerateContentConfig.thinking_config.thinking_budget`。
- `video_cover.product_analysis` 使用 Gemini 系供应商（`gemini_aistudio`、`gemini_vertex`、`google_wj`）时，必须显式传入 `thinking_budget=0`，避免 hidden thinking 挤占正文输出预算。
- `video_cover.product_analysis` 的正文输出预算必须从 3600 提高到 8192；其他 provider 可沿用该更高预算，避免产品分析报告被正常截断。
- 统一 LLM 入口的请求日志必须记录 `thinking_budget`，方便后续在「全部报文预览」和用量日志中确认该次调用实际配置。
- 回归测试必须覆盖产品分析调用会传入 `max_output_tokens=8192`，且 Gemini 系模型会传入 `thinking_budget=0`；Gemini adapter 测试必须覆盖 `thinking_budget` 被写入 SDK config。

## 后端设计

- `appcore.video_cover_generation.COVER_MODEL_OPTIONS` 增加 `googlewj` 供应商，展示名为 `GOOGLEWJ`。
- `resolve_cover_model_selection("googlewj", ...)` 返回 provider `googlewj` 和裸 Gemini 图片模型 ID。
- `generate_cover_image()` 对 provider `googlewj` 调用 `appcore.gemini_image.generate_image(channel="googlewj")`。
- 新增详情页专用 POST API：
  - `POST /video-cover/api/<task_id>/regenerate-cover`
  - 仅 `@login_required + @admin_required`
  - 入参：`provider`、`model_id`
  - 服务端归一模型选择，并强制 `execution_mode="serial"`
  - 写回 `state_json.model_defaults.cover_generation`
  - 启动后台链路从 `cover_generation` 开始
- 若任务正在运行，返回 409；若前三步未完成，返回 400。
- 通用步骤级入口：
  - `POST /video-cover/api/<task_id>/run/<step>`
  - 入参可为空；为空时沿用项目快照从该步骤继续。
  - 入参包含 `provider` / `model_id` 时，服务端归一该步骤模型选择，写回 `state_json.model_defaults[step]`，清空该步骤及下游输出后从该步骤开始。
  - 若任务正在运行，返回 409；若前序步骤未完成，返回 400。

## 前端设计

- 第四步卡片标题行增加 `重选模型后生成` 按钮。
- 弹窗包含：
  - 供应商下拉
  - 模型 ID 下拉，随供应商联动
  - 取消、生成按钮
- 弹窗初始值优先取当前实际第四步模型，其次取项目级默认配置。
- 提交时带 `X-CSRFToken`。
- 提交成功后关闭弹窗并进入轮询；第四步状态显示为运行中。
- 四张步骤卡片复用同一个模型选择弹窗；弹窗标题、供应商列表和模型下拉随步骤切换。
- 文本步骤按钮文案为「重选模型后运行」，第四步按钮文案为「重选模型后生成」。

## 验收

- 封面生成卡片显示「重选模型后生成」按钮。
- 模型选择弹窗包含 `GOOGLEWJ`，其模型只包含 Nano Banana 2 / Nano Banana Pro。
- 选择 `GOOGLEWJ / Nano Banana Pro` 后提交，状态里的 `model_defaults.cover_generation.provider` 为 `googlewj`，`model_id` 为 `gemini-3-pro-image-preview`，`execution_mode` 为 `serial`。
- 后台只从第四步重新生成，前三步结果保留。
- 运行中重复点击接口返回 409。

## 测试

- `tests/test_video_cover_generation.py`
  - 覆盖 `GOOGLEWJ` 模型池和 `generate_cover_image(channel="googlewj")`。
  - 覆盖详情页渲染按钮和弹窗 JS。
  - 覆盖 `POST /video-cover/api/<task_id>/regenerate-cover` 写回串行配置并从第四步启动。
