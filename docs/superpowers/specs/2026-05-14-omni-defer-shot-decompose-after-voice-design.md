# Omni 分镜延后到音色确认后执行

日期：2026-05-14

## 文档锚点

- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md` §3/§6：`shot_char_limit` 依赖 `shot_decompose`，但只要求分镜在翻译前存在。
- `docs/superpowers/specs/2026-05-07-omni-dynamic-resume-and-prompt-display-fix.md`：Omni 步骤顺序、恢复入口、前端 `STEP_ORDER` 必须来自真实动态流程。
- `docs/superpowers/specs/2026-05-13-omni-asr-primary-compact-timeline-design.md`：ASR 是主时间轴，镜头只作为翻译上下文。

## 背景

当前默认 preset 使用 `shot_decompose=true + translate_algo=shot_char_limit`。旧顺序把 `shot_decompose` 放在 ASR 之后、ASR 后处理和音色选择之前，导致大模型视频分镜调用阻塞用户进入音色选择。

`voice_match` 只依赖 ASR 文本、原视频/人声分离音频和目标语言，不依赖 `task.shots`。`alignment` 也只依赖 ASR 句段和本地镜头切点检测。`task.shots` 的硬依赖首次出现在 `shot_char_limit` 翻译阶段。

## 目标

当配置启用 `shot_decompose` 时，将步骤顺序调整为：

```text
extract -> asr -> separate? -> asr_clean/asr_normalize -> voice_match -> alignment -> shot_decompose -> translate -> tts -> av_sync_audit? -> loudness_match? -> subtitle -> compose -> export
```

用户在 `voice_match` 等待时不再被 `shot_decompose` 阻塞。确认音色后，流水线继续执行 `alignment`，再在翻译前执行 `shot_decompose`。

## 非目标

- 不关闭当前默认 preset 的 `shot_decompose`。
- 不改变 `shot_char_limit` 的翻译算法和 `task.shots` 依赖。
- 不并行执行分镜与音色选择；本次只做顺序延后，避免引入后台竞态。
- 不改变用户自定义 preset 的配置内容。

## 验收

- `shot_decompose=true` 的 Omni step list 中，`voice_match` 和 `alignment` 必须早于 `shot_decompose`，`shot_decompose` 必须早于 `translate`。
- `confirm-voice` 根据真实 step list 继续时，开启分镜的任务会继续到 `alignment`，随后自然进入 `shot_decompose`。
- 详情页工作台卡片顺序与后端一致：分镜卡片显示在分段确认之后、翻译之前。
- 旧任务从 `shot_decompose` 或其后续步骤恢复时仍可使用真实 step list 校验。
- 迁移期间已经完成过 `shot_decompose` 且已有 `task.shots` 的任务，走到延后的 `shot_decompose` 时复用既有分镜，不能再次触发大模型分镜调用。
