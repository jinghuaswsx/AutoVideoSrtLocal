# 任务中心取消归档与每小时自动归档设计

- **日期**：2026-06-08
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-28-task-center-archive-tab-design.md`
  - `docs/superpowers/specs/2026-06-01-task-center-auto-archive-design.md`
  - `docs/superpowers/specs/2026-06-06-task-center-pending-push-filter-design.md`
  - `docs/superpowers/specs/2026-06-06-task-center-status-taxonomy-cleanup-design.md`

## 背景

任务中心已支持人工归档和自动归档。归档是列表可见性属性，通过 `tasks.archived_at` / `tasks.archived_by` 控制，不改变 `tasks.status`。旧归档规格把“取消归档/恢复”列为不做范围；现在需要在已归档任务上提供管理员可见的“取消归档”按钮，让误归档或需要继续处理的任务重新回到正常任务列表。

同时，任务中心自动归档从每天 06:00 调整为每小时执行一次，以更快清理已经完成且已推送/跳过推送的任务。

## 目标

1. 已归档任务在操作区显示“取消归档”按钮，仅管理员可用。
2. 点击“取消归档”后清空 `tasks.archived_at` 和 `tasks.archived_by`，刷新当前列表。
3. 取消归档不改变任务原有 `status`、`completed_at`、`cancelled_at`、推送日志或素材状态。
4. 取消归档写入 `task_events(event_type='unarchived')`，记录操作人和原归档信息。
5. 任务重新进入哪个正常列表由既有状态和派生规则决定：
   - `assigned` / `review` 子任务若满足待推送 readiness，进入 `pending_push` / 待推送。
   - `done` / `all_done` 已完成任务回到已完成，不自动回退到待推送。
6. 自动归档 APScheduler 频率改为每小时。

## 后端设计

在 `appcore.tasks` 中新增：

- `TASK_UNARCHIVED_EVENT = "unarchived"`
- `unarchive_task(task_id, actor_user_id, is_admin) -> bool`

语义：

- 仅管理员可取消归档。
- 任务不存在时报 `StateError("task not found")`。
- 未归档任务重复取消归档不报错，返回 `False`，保持幂等。
- 首次取消归档执行：

```sql
UPDATE tasks
SET archived_at=NULL, archived_by=NULL, updated_at=NOW()
WHERE id=%s AND archived_at IS NOT NULL
```

并写入 `task_events`：

```json
{
  "previous_archived_at": "2026-06-08T06:00:00",
  "previous_archived_by": 7,
  "task_status": "review"
}
```

新增路由：

```text
POST /tasks/api/<task_id>/unarchive
```

路由必须使用 `@login_required` + `@admin_required`，调用 service 后返回 `{"ok": true}`。

## 前端设计

`tasks_list.html` 已归档行操作区从只显示禁用“已归档”改为：

- “取消归档”：管理员可见，调用 `POST /tasks/api/<id>/unarchive`。

取消归档成功后调用现有行刷新逻辑。由于当前处于已归档 TAB 时该任务不再满足 `archived=True` 过滤，刷新后应从当前列表消失；在全部/进行中/待推送/已完成等正常列表里，任务按既有状态和派生显示。

## 调度设计

`appcore.task_center_auto_archive_scheduler.register()` 使用 APScheduler interval：

```python
scheduled_tasks.add_controlled_job(..., "interval", hours=1, ...)
```

`appcore.scheduled_tasks` 中 `task_center_auto_archive.schedule` 改为“每小时”，描述同步改为每小时扫描。

## 不做范围

1. 不新增 `status='archived'`。
2. 不把已完成任务取消归档后自动回退到 `review` / 待推送。
3. 不清理或回滚 `media_items.pushed_at` / `skip_push` / `media_push_logs`。
4. 不新增批量取消归档。
5. 不连接 Windows 本机 MySQL 做验证。

## 验证

1. `tests/test_appcore_tasks_supporting_data.py`
   - 已归档任务取消归档清字段并写 `unarchived` 事件。
   - 未归档任务重复取消归档幂等返回 `False`。
2. `tests/test_tasks_routes.py`
   - 模板包含“取消归档”按钮和 `/tasks/api/<id>/unarchive` 请求。
   - 取消归档路由调用 service 并返回成功。
3. `tests/test_task_center_auto_archive_scheduler.py`
   - 自动归档调度注册为每小时。
4. `tests/test_appcore_scheduled_tasks.py`
   - 定时任务定义显示“每小时”，描述引用本规格。
5. `python3 -m py_compile appcore/tasks.py web/routes/tasks.py appcore/task_center_auto_archive_scheduler.py appcore/scheduled_tasks.py`
