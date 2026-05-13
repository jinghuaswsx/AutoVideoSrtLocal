# Omni 镜头翻译模型标签展示修复

日期：2026-05-13

## 文档锚点

- `AGENTS.md` "文档驱动代码"：新要求先落文档，再以文档作为代码修改锚点。
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md` §3/§6：Omni `shot_char_limit` 使用 `translate_lab.shot_translate` 能力点执行镜头级翻译。
- `docs/superpowers/specs/2026-05-01-llm-client-consolidation-design.md` §2.1/§3：LLM 调用由 use_case binding 解析 provider/model，业务展示不应硬编码供应商。
- `docs/superpowers/notes/llm-direct-sdk-allowlist.md` "过渡期保留入口"：runtime/web 后续应改为直接读 binding。

## 背景

Omni 详情页的"翻译本土化"步骤在 `shot_char_limit` 路径会显示模型标签。当前标签写死为 `gemini · <model>`，但 `translate_lab.shot_translate` 的实际供应商来自 `llm_use_case_bindings.provider_code`，可能是 `gemini_aistudio`、`gemini_vertex`、`openrouter` 等。

用户期望这里显示"供应商 + 模型 id"，便于从详情页直接确认本次镜头级翻译实际走的渠道。

2026-05-13 追加要求：Omni 翻译本土化要改为 OpenRouter `google/gemini-3-flash-preview`，并以并发模式执行镜头级翻译。用户会在发布后强制重新开始任务，因此旧任务里已写入的模型标签不做迁移，只保证重新运行时使用新配置。

## 设计

- `appcore/runtime_omni_steps.py::step_translate_shot_limit` 解析 `llm_bindings.resolve("translate_lab.shot_translate")` 后，同时读取 `provider` 和 `model`。
- `model_tag` 格式统一为 `<provider> · <model_id>`。
- 若 binding 缺字段，保留空字符串兜底，不影响任务执行。
- `translate_lab.shot_translate` 的注册默认值改为 `openrouter / google/gemini-3-flash-preview / openrouter`，避免未自定义 binding 的环境回落到 AI Studio Pro。
- `pipeline.translate_v2` 写入 LLM debug payload 时读取当前 `llm_bindings.resolve("translate_lab.shot_translate")`，与实际调用的 provider/model 保持一致。
- `shot_char_limit` 翻译单元默认使用小并发池执行，完成后按原 translation unit 顺序写回 `translations`、artifact 和下游 TTS 输入。
- 并发路径不依赖上一句译文作为当前句 prompt 上下文；保留后一条原文上下文。需要严格串行上下文时，可通过环境变量把并发数降为 1。
- 不改变计费、preset schema 或非 `shot_char_limit` 翻译路径。

## 验收

- 当 `llm_bindings.resolve("translate_lab.shot_translate")` 返回 `{"provider": "gemini_aistudio", "model": "gemini-3.1-pro-preview"}` 时，任务状态中的 `step_model_tags.translate` 必须是 `gemini_aistudio · gemini-3.1-pro-preview`。
- 默认注册表中 `translate_lab.shot_translate` 必须是 `openrouter · google/gemini-3-flash-preview`。
- 当当前 binding 是 `openrouter / google/gemini-3-flash-preview` 时，`translate_shot()` 产生的 debug payload 也必须写同一组 provider/model。
- 多个 translation unit 的 `shot_char_limit` 任务应并发调用 `translate_shot()`，但最终 `translations` 顺序必须仍等于原 unit 顺序。
- 相关 Omni dispatch 测试通过。
