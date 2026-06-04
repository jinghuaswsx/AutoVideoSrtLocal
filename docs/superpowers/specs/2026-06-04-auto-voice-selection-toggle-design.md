# 大模型自动音色选择开关设计

## 背景

全能视频翻译在对话 A/B 音色匹配链路中复用了 `voice_match_ab`，当前逻辑会在 AI 排名后固定进入人工确认，导致默认自动化流程被阻塞。业务期望保留人工确认能力，但默认仍由大模型自动选择排名第一的音色并继续后续步骤。

## 配置

在 `plugin_config` 中新增布尔字段：

```json
{
  "auto_voice_selection": true
}
```

默认值为 `true`。缺失字段由 `validate_plugin_config()` 补齐，接受布尔值、`0/1`、`true/false` 字符串。

## UI

全能视频翻译创建弹窗新增 `大模型自动音色选择` 开关，位置在 `句级TTS响度校准` 左边。开关默认开启，并随提交写入 `plugin_config.auto_voice_selection`。

任务详情页顶部同样展示 `大模型自动音色选择` 和 `句级TTS响度校准`。点击“强制重新开始”时，前端必须把当前两个开关值带入 restart 请求；后端在重置任务状态前将请求值合并并校验到 `plugin_config`，确保新一轮 runner 读取的是用户当前看到的开关状态，而不是上一轮旧快照。

详情页顶部工具栏也必须暴露同一个开关。`/omni-translate/<task_id>` 与 `/dialogue-translate/<task_id>` 都复用 `_translate_detail_shell.html`，因此开关渲染条件不能只绑定 Omni API；对话式详情页应使用 `/api/dialogue-translate/<task_id>/auto-voice-selection` 持久化同一字段。开关只负责更新 `plugin_config.auto_voice_selection`，不触发重跑，不改变当前步骤状态。

## 运行时

`DialogueTranslateRunner._step_voice_match_ab()` 继续生成 A/B 候选、相似度、语速参考和大模型排名。

- `auto_voice_selection == true`：为 A/B 各选择 AI 排名第一的候选，写入 `selected_voice_by_speaker`，将 `voice_match_ab` 标记为 `done`，清空 `current_review_step`，流程继续。
- `auto_voice_selection == false`：不写入选定音色，将 `voice_match_ab` 标记为 `waiting`，`current_review_step = "voice_match_ab"`，等待人工确认。
- 任一必要说话人没有可选候选时，即使开关开启，也回退为 `waiting`，避免自动选择不完整。

## 验证

- 配置校验默认补 `auto_voice_selection = true`。
- 创建弹窗展示并提交该开关，默认 checked。
- 详情页强制重新开始请求携带顶部开关值，并在 restart reset 中写入 `plugin_config`。
- Omni 与 Dialogue 详情页右上角展示该开关，并通过各自 API 保存到任务 `plugin_config`。
- `voice_match_ab` 在开关开启时自动选择排名第一并继续。
- `voice_match_ab` 在开关关闭时保持等待人工选择。
