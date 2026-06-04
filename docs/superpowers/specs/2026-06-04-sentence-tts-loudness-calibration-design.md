# 句级 TTS 响度校准设计

## 背景

全能视频翻译的句级 TTS 链路会为每个句子独立生成音频，再按 ASR 时间轴拼回完整音轨。不同句子、不同 TTS 调用或同一音色的不同输出，可能出现句间音量忽大忽小。现有 `loudness_match` 主要处理最终 TTS 与背景音混合后的整体响度，不能保证每个 TTS 句段进入时间轴前的听感稳定。

本次只处理单说话人场景，不做 A/B 多说话人分组，也不改变音色匹配、语速排名或多说话人对话逻辑。

## 用户可见行为

- 新增开关：`句级TTS响度校准`。
- 默认关闭。
- 创建全能视频翻译项目时可手动开启。
- 任务详情页顶部显示同名开关，位置在 `对所有人可见` 左侧，样式复用 `对所有人可见` 的 toggle。
- 详情页切换后只保存配置，不立即改已生成音频。
- 点击 `强制重新开始` 后，重新执行的 TTS 阶段按当前开关生效。

## 配置

在 Omni `plugin_config` 中新增布尔字段：

```json
{
  "sentence_tts_loudness_calibration": false
}
```

默认值为 `false`。校验器负责补默认值并接受布尔值、`0/1`、`true/false` 字符串。该字段不强制改变现有 `loudness_match`，但运行时只有在人声分离结果提供 `vocals_lufs` 时才执行校准。

## 处理链路

仅接入全能视频翻译的 `sentence_reconcile` 句级链路：

1. TTS engine 生成每句 `tts_path`。
2. `reconcile_duration` 完成句级时长收敛。
3. 组装 `final_tts_segments`。
4. 若 `plugin_config.sentence_tts_loudness_calibration == true` 且 `separation.vocals_lufs` 可用：
   - 遍历每个 `final_tts_segments[*].tts_path`。
   - 以 `separation.vocals_lufs` 为目标 LUFS。
   - 调用现有 `appcore.audio_loudness.normalize_to_lufs()` 生成校准后的句段文件。
   - 将该句段 `tts_path` 替换为校准文件路径。
   - 在句段和任务 debug state 中记录输入响度、目标响度、输出响度、是否收敛。
5. `_rebuild_tts_full_audio_from_segments()` 使用校准后的句段按时间轴拼接完整 TTS 音轨。
6. 后续现有 `loudness_match` 保持不变，继续处理最终混音整体响度。

## 降级规则

- 开关关闭：完全保持当前行为。
- `vocals_lufs` 缺失或不可用：跳过句级校准，记录 `skipped_missing_vocals_lufs`。
- 单句文件缺失、测量失败或 loudnorm 失败：跳过该句，记录错误，主任务继续。
- 不对齐整条原视频响度，也不使用背景音响度作为目标。

## 测试范围

- `validate_plugin_config()` 补默认关闭并接受开启值。
- 创建页 JS 会把 `句级TTS响度校准` 写入 `plugin_config`。
- 详情页顶部渲染开关，位于 `对所有人可见` 前。
- 详情页切换开关调用新的 Omni 配置 API 并持久化到 `plugin_config`。
- 句级校准开启且 `vocals_lufs` 可用时，会在重建完整音轨前替换句段 `tts_path`。
- `vocals_lufs` 不可用时，不调用 loudnorm 并记录跳过。
