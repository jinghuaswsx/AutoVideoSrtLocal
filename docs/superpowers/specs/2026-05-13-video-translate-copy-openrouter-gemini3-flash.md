# 视频翻译文案模型切换到 OpenRouter Gemini 3 Flash

日期：2026-05-13

## 文档锚点

- `AGENTS.md`「文档驱动代码」：新要求先落文档，再以文档作为代码修改锚点。
- `AGENTS.md`「LLM 调用」：新业务和默认模型在 `appcore/llm_use_cases.py` 注册，运行时通过 `appcore.llm_client` 和 binding 解析。
- `docs/superpowers/specs/2026-04-30-translate-quality-eval-report.md` §5.1：`video_translate.localize` 已评估建议切到 OpenRouter `google/gemini-3-flash-preview`。
- `docs/superpowers/specs/2026-05-01-llm-client-consolidation-design.md` §2.1/§3：视频翻译文案调用统一通过 use case binding 管理。
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md` §3/§6：全能视频翻译标准翻译路径复用视频翻译文案能力点。

## 背景

用户要求把整个视频翻译模块的文案翻译全部切到 OpenRouter 通道的 Gemini 3 Flash。此前只有部分路径已是 OpenRouter Gemini 3 Flash，仍有 `gemini_vertex`、`openai/gpt-5.5` 或 `gemini_aistudio` 默认值会在空 binding 或重置默认时回落到其他通道。

## 范围

本次只调整视频翻译相关的文案生成、文案翻译、文案重写和 ASR 文本标准化翻译 use case 默认绑定与现有 DB binding。

覆盖 use case：

- `video_translate.localize`
- `video_translate.tts_script`
- `video_translate.rewrite`
- `video_translate.source_normalize`
- `video_translate.av_localize`
- `video_translate.av_rewrite`
- `asr_normalize.translate_zh_to_en`
- `asr_normalize.translate_es_to_en`
- `asr_normalize.translate_generic_to_en`
- `ja_translate.localize`
- `ja_translate.rewrite`
- `translate_lab.shot_translate`
- `translate_lab.tts_refine`

目标绑定统一为：

```text
provider_code = openrouter
model_id = google/gemini-3-flash-preview
usage_log_service = openrouter
```

不改变：

- ASR：`video_translate.asr`
- TTS 配音：`video_translate.tts`
- TTS 语言校验：`video_translate.tts_language_check`
- 画面笔记、视频分析、质量评估、音画同步审计等非文案翻译 use case
- prompt 内容、运行时步骤顺序、前端 UI

## 设计

1. 修改 `appcore/llm_use_cases.py` 中上述 use case 的默认 provider/model/service。
2. 新增 DB migration，强制把现有 `llm_use_case_bindings` 中上述 use case 的启用 binding 切到 OpenRouter Gemini 3 Flash；没有记录的环境插入新记录。
3. 更新 registry 测试，防止未来默认值回退到 Vertex、GPT-5.5 或 AI Studio。
4. 增加 migration smoke test，确保迁移覆盖完整 use case 清单并使用 `ON DUPLICATE KEY UPDATE` 覆盖现有 binding。

## 验收

- `tests/test_llm_use_cases_registry.py` 中视频翻译文案 use case 默认值均为 `openrouter / google/gemini-3-flash-preview / openrouter`。
- 新 migration 文件包含全部覆盖 use case，并通过 `ON DUPLICATE KEY UPDATE` 写回 `provider_code`、`model_id`、`enabled`。
- ASR、TTS 配音、视频分析类 use case 默认值不被本次测试要求改变。
