# Omni 分镜请求前 480p 预处理

日期：2026-05-14

## 文档锚点

- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md` §3：Omni 的 `shot_decompose` 是可配置能力点，负责用 Gemini 视觉分析视频并产出镜头列表。
- `docs/superpowers/specs/2026-05-14-omni-defer-shot-decompose-after-voice-design.md`：`shot_decompose` 只要求在 `translate` 前存在；原视频仍用于 ASR、对齐、TTS、合成和导出。
- 本 spec 固化 2026-05-14 实测结论：60 秒 1080x1920 视频直接走 OpenRouter base64 请求约 96 MiB JSON、229.464 秒；转为 480p/15fps/600k/无音频后约 5.76 MiB JSON、10.110 秒，转码耗时约 2.03 秒。

## 背景

`shot_decompose.run` 默认走 `openrouter / google/gemini-3-flash-preview`。当前 OpenRouter adapter 会把本地视频读成 `data:video/mp4;base64,...` 放进 `video_url`，请求体大小与视频文件大小强相关。1080p 原视频会显著拉长请求构造、上传和模型处理等待。

分镜阶段只需要视觉镜头边界和画面描述，不依赖音频。音频内容已经由 ASR 主时间轴提供，后续合成也继续使用原视频。

## 目标

在 Omni `shot_decompose` 大模型请求前，生成一个仅用于 LLM 输入的轻量视频：

```text
max height 480px, keep aspect ratio, 15fps, H.264, target 600k, maxrate 800k, bufsize 1200k, no audio
```

`decompose_shots()` 应把该轻量视频传给 `invoke_generate("shot_decompose.run", media=[...])`，但 prompt 里的总时长、分镜归一化和后续流程仍使用原视频时长。

## 非目标

- 不改变 `shot_decompose.run` 的 provider/model 绑定。
- 不改变 prompt、JSON schema、分镜归一化规则或 ASR 对齐算法。
- 不把原视频替换成 480p 版本用于合成、导出或其他步骤。
- 不为 `video_review`、`video_score`、`video_csk` 等其他视频分析链路引入同样逻辑。

## 运行规则

- 默认启用预处理；仅 `shot_decompose` 请求使用预处理产物。
- 若源视频已经不高于 480p 且可以直接满足体积目标，也允许复用原文件，避免无意义重编码。
- 若 `ffmpeg` 预处理失败或产物无效，分镜步骤降级使用原视频请求，并写 warning 日志；任务不因预处理失败直接中断。
- 临时 480p 文件写入任务目录或系统临时目录，并在请求完成后清理，避免长期占用磁盘。
- debug payload 的 `request_payload.media` 应记录实际传给 LLM 的路径；`input_snapshot` 同时记录原视频路径、LLM 视频路径、预处理是否启用、预处理失败原因。

## 验收

- `decompose_shots(video_path=原视频)` 调用 LLM 时，`media` 参数默认是 480p 预处理产物路径，而不是原视频路径。
- 预处理命令包含 `scale=-2:min(480\,ih)`、`fps=15`、`-b:v 600k`、`-maxrate 800k`、`-bufsize 1200k`、`-an`。
- 预处理失败时仍能用原视频完成分镜调用。
- Omni `shot_decompose` debug payload 能看出实际请求媒体与原视频的对应关系。
