# 任务中心牛马去字幕状态与跳转设计

- **日期**：2026-05-21
- **状态**：用户确认，待实施
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-20-task-center-assignment-and-niuma-automation-fix.md`
  - `docs/superpowers/specs/2026-05-20-task-center-review-process-view-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-step-review-assets-design.md`
  - `docs/superpowers/specs/2026-04-15-subtitle-removal-design.md`

## 背景

小语种翻译任务创建后，父任务会自动把原素材提交到牛马去字幕链路。当前任务中心第 2 步“提交牛马去字幕”只展示事件和技术 JSON，管理员无法直接判断：

- 牛马去字幕任务现在是排队、运行、完成、失败还是超时。
- 这次自动提交发生在什么时候。
- 从提交到现在已经过了多久。
- 应该跳到哪里继续查看去字幕任务的内部细节和后续操作。

字幕移除模块本身已经有独立任务详情页。任务中心不应重复展示字幕移除内部步骤，而应在流程步骤里提供清晰的归纳状态和跳转入口。

## 目标

1. 第 2 步“提交牛马去字幕”卡片必须展示 `字幕移除任务页` 按钮。
2. 按钮跳转到具体字幕移除详情页：`/subtitle-removal/<subtitle_task_id>`。
3. 任务中心只展示归纳状态，不重复展示字幕移除任务内部步骤。
4. 卡片展示自动提交时间、已过时间、最近更新时间、错误摘要等可视化信息。
5. 兼容历史事件：优先读取 `subtitle_task_id`，必要时兼容旧 payload 中的 `task_id`。

## 非目标

1. 不改字幕移除任务详情页内部 UI。
2. 不新增数据库表。
3. 不改变父任务、子任务或字幕移除任务状态机。
4. 不把字幕移除内部步骤（提交、轮询、下载、上传）复制到任务中心。

## 状态口径

任务中心卡片展示“归纳状态”，由字幕移除任务状态、provider 状态和任务中心事件共同归并：

| 归纳状态 | 来源条件 |
| --- | --- |
| `已提交` | 有 `raw_niuma_submitted` 事件，但暂未读取到字幕移除任务详情 |
| `排队中` | 字幕移除任务 `status` 为 `queued` 或 provider 状态为 `waiting` |
| `运行中` | 字幕移除任务 `status` 为 `running`，或 provider 状态处于运行/轮询中 |
| `已完成` | 字幕移除任务 `status` 为 `done`，或父任务已有 `raw_niuma_done` 事件 |
| `失败` | 字幕移除任务 `status` 为 `error`，或父任务已有 `raw_niuma_failed` 事件 |
| `超时` | 父任务已有 `raw_niuma_timeout` 事件 |

展示字段：

- `自动提交时间`：使用 `raw_niuma_submitted.created_at`。
- `已过时间`：前端按当前时间减自动提交时间计算，打开抽屉时刷新。
- `最近更新时间`：优先使用字幕移除任务 `last_polled_at`，其次使用任务状态更新时间。
- `字幕移除任务 ID`：展示 `subtitle_task_id`，但不让用户只靠技术详情查找。
- `错误摘要`：优先展示 `error`、`provider_emsg` 或失败事件 payload 中的错误。

## 后端设计

### 事件上下文增强

`appcore.tasks.list_task_events()` 在解析事件 payload 时：

1. 识别 `raw_niuma_submitted`、`raw_niuma_done`、`raw_niuma_failed`、`raw_niuma_timeout` 中的 `subtitle_task_id`。
2. 兼容历史 payload 中名为 `task_id` 的字幕移除任务 ID，但不能覆盖父任务自己的 `task_id` 语义。
3. 为事件追加向后兼容字段，例如：

```json
{
  "payload_context": {
    "subtitle_removal": {
      "task_id": "tcraw-5-fixed",
      "detail_url": "/subtitle-removal/tcraw-5-fixed",
      "summary_status": "running",
      "summary_label": "运行中",
      "submitted_at": "2026-05-20T23:30:24",
      "last_updated_at": "2026-05-20T23:33:10",
      "error": ""
    }
  }
}
```

已有 `payload_context.users` 保持不变；新增 `payload_context.subtitle_removal` 不改变现有字段含义。

### 自动提交事件

`appcore/task_raw_video_processing.py` 写入 `raw_niuma_submitted` 事件时继续保留 `subtitle_task_id`，并补充必要的排查字段：

- `subtitle_task_id`
- `timeout_seconds`
- `subtitle_backend: "niuma"`

失败或超时事件若已有关联 `subtitle_task_id`，继续携带该字段。

## 前端设计

`web/templates/tasks_list.html` 的时间线卡片渲染规则：

1. 当事件上下文存在 `payload_context.subtitle_removal.detail_url` 时，在该步骤卡片主内容区展示按钮：
   - 文案：`字幕移除任务页`
   - 行为：新标签页打开 `detail_url`
2. 同一卡片展示归纳状态 badge、自动提交时间、已过时间、最近更新时间、字幕移除任务 ID。
3. 错误状态展示错误摘要；完整 payload 仍保留在“技术详情”折叠区。
4. `raw_niuma_done`、`raw_niuma_failed`、`raw_niuma_timeout` 若带同一个 `subtitle_task_id`，同样展示 `字幕移除任务页` 按钮，方便从后续结果或失败节点回到具体任务。

## 旧数据兼容

- 历史 `raw_niuma_submitted` 事件只要 payload 中有 `subtitle_task_id`，即可生成跳转按钮。
- 若 payload 缺少字幕移除任务 ID，卡片仍展示归纳状态和技术详情，但不展示跳转按钮。
- 若字幕移除任务记录已删除或无法读取，按钮仍可按 ID 生成；归纳状态显示为 `已提交` 或事件本身状态，避免因为关联详情缺失导致任务中心页面报错。

## 验证

1. `pytest tests/test_tasks_routes.py tests/test_task_raw_video_processing.py -q`
2. `python3 -m compileall appcore/tasks.py appcore/task_raw_video_processing.py web/routes/tasks.py`
3. 手工打开 `/tasks/`：
   - 第 2 步能看到 `字幕移除任务页` 按钮。
   - 按钮跳转到 `/subtitle-removal/<subtitle_task_id>`。
   - 卡片显示归纳状态、自动提交时间、已过时间、最近更新时间和错误摘要。
   - 卡片不展示字幕移除内部步骤。
