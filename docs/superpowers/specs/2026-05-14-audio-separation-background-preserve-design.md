# 音频分离背景保留修复设计

日期：2026-05-14

## 背景

全能视频翻译的能力点表中，“⑦ 人声分离”定义为用 audio-separator 分离人声和背景音，配音后跟原 BGM 重新混音。当前实现把 ASR 输入音频（16kHz / mono / WAV）继续压成 192kbps MP3 上传到 AudioSeparator，并默认传 `ensemble_preset=vocal_balanced`。这个链路优先提取人声，对背景音乐和环境音的保真不够，导致最终翻译配音视频的背景保留差。

## 锚点

- `AGENTS.md`：开发必须先有文档锚点；常规改动在隔离 worktree；禁止连接 Windows / 本机 MySQL `127.0.0.1:3306` 做验证。
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md` 的“⑦ 人声分离”：`voice_separation` 是 multi / omni 共有能力，用于分离人声和背景音，后续与配音重新混音。
- AudioSeparator `docs/API.md`：`POST /separate/download` 是 multipart 文件上传并下载 ZIP 的同步接口；`output_format` 支持 `WAV`；`ensemble_preset` 作为旧协议兼容字段。
- 2026-05-14 新版服务端调用口径：

```bash
curl -X POST http://127.0.0.1:83/separate/download \
  -F "file=@source_separation.wav" \
  -F "separation_goal=background_preserve" \
  -F "output_format=WAV" \
  -o separated.zip
```

## 目标

1. ASR 输入继续保持现有 `16kHz / mono / PCM WAV`，不改变识别链路。
2. 人声分离使用独立高保真输入：`44.1kHz / stereo / PCM WAV`。
3. AudioSeparator 客户端直接上传高保真 WAV，不再转成 192kbps MP3。
4. 默认分离目标为 `background_preserve`，调用 `/separate/download` 时传 `separation_goal=background_preserve` 和 `output_format=WAV`。
5. 保留旧 `ensemble_preset` 兼容能力；背景保留默认链路不得再传 `vocal_balanced`。
6. 设置页提供“分离目标”字段，默认 `background_preserve`；若保留 preset 下拉，至少包含 `instrumental_full`、`instrumental_balanced`、`instrumental_clean`。

## 非目标

- 不改 TTS、字幕、合成、响度匹配算法本身。
- 不改 AudioSeparator 服务端实现；本仓库只改调用端和本地配置展示。
- 不连接或依赖 Windows / 本机 MySQL 做验证。
- 不重启生产 / 测试服务。

## 设计

### 抽音

`pipeline.extract.extract_audio(video_path, output_dir)` 保持现状：输出 `*_audio.wav`，参数为 `pcm_s16le`、`-ar 16000`、`-ac 1`，供 ASR 使用。

新增 `pipeline.extract.extract_separation_audio(video_path, output_dir)`：

- 输出文件名：`<base>_separation.wav`
- ffmpeg 参数：`-vn -acodec pcm_s16le -ar 44100 -ac 2`
- 只供 AudioSeparator 上传使用，不替代 ASR 输入。

### Runtime 状态

`PipelineRunner._step_extract` 同时调用：

- `extract_audio(...)` -> `task["audio_path"]`
- `extract_separation_audio(...)` -> `task["separation_audio_path"]`

`task_state` 更新时保留两个路径。音频预览仍指向 `audio_path`，避免浏览器预览和 ASR 口径变化。

`PipelineRunner._step_separate` 取输入时优先：

1. `task["separation_audio_path"]`
2. `task["audio_path"]`

降级回退保留是为了兼容旧任务 resume 和历史 task state。

### AudioSeparator 客户端

`appcore.audio_separation_client.SeparationClient` 不再执行 `_ensure_mp3` / `_mp3_upload_path`。`separate()` 直接把传入文件作为 multipart `file` 上传。WAV 文件 MIME 使用 `audio/wav`，其它格式用 `application/octet-stream` 即可。

`POST /separate/download` 表单默认发送：

```python
{
    "separation_goal": "background_preserve",
    "output_format": "WAV",
}
```

`ensemble_preset` 仅作为兼容字段：当调用方显式配置了非空 preset 且不等于旧默认 `vocal_balanced` 时才附带；默认背景保留流程不发送 `vocal_balanced`。

返回 ZIP 内仍按 `(Vocals)` / `(Instrumental)` stem 名查找，保存为 `vocals.wav` 和 `accompaniment.wav`，保持下游 loudness / mix 逻辑不变。

### 设置页

`pipeline.audio_separation` 增加 `audio_separation_goal` 设置键，默认 `background_preserve`。

设置页新增“分离目标”输入/下拉，默认 `background_preserve`。preset 下拉保留为旧服务端兼容或高级调试项，并补齐：

- `instrumental_full`
- `instrumental_balanced`
- `instrumental_clean`

### 测试

必须覆盖：

1. WAV 输入调用 `SeparationClient.separate()` 不会触发 MP3 转码。
2. `requests.post(..., data=...)` 包含 `separation_goal=background_preserve` 和 `output_format=WAV`。
3. 默认背景保留链路不发送 `ensemble_preset=vocal_balanced`。
4. `_step_separate` 优先使用 `task["separation_audio_path"]`，缺失时回退 `audio_path`。
5. `extract_separation_audio()` 的 ffmpeg 参数为 `44100Hz stereo PCM WAV`。

## 验收

- 相关 pytest 通过。
- diff 中不存在新的 MySQL 本机连接、服务重启、部署脚本调用。
- 新任务的 ASR 输入和分离输入路径不同，分离输入为高保真 WAV。
