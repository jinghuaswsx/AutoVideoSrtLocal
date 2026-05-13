# Omni LLM 卡片提示词可视化设计

日期：2026-05-13

## 文档锚点

- `AGENTS.md`：LLM 调用统一入口为 `appcore.llm_client`，新业务需可审计。
- `docs/superpowers/specs/2026-05-07-omni-dynamic-resume-and-prompt-display-fix.md` §修复范围 6：omni 动态步骤如果产生 LLM 调用记录，前端提示词检查器必须能显示对应步骤按钮和 payload。
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md` §3/§6：omni 的 `shot_decompose`、`shot_char_limit`、`av_sentence`、`sentence_reconcile` 是可组合动态能力点。
- `web/templates/CLAUDE.md`：翻译详情页必须通过 `_translate_detail_shell.html` / `_task_workbench.html` 的既有 block 和卡片结构扩展，禁止在 shell 外追加孤立 HTML。

## 背景

`multi_translate` 详情页已通过 `llm_debug_refs`、`/llm-debug/<step>` 和 `_task_workbench` 提示词检查器展示实际 messages 与请求报文。omni 合并后复用了 multi 的前端检查器，但部分从实验路径接入的 LLM 调用没有落 `llm_debug_refs`，导致对应步骤卡片没有「提示词」按钮。

目标任务 `d8aba350-231a-45f4-909a-fb4ed77b6d75` 的 `plugin_config` 为：

- `shot_decompose=true`
- `translate_algo=shot_char_limit`
- `tts_strategy=sentence_reconcile`
- `subtitle=sentence_units`

该任务状态里 `shot_decompose` 与 `translate` 已有模型标签和产物，但 `llm_debug_refs` 只包含 `asr_clean`、`av_sync_audit`、`quality_assessment`，缺少分镜、镜头级翻译和句级收敛相关 LLM 请求。

## 范围

本次只补齐已有卡片的提示词可视化数据，不新增新的业务流程或重做布局。

需要落 debug payload 的 LLM 调用：

1. `shot_decompose` 卡片：`pipeline.shot_decompose.decompose_shots()` 的 `shot_decompose.run` 多模态请求。
2. `translate` 卡片：
   - `pipeline.translate_v2.translate_shot()` 的 `translate_lab.shot_translate` 初译和超长重试。
   - `pipeline.av_source_normalize.normalize_source_segments()` 的 `video_translate.source_normalize`。
   - `pipeline.shot_notes.generate_shot_notes()` 的 `video_translate.shot_notes`。
   - `pipeline.av_translate.generate_av_localized_translation()` 的 `video_translate.av_localize`。
3. `tts` 卡片：
   - `pipeline.duration_reconcile.reconcile_duration()` 调用 `pipeline.av_translate.rewrite_one()` 产生的 `video_translate.av_rewrite`。
   - `appcore.tts_language_guard.validate_tts_script_language_or_raise()` 的 `video_translate.tts_language_check`。

已有能力保持：

- `asr_clean` / `asr_normalize` 继续走现有 `_llm_debug_calls` 保存逻辑。
- `standard` 翻译继续走现有 `localized_translate_messages.json`。
- `five_round_rewrite` TTS 继续走现有 `tts` prompt debug。
- `av_sync_audit`、`quality_assessment`、`video_ai_review` 继续走现有 debug refs。

## 设计

沿用 multi 的数据模型：

- 业务函数在返回值中携带 `_llm_debug_calls: list[dict]`，每个 dict 使用 `appcore.llm_debug_payloads.prompt_file_payload()` 格式。
- runtime/profile 层在对应 step 完成前调用 `appcore.llm_debug_runtime.save_llm_debug_calls()`。
- 前端不新增卡片，仅依赖现有 `renderLlmDebugButtons()`：当 `currentTask.llm_debug_refs[step]` 非空时，在对应 `.step-name-row` 中插入「步骤名提示词」按钮。

对生成式接口统一记录：

- `messages`：把 generate 类 prompt 映射为 `system/user` 消息；没有 system 时只记录 user prompt。
- `request_payload`：使用 `build_generate_request_payload()` 或 `build_chat_request_payload()`，记录 use case、provider、model、prompt/messages、schema、temperature、max tokens、media 文件名等。
- `input_snapshot`：记录关键输入，如分镜原文、字符上限、目标语种、句级收敛上下文。

## 非目标

- 不改 `multi_translate` 的生产语义。
- 不改变 LLM prompt 本身。
- 不改变任务步骤顺序、resume 逻辑、preset 校验或数据库 schema。
- 不为历史任务补写缺失文件；历史任务只有在重新跑对应步骤后才会生成新 debug payload。

## 验收

- omni `shot_decompose` 跑过后，任务状态有 `llm_debug_refs.shot_decompose`，前端卡片显示「镜头分镜提示词」。
- omni `shot_char_limit` 跑过后，任务状态有 `llm_debug_refs.translate`，至少包含每段镜头初译，超字符重试也作为同一步多次调用展示。
- omni `av_sentence` 翻译路径跑过后，`translate` 卡片能展示 source normalize、shot notes、AV localize 的提示词和请求报文。
- omni `sentence_reconcile` TTS 跑过后，`tts` 卡片能展示语言校验和句级 rewrite 调用。
- `/api/omni-translate/<id>/llm-debug/<step>` 继续拒绝路径穿越；debug 文件仍只允许从任务目录读取。
- 相关单测通过。
