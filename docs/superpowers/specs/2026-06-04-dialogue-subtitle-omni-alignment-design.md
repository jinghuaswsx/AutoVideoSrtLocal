# 对话式翻译字幕调节对齐全能视频翻译设计

**日期**: 2026-06-04  
**状态**: 已确认，待实现

## 背景

对话式视频翻译复用 Omni V2 的翻译、TTS、字幕、合成和导出流程，但详情页的 A/B 音色确认面板当前没有挂入全能视频翻译的字幕调节能力。用户在对话式流程中需要和全能视频翻译一致地调节字幕位置、字体和大小，并且调节时的预览位置必须与最终硬字幕视频中的实际位置一致。

## 锚点

- `docs/superpowers/specs/2026-04-16-subtitle-config-design.md`：字幕字体、字号、位置配置，以及 2026-05-08 所见即所得坐标契约修订。
- `docs/superpowers/plans/2026-05-28-dialogue-video-translation.md`：对话式翻译应复用 Omni V2 的字幕、合成和导出流程。
- `web/templates/_voice_selector_multi.html` 与 `web/static/voice_selector_multi.js`：全能视频翻译当前字幕调节 UI、预览和提交路径。

## 需求

1. 对话式翻译详情页必须提供与全能视频翻译一致的字幕调节能力：
   - 可选择字幕位置。
   - 可选择字幕字体。
   - 可调节字幕大小。
2. 用户确认 A/B 音色继续流程时，字幕设置必须一并保存到任务状态。
3. 最终生成视频中的字幕位置必须与调节时预览位置一致。坐标继续使用 `subtitle_position_y`，含义为“字幕渲染外框底边距顶百分比”，预览和合成都不能引入第二套坐标。
4. 对话式的 A/B 音色选择逻辑保持不变；本次只扩展字幕设置与保存。

## 设计

对话式详情页在 `dialogueVoicePanel` 内增加字幕样式区块，复用全能视频翻译已有的控件结构：字体下拉、字号按钮、位置滑块、真实源视频预览和可拖动字幕块。前端脚本从 `/api/dialogue-translate/<task_id>/subtitle-preview` 读取初始字体、字号、位置和源视频 URL，用户调整后实时同步预览。

对话式确认接口 `/api/dialogue-translate/<task_id>/confirm-voices` 接收 `subtitle_font`、`subtitle_size`、`subtitle_position_y`、`subtitle_position`。后端用全能翻译相同的 `normalize_confirm_voice_payload()` 规则规范化字幕字段，但不复用单音色字段；A/B 音色仍由 `selected_voice_by_speaker` 负责。保存后同步 `task_state`，让后续 subtitle/compose/export 使用同一套字幕参数。

预览位置遵循已有所见即所得契约：字幕预览块使用 `top: subtitle_position_y * 100%` 和 `transform: translateY(-100%)`，表示被定位的是字幕外框底边。拖拽和滑块写回的仍是同一个 `subtitle_position_y` 数值，最终 `pipeline/compose.py` 通过 `MarginV = video_height * (1 - subtitle_position_y)` 生成硬字幕。

## 改动范围

- 修改 `web/templates/dialogue_translate_detail.html`：新增字幕样式与预览 UI。
- 修改 `web/static/js/dialogue_translate_detail.js`：加载字幕预览 payload，处理字体/字号/位置调节，确认 A/B 时提交字幕参数。
- 修改 `web/routes/dialogue_translate.py`：`confirm_voices` 保存字幕参数并同步内存态。
- 修改 `tests/test_dialogue_translate_routes.py`：覆盖详情页渲染字幕控件、确认 A/B 时持久化字幕参数。

## 不在本次范围

- 不改全能视频翻译、日语翻译、多语种翻译和英语重配音的现有行为。
- 不增加字幕颜色、描边、背景框或动画。
- 不改 compose/capcut 的现有坐标换算。

## 验证

- `pytest tests/test_dialogue_translate_routes.py -q`
- `pytest tests/test_translate_detail_protocol.py -q`
- `node --check web/static/js/dialogue_translate_detail.js`

