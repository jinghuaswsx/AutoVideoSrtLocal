# Omni ASR Clean Artifact Display Fix

日期：2026-05-13

## 文档锚点

- `docs/superpowers/specs/2026-04-26-translation-pipeline-overhaul-design.md` §5.2：Omni 的原文纯净化由 `_step_asr_clean` 执行，替代 `asr_normalize`。
- `docs/superpowers/specs/2026-05-07-omni-dynamic-resume-and-prompt-display-fix.md`：Omni 详情页步骤和恢复入口必须基于任务真实 `plugin_config`，不能把 `asr_clean` / `asr_normalize` 硬别名。
- `web/templates/CLAUDE.md`：翻译详情页新增展示必须留在 `_task_workbench.html` 的 step 卡片内。

## 背景

生产任务 `d8aba350-231a-45f4-909a-fb4ed77b6d75` 的 `plugin_config.asr_post` 为 `asr_clean`，`steps.asr_clean` 已完成，且任务状态里存在 `utterances_raw` 和 `utterances`。但是 `artifacts.asr_clean` 缺失，前端 `renderStepPreviews()` 只在 artifact 存在时渲染原文纯净化对比，导致“原文纯净化”卡片没有展示纯净化后的结果。

## 需求

1. `asr_clean` 步骤跑完后，无论真实纯净化成功、校验失败保留原文、无文本跳过，还是 resume 幂等跳过，都必须有可展示的 `artifacts.asr_clean`。
2. 历史任务如果已经有 `utterances_raw` / `utterances` 但缺少 `artifacts.asr_clean`，详情页必须在“原文纯净化”卡片内展示可用结果，不要求重跑任务。
3. `asr_normalize` preset 仍显示自身步骤，不把它映射成 `asr_clean`。
4. 详情页“强制重新开始”必须清空所有展示型中间态，仅保留源视频、缩略图、任务身份和用户选择的配置；不能让 `utterances_raw`、`source_full_text`、`shots`、`shot_notes`、`media_passthrough_*`、`tts_generation_summary`、`step_model_tags`、`llm_debug_refs`、`recommended_voice_id` 等上一轮状态在重跑后继续驱动前端 fallback 展示。

## 设计

- 后端 `_step_asr_clean` 增加统一 artifact 构建逻辑：
  - `input_utterances` 来自纯净化前文本；
  - `utterances` 来自纯净化后文本，跳过或失败时使用当前可用文本；
  - `cleaned` 明确记录成功与否，`skipped` / `skip_reason` 记录跳过原因。
- resume 幂等跳过时，如果 artifact 缺失，使用 `utterances_raw` 和 `utterances` 补写 `artifacts.asr_clean`。
- 前端 `renderStepPreviews()` 在 `step === "asr_clean"` 且 artifact 缺失时，若 `currentTask.utterances_raw` 或 `currentTask.utterances` 有数据，则合成只用于展示的 artifact，再调用既有 `renderAsrCleanArtifact()`。
- `web.services.task_restart.restart_task()` 的 reset payload 覆盖所有从 ASR、分镜、翻译、TTS、音画审计和提示词检查器产生的派生状态；restart 后前端只能看到 pending step、源视频 preview 和新一轮运行即时写入的数据。

## 验收

- 新任务的 `_step_asr_clean` 在 resume 跳过时仍能写入 `artifacts.asr_clean`。
- 历史任务缺少 `artifacts.asr_clean` 时，前端仍能用 `utterances_raw` / `utterances` 展示“纯净化前 / 纯净化后”。
- “强制重新开始”后，上一轮 ASR clean fallback、shot translate fallback、TTS 轮次统计、模型标签和 LLM debug refs 不再残留。
- 相关 focused pytest 通过。
