# 人声分离卡片位置调整设计

## Goal

把多语种 / 全能视频翻译详情页中的「人声分离」卡片放到用户截图 2 红框标注的位置：紧跟「音频提取」卡片之后，并位于「选择 TTS 音色」卡片之前。

## Context

当前共享详情页由 `web/templates/_translate_detail_shell.html` 组合：

- `_voice_selector_multi.html` 先渲染「选择 TTS 音色」
- `_task_workbench.html` 后渲染包含 `#step-extract` 与 `#step-separate` 的处理步骤
- `_separation_card.html` 再用 JS 把 `#step-separate` 移到 `#voice-selector-multi` 前
- `web/static/voice_selector_multi.js` 会在加载后把 `#voice-selector-multi` 移进 `#pipelineCard .steps`，放到 `#step-asr` 之后

这导致「人声分离」在视觉上浮到音色选择器正上方，但没有贴着「音频提取」卡片，和用户要的截图 2 位置不一致。

## Desired Layout

多语种 / 全能视频翻译详情页的相关视觉顺序固定为：

1. 语音识别 / 原文标准化结果区域
2. 音频提取
3. 人声分离
4. 选择 TTS 音色
5. 后续分段、翻译、TTS、字幕、合成、导出步骤

「人声分离」仍复用 `_task_workbench.html` 中的 `#step-separate` 步骤卡外壳，不新建独立卡片，不改变后端步骤顺序、接口或 artifact 名称。

## Implementation

在 `_translate_detail_shell.html` 中把 `#step-separate` 的 flex order 调整为与 `#step-extract` 同组。由于 `_task_workbench.html` 中 `#step-extract` 的源码顺序早于 `#step-separate`，两者同组后视觉顺序自然变成「音频提取 → 人声分离」。

删除 `_separation_card.html` 中把 `#step-separate` 直接插到 `#voice-selector-multi` 前的 DOM 重排逻辑，避免它脱离 `#pipelineCard .steps`。

## Validation

- 模板测试应确认 shell 中包含 `#pipelineCard .steps > #step-separate { order: -1; }`。
- 模板测试应确认 `_separation_card.html` 不再直接把 `#step-separate` 插到 `#voice-selector-multi` 前。
- 聚焦验证命令：
  `pytest tests/test_translate_detail_shell_templates.py -q`
- 详情页相关回归命令：
  `pytest tests/test_multi_translate_routes.py tests/test_omni_translate_routes.py tests/test_runtime_multi_asr_normalize.py -q`
