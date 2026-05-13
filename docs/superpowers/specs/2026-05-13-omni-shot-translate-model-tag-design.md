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

## 设计

- `appcore/runtime_omni_steps.py::step_translate_shot_limit` 解析 `llm_bindings.resolve("translate_lab.shot_translate")` 后，同时读取 `provider` 和 `model`。
- `model_tag` 格式统一为 `<provider> · <model_id>`。
- 若 binding 缺字段，保留空字符串兜底，不影响任务执行。
- 不改变翻译调用、计费、prompt 或 preset 行为。

## 验收

- 当 `llm_bindings.resolve("translate_lab.shot_translate")` 返回 `{"provider": "gemini_aistudio", "model": "gemini-3.1-pro-preview"}` 时，任务状态中的 `step_model_tags.translate` 必须是 `gemini_aistudio · gemini-3.1-pro-preview`。
- 相关 Omni dispatch 测试通过。
