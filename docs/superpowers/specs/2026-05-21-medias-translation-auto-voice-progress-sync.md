# 素材管理翻译任务 AI 自动选音与进度同步修正

- 日期：2026-05-21
- 上位锚点：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-04-22-medias-translation-orchestration-design.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`

## 背景

4 月的素材管理一键翻译编排设计要求视频翻译子任务统一停在“选择声音”步骤，父任务显示 `awaiting_voice`，等待人工确认后继续。当前视频翻译详情页已经升级为 AI 音色排名完成后自动确认 top1 音色并继续执行，但素材管理“翻译”按钮创建的视频翻译任务是在后台创建的，不会打开详情页，因此仍沿用旧的人工选音状态，导致任务进度看起来固定卡在选语音。

## 目标

1. 素材管理翻译父任务同步子视频任务状态时，若子任务处于 `voice_match=waiting` 且 AI 音色排名已完成，应后台确认 AI top1 音色并从 voice_match 后续步骤继续。
2. AI 排名仍在 `running/queued` 时，父任务进度显示为正常运行中，不标记为卡住或等待人工选音。
3. 仅当自动选音关闭、AI 排名失败、或没有可用 AI top1 音色时，才显示人工选音入口。
4. 翻译任务管理页文案应按实际状态表达：自动选音启用时显示“自动选择并继续”，人工入口只代表异常或关闭自动选音后的兜底。

## 实现边界

- 不新增数据库表或迁移。
- 不改变视频翻译 runner 的 `voice_match` 步骤产物格式。
- 不改变图片、文案、封面翻译的调度规则。
- 不访问 Windows 本机 MySQL；验证使用 no-db / fake-db 聚焦测试。

## 验收

- bulk translate 父任务同步到 AI top1 时会写入 `selected_voice_id`、把子任务 `voice_match` 标记为 `done`，并恢复子 runner。
- 父任务计划项不再固定进入 `awaiting_voice`，进度页不再把自动选音阶段渲染成“卡在选择声音”。
- 旧的人工选音路径在自动选音关闭时仍可用。
