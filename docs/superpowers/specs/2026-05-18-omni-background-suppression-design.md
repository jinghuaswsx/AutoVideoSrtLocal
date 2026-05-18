# Omni 背景电音抑制设计

日期：2026-05-18

## 背景

全能视频翻译的 `voice_separation` 会把原视频分离成 vocals 和 accompaniment，后续 `loudness_match` / `compose` 再把 TTS 与 accompaniment 混回成品视频。这个默认链路适合保留原 BGM，但当 accompaniment 本身是电子音乐、手机外放声或分离伪影时，成品视频会保留用户反馈的“电音”。

现有任务级响度方案只有 `standard`、`bg_boost`、`manual_boost`，都以“保留或增强背景音”为目标，缺少单个任务的“只保留配音，去掉背景音”方案。

## 目标

1. 增加任务级 `voice_only` loudness profile，用于把背景音量解析为 `0.0`。
2. `voice_only` 不禁用人声分离：分离结果仍用于预览、响度基准和 CapCut 独立音轨导出。
3. 成品 mp4 的 compose 路径必须尊重 `effective_background_volume=0.0`，不能因为 Python truthiness 回退到全局默认背景音量。
4. 任务详情页在“响度匹配”卡片展示“清除背景”选项，用户选择后可从 `loudness_match` 继续重跑。

## 非目标

- 不修改 AudioSeparator 服务端。
- 不全局关闭 `voice_separation` 或改变 Omni 默认 preset。
- 不自动识别音乐类型；本次只提供明确的任务级抑制开关。

## 验收

- `validate_loudness_profile("voice_only")` 合法。
- `resolve_background_volume_profile("voice_only", standard_volume=0.8)` 返回 `effective_background_volume=0.0`。
- `/api/omni-translate/<id>/loudness-profile` 接受 `{"profile": "voice_only"}`。
- compose fallback 混音使用 `0.0` 背景音量，而不是回退到 `0.8`。
