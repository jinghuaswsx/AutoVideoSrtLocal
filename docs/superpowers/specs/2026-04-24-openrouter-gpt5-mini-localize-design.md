# OpenRouter GPT 5-mini 本土化模型接入设计

## 背景

当前多语言视频翻译里的“文本翻译本土化”流程已经支持通过 OpenRouter 走 Claude Sonnet 与 Gemini 系列模型，但还没有 `GPT 5-mini` 作为可选项。用户希望复用现有 `OPENROUTER_API_KEY`，把 `GPT 5-mini` 接到现有 OpenRouter 通道里，用作翻译本土化模型的一个新选项。

## 目标

- 在不新增新服务商配置的前提下，接入一个新的 OpenRouter 模型选项
- 让用户可以在设置页把主线翻译默认模型切到 `GPT 5-mini`
- 让任务工作台里的“翻译本土化配置”弹窗也能选到 `GPT 5-mini`
- 继续复用现有翻译链路、计费归因和 OpenRouter Key 存储方式

## 非目标

- 不改 `video_translate.localize` 的 use case binding 默认值
- 不新增独立的 OpenAI 供应商接入
- 不改 OpenRouter 的 `base_url` / `model_id` 覆盖逻辑
- 不调整翻译 prompt、本土化逻辑或 billing schema

## 方案

### 1. 模型接入点

在 `pipeline/translate.py` 的 `_OPENROUTER_PREF_MODELS` 中新增一个 provider key，例如 `gpt_5_mini`，映射到 OpenRouter 的真实模型 ID。

### 2. 偏好值白名单

在 `appcore/runtime.py` 与 `web/routes/settings.py` 中把 `gpt_5_mini` 加入允许的 `translate_pref` 值，确保设置保存后主线 runtime 可以接受并读取这个偏好。

### 3. UI 入口

- `web/templates/settings.html`
  在“默认翻译模型”的 OpenRouter 分组里新增 `GPT 5-mini` 选项
- `web/templates/_task_workbench.html`
  在“翻译本土化配置”弹窗里新增 `GPT 5-mini (OpenRouter)` 选项

### 4. 兼容性

- 旧值 `openrouter` 继续回落到 Claude Sonnet，不变
- 若用户在 OpenRouter 服务设置里手动填写 `model_id`，仍然保留现有“用户自定义覆盖优先”的行为
- billing 继续按 `openrouter` provider 记账，不新增服务类型

## 验证

- `pipeline.translate.resolve_provider_config("gpt_5_mini")` 能解析到正确模型 ID
- 保存 `translate_pref=gpt_5_mini` 后，runtime 能识别该偏好值
- 设置页与任务工作台能看到新的模型选项

## 风险与控制

- 风险：任务弹窗和设置页只改一处会造成“默认可选但手动重翻译不可选”或反之
- 控制：同时覆盖主设置入口和任务工作台入口，并补对应测试
