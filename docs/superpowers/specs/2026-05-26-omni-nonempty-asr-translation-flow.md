# 2026-05-26 Omni 非空 ASR 继续翻译规则

## 背景

全能视频翻译详情页出现“源 ASR 37 字符，已跳过字幕生成并保留原视频”。根因是 ASR 文本少于 50 个可见字符时，流水线把任务标记为 `media_passthrough_mode=original_video`，后续 `alignment`、`translate`、`tts`、`subtitle`、`compose`、`export` 都走原视频直通。

## 规则

- 只要 ASR 返回了至少一段有效结果，就继续正常翻译流程，不再按 ASR 字符数触发原视频直通。
- 只有完全没有 ASR 结果时，才保留“音乐/无语音视频直通原视频”的兜底行为。
- 旧任务里已经写入的 `media_passthrough_reason=short_asr` 视为遗留状态，不再允许它短路下游步骤。
- 重跑 ASR 后如果不再直通，必须清空旧的 `media_passthrough_*` 标记，避免下游步骤继续短路。

## 验证

- `_resolve_original_video_passthrough([{"text": "tiny"}])` 应返回 `enabled=False`。
- `_is_original_video_passthrough({"media_passthrough_mode": "original_video", "media_passthrough_reason": "short_asr", "media_passthrough_source_chars": 37})` 应返回 `False`。
- `_step_asr` 遇到短但非空 ASR 时，只完成 ASR 步骤，后续步骤保持待执行。
- `_step_asr` 遇到空 ASR 时，仍可完成原视频直通。
