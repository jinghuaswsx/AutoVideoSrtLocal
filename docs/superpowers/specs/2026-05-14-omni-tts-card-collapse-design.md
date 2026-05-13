# Omni TTS Card Collapse Design

日期：2026-05-14

## 背景

Omni 任务详情页的「语音生成」步骤会同时展示整段配音按钮、配音段落、句级时长收敛过程和多轮 TTS 诊断。对长视频任务来说，这块日志会把页面撑得很长，用户在任务 `d8aba350-231a-45f4-909a-fb4ed77b6d75` 上明确要求在卡片右上角增加「展开/收拢」按钮，便于快速折叠语音生成日志。

## 目标

1. 在 Omni / shared translate detail 的「语音生成」卡片右上角增加一个小按钮。
2. 按钮控制该卡片下方日志区域展开或收拢，范围包括 `preview-tts` 和 `ttsDurationLog`。
3. 默认保持展开，避免改变首次进入页面时的信息可见性。
4. 折叠状态按任务 ID 保存在浏览器本地，刷新后保持用户选择。
5. 按钮文案和 `aria-expanded` 必须随状态更新，键盘和读屏可识别。

## 非目标

1. 不改变 TTS 生成、重写、调速、拼接、字幕或合成流程。
2. 不改变 socket 事件、任务状态字段或数据库结构。
3. 不重做任务详情页整体布局。
4. 不给每个配音段落单独增加折叠按钮。

## 前端设计

`web/templates/_task_workbench.html` 中的 `#step-tts` 继续作为语音生成步骤容器。按钮放在 `.step-name-row` 右侧、恢复按钮之前或附近，视觉上对应卡片右上角。

折叠行为由 `web/templates/_task_workbench_scripts.html` 维护：

- 查找 `#step-tts`、`#preview-tts`、`#ttsDurationLog` 和按钮。
- localStorage key 使用任务 ID 隔离，例如 `ttsCardCollapsed:<taskId>`。
- 折叠时给 `#step-tts` 加 `tts-card-collapsed`，并隐藏 `#preview-tts`、`#ttsDurationLog`。
- 展开时移除折叠 class，恢复 `renderStepPreviews()` 和 `renderTtsDurationLog()` 原本负责的内容可见性。
- 轮询或 socket 刷新重新渲染日志时，不应覆盖用户的折叠选择。

样式放在 `web/templates/_task_workbench_styles.html`：

- 按钮使用小号描边按钮，遵循现有 Ocean Blue token。
- 折叠/展开只做颜色或轻量 transform 变化，不引入复杂动画。
- 移动端保持按钮不撑破标题行。

## 验证策略

1. `tests/test_translate_detail_shell_templates.py` 增加静态断言，确认按钮、折叠 class、localStorage key、`aria-expanded` 更新逻辑存在。
2. 运行相关模板测试。
3. 运行 Omni 路由测试，确认详情页仍能渲染。
4. 起 dev server 后验证未登录 `/omni-translate/<id>` 返回 302，而不是 500。
