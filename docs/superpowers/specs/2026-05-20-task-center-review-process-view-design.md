# 任务中心中文过程视图设计

- **日期**：2026-05-20
- **上位锚点**：
  - `docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`

## 背景

任务详情抽屉当前把 `task_events` 直接渲染成英文事件名和原始 JSON。审核员能看到事件存在，但很难快速判断：

- 谁提交了任务或素材。
- 当前走到原始视频、去字幕、手动上传、审核、翻译验收中的哪一步。
- 哪一步失败或被打回。
- 失败原因、文件名、文件大小、国家和翻译员等关键字段是什么。

同时右侧抽屉宽度偏窄，不适合承载过程说明、产出素材和验收项。

## 目标

1. 任务详情抽屉在桌面端占据约半屏，移动端仍全屏。
2. 审计流改为中文、可视化、说人话的过程时间线。
3. 每个事件优先展示“谁、做了什么、时间、结果、关键文件、失败原因、下一步提示”。
4. 原始 JSON 不作为主内容直接展示；解析不了的字段放到“技术详情”折叠区。
5. 只改展示层，不改任务状态机、数据库结构、权限和既有 API。

## 事件翻译规则

| event_type | 中文说明 | 重点字段 |
| --- | --- | --- |
| `created` | 创建任务 | `countries`, `translator_id`, `country` |
| `claimed` | 认领原始视频任务 | actor |
| `raw_niuma_submitted` | 已提交牛马去字幕 | `stage`, `task_id` |
| `raw_niuma_done` | 牛马去字幕完成 | `filename`, `new_size`, `media_item_id` |
| `raw_niuma_failed` | 牛马去字幕失败 | `error`, `stage`, `filename` |
| `raw_niuma_timeout` | 牛马去字幕超时 | `error`, `stage` |
| `raw_manual_uploaded` | 手动上传原始视频 | `filename`, `new_size`, `media_item_id` |
| `raw_uploaded` | 提交原始视频审核 | actor |
| `approved` | 审核通过 | actor |
| `rejected` | 打回修改 | `reason` |
| `cancelled` | 取消任务 | `reason` |
| `unblocked` | 子任务解锁 | actor |
| `submitted` | 提交翻译验收 | actor |
| `completed` | 任务完成 | actor |

未知事件显示为“系统记录”，但仍保留技术详情。

## 前端行为

- 时间线按现有接口顺序展示，不改变 `GET /tasks/api/<id>/events` 返回结构。
- 中文卡片包含状态色：通过/完成为绿色，失败/打回为红色，等待/处理中为蓝灰色，取消为警示色。
- payload 解析时优先兼容对象；如果接口返回字符串 JSON，前端尝试 `JSON.parse` 后再展示字段。
- 文件大小字段按 B、KB、MB、GB 展示。
- 技术详情默认折叠，用户需要排查时可展开查看完整 payload。

## 人物字段可视化规则

- 审核流程主内容不展示裸用户 ID，例如不显示“翻译员 ID 33”。
- `created` 等事件 payload 中出现 `translator_id` 时，主卡片展示为：
  - 标签：`翻译员`
  - 值：优先 `users.xingming`；没有中文姓名或部署无 `xingming` 列时回退 `username`；用户记录不存在时才回退 `用户 #<id>`。
- 事件操作人、任务负责人同样优先展示中文姓名，缺失时回退用户名。
- 原始 ID 保留在“技术详情”折叠区，便于排查历史数据和接口问题。
- 后端可以在事件响应上追加向后兼容的显示上下文字段（例如 `actor_display_name`、`payload_context.users`），但不能改变现有字段含义。

## 验证

1. `pytest tests/test_tasks_routes.py -q`
2. `pytest tests/test_appcore_tasks_supporting_data.py -q`
3. `python3 -m compileall appcore/tasks.py web/routes/tasks.py`
4. 手工打开 `/tasks/`，详情抽屉桌面端约半屏；审计流能看到中文步骤、失败原因和文件信息；创建任务事件显示“翻译员 / 中文姓名或用户名”，不显示“翻译员 ID”。
