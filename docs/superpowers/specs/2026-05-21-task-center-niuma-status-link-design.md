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
6. 当字幕移除任务已有结果视频时，第 2 步卡片直接展示左右双列视频对比：
   - 左列：原始带字幕英文视频。
   - 右列：字幕移除后的结果视频。
7. 任务负责人可以直接点击视频播放按钮检查字幕移除效果；超级管理员在任务中心查看全量任务时也能看到同样过程和对比视频。

## 非目标

1. 不改字幕移除任务详情页内部 UI。
2. 不新增数据库表。
3. 不改变父任务、子任务或字幕移除任务状态机。
4. 不把字幕移除内部步骤（提交、轮询、下载、上传）复制到任务中心。
5. 不在任务中心提供重新提交、删除、下载等字幕移除详情页已有操作；这些操作仍通过 `字幕移除任务页` 进入。

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
      "error": "",
      "comparison": {
        "source_video_url": "/api/subtitle-removal/tcraw-5-fixed/artifact/source-video",
        "result_video_url": "/api/subtitle-removal/tcraw-5-fixed/artifact/result",
        "source_label": "原始英文视频",
        "result_label": "字幕移除结果"
      }
    }
  }
}
```

已有 `payload_context.users` 保持不变；新增 `payload_context.subtitle_removal` 不改变现有字段含义。

### 对比视频来源

当能读取到关联的字幕移除任务时，事件上下文按以下规则生成对比信息：

1. `source_video_url` 使用字幕移除源视频 artifact 路由：`/api/subtitle-removal/<subtitle_task_id>/artifact/source-video`。
2. `result_video_url` 仅在字幕移除任务已有结果时生成，优先使用 result artifact 路由：`/api/subtitle-removal/<subtitle_task_id>/artifact/result`。
3. 只有 `result_video_url` 存在时，任务中心才展示双列视频对比；任务未完成时只展示归纳状态和 `字幕移除任务页` 按钮。
4. 如果源视频或结果视频 artifact 不可用，卡片不报错，保留状态、错误摘要和跳转按钮。

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
3. 当 `payload_context.subtitle_removal.comparison.result_video_url` 存在时，在该步骤卡片中部展示双列对比播放器：
   - 左列标题：`原始英文视频`
   - 右列标题：`字幕移除结果`
   - 两列都使用 `<video controls preload="metadata">`，允许负责人直接点击播放。
   - 桌面端两列并排；窄屏自动上下堆叠，避免播放器挤压或重叠。
4. 错误状态展示错误摘要；完整 payload 仍保留在“技术详情”折叠区。
5. `raw_niuma_done`、`raw_niuma_failed`、`raw_niuma_timeout` 若带同一个 `subtitle_task_id`，同样展示 `字幕移除任务页` 按钮，方便从后续结果或失败节点回到具体任务。

## 权限与可见性

- `字幕移除任务页` 和对比视频 URL 复用现有已登录字幕移除 artifact 路由，不新增无鉴权下载入口。
- 任务负责人在“我的任务”中查看父任务时，可以看到归纳状态、跳转按钮和已生成的对比视频。
- 超级管理员在“全部任务”中查看任意父任务时，可以看到完整过程、跳转按钮和已生成的对比视频。
- 前端只使用后端返回的 URL，不拼接本地路径。

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
   - 有字幕移除结果时，第 2 步中部展示左原始英文视频、右字幕移除结果视频，两个播放器都能点击播放。
   - 超级管理员在“全部任务”中可以看到同样过程和对比视频。
   - 卡片不展示字幕移除内部步骤。
