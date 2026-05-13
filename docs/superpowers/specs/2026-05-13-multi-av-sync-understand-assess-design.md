# Multi Translate AV Sync Understand/Assess Design

## 背景

多语种视频翻译的音画同步审计必须发生在视频合成之后，审计对象是最终 `hard_video` 成片。Doubao seed 2.0 lite 的职责是读取视频内容，而不是承担复杂的结构化审计和修复决策。

## 目标

- Doubao 只做成片视频理解：画面动作、可见字幕、屏幕文字、直观看到的音画错位观察。
- 程序逻辑负责配对字幕/句级 TTS 时间线，计算每个同步点的窗口、音频时长、差值和候选风险。
- Gemini 3.1 Flash Lite 负责文本侧审计：结合 Doubao 视频理解笔记、最终字幕、程序候选风险，输出中文结构化结论和处理建议。
- 多语种路径只做评估，不自动修改音频、字幕或视频。

## 数据流

1. `compose` 完成后，`av_sync_audit` 解析 `variants.normal.result.hard_video`。
2. `omni_av_sync.understand` 使用 Doubao seed 2.0 lite 读取成片视频，输出自然语言中文视频理解笔记，不要求 JSON。
3. 程序根据 `variants.normal.segments`、`script_segments`、`corrected_subtitle` / `subtitle.normal.srt` 构建同步句表和候选风险。
4. `omni_av_sync.assess` 使用 Gemini 3.1 Flash Lite 输出 `issues` JSON，包含同步点、问题句子、证据、建议动作。
5. 多语种模块到此结束：只展示辅助审计/分析结果，不再进入 `omni_av_sync.verify` 复核，也不自动修改音频、字幕或视频。Omni `safe_auto` 修复链路仍可保留复核。

## 候选风险规则

- 目标画面窗口来自源句时间窗；最终字幕窗口作为辅助上下文。
- TTS 时长超过目标窗口约 12% 或 0.35s 以上，标记为候选“音频太长”。
- TTS 时长短于目标窗口约 18% 或 0.35s 以上，标记为候选“音频太短”。
- 建议优先级：小偏差优先音频变速；偏差较大时建议重写/压缩/扩写文案后重新生成音频；不建议剪画面或移动时间轴。

## 验收

- Doubao debug ref 是 `av_sync_audit.understand`，请求无 `response_schema`。
- Gemini debug ref 是 `av_sync_audit.assess`，请求包含 Doubao 视频理解笔记、最终字幕、程序候选风险。
- 多语种任务只产生 `av_sync_audit.understand` 和 `av_sync_audit.assess` 两类调试记录，不产生 `av_sync_audit.verify`。
- 对既有失败任务，从 `av_sync_audit` 重跑时不会因为 Doubao 非 JSON 输出失败。
