# Block 2 — tts_script 防静默改写 + asr_clean 可靠性（P0）

- **日期**: 2026-06-12
- **状态**: Approved（待实施）
- **总览**: [2026-06-12-omni-quality-overview.md](2026-06-12-omni-quality-overview.md)（红线必读）
- **实施计划**: [plans/2026-06-12-omni-quality-block2-deterministic-guards.md](../plans/2026-06-12-omni-quality-block2-deterministic-guards.md)
- **改动层**: 确定性校验 / 参数与绑定，不动时长窗口与时间轴 → 音画对齐零影响

## 背景与问题

1. **tts_script 可静默改写译文**：5 轮收敛循环每轮用 LLM 重切分朗读块（use case `video_translate.tts_script`），prompt 要求 "preserve the exact wording"，但 `pipeline/localization.py::validate_tts_script` 只校验 subtitle_chunks 与 blocks 拼接一致，**从不校验 blocks 文本 == 输入 sentences 文本**。模型换词、漏句、加词不会被发现，改写后的文本直接成为最终配音和字幕，绕过了所有翻译质量保证。校验所需的 `_subtitle_word_signature` 工具就在同一文件。
2. **asr_clean 长视频静默放弃清洗**：`pipeline/asr_clean.py::_call` 写死 `max_tokens=4000`。长视频 utterances 多时输出被截断 → JSON 不完整或 length mismatch → 主路、兜底相继失败 → **静默保留 ASR 原文继续**，下游翻译吃进未清洗的噪声。
3. **asr_clean 兜底名存实亡**：`appcore/llm_use_cases.py` 中 `asr_clean.purify_fallback` 描述写"Claude Sonnet 兜底"，实际默认绑定 `openrouter / google/gemini-3-flash-preview`——主路（gemini-3.1-flash-lite）失败后用**同家族**模型重跑同样 prompt，异构兜底的设计意图已失效。

## 目标

1. tts_script 产物与输入译文做词级一致性硬校验：不一致 → 带反馈重试一次 → 仍不一致 → **确定性回退**（不再信任 LLM 切分），保证"送 TTS 的文本 == 翻译产物"恒成立。
2. asr_clean 的 max_tokens 按输入规模动态计算，消除长视频截断。
3. asr_clean 兜底恢复异家族模型（Claude Sonnet 4.6 via OpenRouter），描述与绑定一致。

## 非目标

- 不改 tts_script 的 prompt 内容（block1/3 负责 prompt 层）。
- 不把空格语言整体切换为确定性切分（保留 LLM 切分的朗读节奏优化价值；本块只兜底）。
- 不动 `validate_tts_script` 的既有校验（chunks vs blocks、max_words）。
- 不动 ja 路径（`build_tts_script_from_localized` 已是确定性构建，天然满足）。

## 需求细则

### R1 词级一致性校验

- `pipeline/localization.py` 新增异常 `class TtsScriptWordingMismatchError(ValueError)`。
- `validate_tts_script(payload, sentences=None, max_words=10)` 在现有校验通过后追加：当 `sentences` 非空时，比较 `_subtitle_word_signature(" ".join(blocks 文本))` 与 `_subtitle_word_signature(" ".join(sentences 文本))`；不一致 → raise `TtsScriptWordingMismatchError`，错误信息包含首个差异位置附近的两边词序列片段（各 ≤15 词），便于日志定位。
- 词签名 = 小写词序列（既有 `_subtitle_word_signature`），天然忽略标点/大小写差异——blocks 允许为朗读节奏调整标点，**词序列必须逐词一致**。

### R2 重试 + 确定性回退

`pipeline/translate.py::_generate_tts_script_single` 捕获 `TtsScriptWordingMismatchError`：

1. **重试一次**：在原 messages 末尾追加一条 user 消息：`"Your previous attempt changed the wording. Reproduce the input sentences with EXACT wording — same words in the same order. Only regroup them into blocks and subtitle_chunks."`，重新调用并再次校验。
2. **仍失败 → 确定性回退**：不再调 LLM，直接构造：
   - `blocks` = 输入 sentences 一句一块：`{"index": i, "text": s["text"], "sentence_indices": [i], "source_segment_indices": s["source_segment_indices"]}`；
   - `full_text` = blocks 拼接；
   - `subtitle_chunks` = `_rebuild_subtitle_chunks(blocks, ...)`（既有函数）；
   - 结果标记 `result["_wording_fallback"] = True`，供轮次记录与 UI 排查（duration round record 加 `tts_script_source: "wording_fallback"`，与现有 `"deterministic"` 并列）。
3. batched 路径（`_generate_tts_script_batched`）自动受益（其逐批调 `_single`）；最终合并后的 `validate_fn(merged, sentences=...)` 若再抛 mismatch（极小概率，由批边界引起），同样走整体确定性回退。
4. **es/it 模块适配**：`pipeline/localization_es.py` / `localization_it.py` 若导出自有 `validate_tts_script`，必须内部复用 `pipeline.localization` 的词签名校验 helper（抽出 `ensure_tts_script_wording(blocks, sentences)` 公共函数供两处调用），保证三条路径行为一致。

### R3 asr_clean max_tokens 动态

`pipeline/asr_clean.py::_call` 的 `max_tokens` 改为按输入估算：

```
est = 600 + sum(每条 utterance 文本长度) × 2 + len(utterances) × 30
max_tokens = min(16000, max(4000, est))
```

（输出为同语言全文 JSON 回显，约等于输入文本量 + JSON 结构开销；×2 为非拉丁文字 token 膨胀冗余。系数实现时可微调，原则：**宁可偏大，禁止再出现输出截断**。）同时把计算出的值写进 debug payload（`request_payload` 已含 max_tokens，确认透传）。

### R4 asr_clean 兜底绑定修正

`appcore/llm_use_cases.py` 的 `asr_clean.purify_fallback`：
- 默认绑定改为 `openrouter / anthropic/claude-sonnet-4.6`；
- 描述保持/修正为「Claude Sonnet 兜底：主路校验失败时换异家族模型重跑同样 prompt」。
- 注意：该默认值仅影响 DB 无绑定行的冷启动；现网 DB `llm_use_case_bindings` 若已有行，需在验收说明中提示管理员在 `/settings?tab=bindings` 同步改一次（或提供 SQL）。**不要写自动迁移**。

## 验收标准

1. 单测覆盖：词签名一致通过 / 改词触发 mismatch / 重试成功 / 重试失败走确定性回退（断言 `_wording_fallback` 与 blocks==sentences 文本）/ max_tokens 估算函数边界（小输入下限 4000、大输入封顶 16000）。
2. `python3 scripts/pytest_related.py --base origin/master --run` 通过。
3. 改动文件不含 `_pipeline_runner.py` 时长逻辑（允许只加 round_record 的 `tts_script_source` 标记读取，如该标记在 runner 侧记录则为 ≤3 行的赋值改动）、不含 multi 模块。
4. 人工验收：跑一条 omni V2 任务，确认 tts_script 步骤正常、artifact 中无 wording_fallback（正常路径不触发）；构造 mock 测试证明回退路径产物可被下游 `build_tts_segments` 正常消费。
