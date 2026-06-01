# 任务中心自动归档设计

- **日期**：2026-06-01
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-28-task-center-archive-tab-design.md`

## 背景

任务中心已支持人工归档，归档通过 `tasks.archived_at` / `tasks.archived_by` 控制列表可见性，不改变任务原始 `status`。现在需要让系统每天自动清理已经完成且对应素材已推送成功的任务，减少“已完成”列表里的长期存量，同时保留原任务状态、推送日志和系统归档时间。

## 目标

1. 每天北京时间 06:00 执行一次任务中心自动归档。
2. 子任务达到 `status='done'` 且对应小语种素材 `media_items.pushed_at IS NOT NULL` 时，系统自动归档该子任务。
3. 父任务只在 `status='all_done'` 且其下已完成子任务对应素材全部推送成功时自动归档；`raw_done` 不自动归档。
4. 自动归档不改变 `tasks.status`、`completed_at`、`cancelled_at`、`media_push_logs` 等已有状态和日志。
5. 自动归档写入 `task_events`，明确记录系统执行时间、触发原因、任务状态、素材 id 和推送时间。

## 数据模型

沿用既有归档字段：

```sql
tasks.archived_at DATETIME DEFAULT NULL
tasks.archived_by INT DEFAULT NULL
```

自动归档不新增表结构。系统归档时：

- `archived_at=NOW()`
- `archived_by=NULL`
- `task_events.event_type='auto_archived'`
- `task_events.actor_user_id=NULL`

`payload_json` 至少包含：

```json
{
  "source": "task_center_auto_archive",
  "reason": "child_done_and_material_pushed",
  "task_status": "done",
  "media_item_id": 123,
  "pushed_at": "2026-06-01 05:33:00"
}
```

父任务 payload 使用 `reason='parent_all_done_and_children_pushed'`，并记录 `child_task_ids`、`child_media_item_ids` 和 `child_count`。

## 后端设计

在 `appcore.tasks` 中新增：

- `TASK_AUTO_ARCHIVED_EVENT = "auto_archived"`
- `auto_archive_completed_pushed_tasks(limit: int | None = None) -> dict`

扫描规则：

1. 子任务候选：
   - `tasks.parent_task_id IS NOT NULL`
   - `tasks.status='done'`
   - `tasks.archived_at IS NULL`
   - 关联最新的未删除、同 `task_id` 的 `media_items`
   - `media_items.pushed_at IS NOT NULL`
2. 父任务候选：
   - `tasks.parent_task_id IS NULL`
   - `tasks.status='all_done'`
   - `tasks.archived_at IS NULL`
   - 至少存在一个 `status='done'` 子任务
   - 不存在“已完成但未推送成功”的子任务

更新和事件写入放在数据库事务中，使用 `FOR UPDATE` 锁住候选任务；重复执行必须幂等，已归档任务直接跳过。

## 调度设计

新增 `appcore/task_center_auto_archive_scheduler.py`：

- `TASK_CODE = "task_center_auto_archive"`
- `tick_once(limit: int | None = None)` 调用 `tasks.auto_archive_completed_pushed_tasks`
- 通过 `scheduled_tasks.start_run` / `finish_run` 记录执行结果
- `register(scheduler)` 使用 APScheduler cron 触发器：`hour=6, minute=0`

在 `appcore/scheduler.py` 注册该调度；在 `appcore/scheduled_tasks.py` 登记任务定义，显示到 Web 后台“定时任务”模块。

## 不做范围

1. 不改变人工归档入口和已归档 TAB 逻辑。
2. 不新增取消归档。
3. 不自动归档 `raw_done` 父任务。
4. 不改推送成功判定来源；仍以 `media_items.pushed_at` 为准。
5. 不连接 Windows 本机 MySQL 做验证。

## 验证

1. `tests/test_appcore_tasks_supporting_data.py`
   - 子任务已完成且素材已推送时写 `archived_at` 和 `auto_archived` 事件。
   - 子任务已完成但素材未推送时不归档。
   - `raw_done` 父任务不归档。
   - `all_done` 父任务在已完成子任务全部推送后归档。
2. `tests/test_task_center_auto_archive_scheduler.py`
   - 调度注册为每天 06:00。
   - `tick_once` 记录 scheduled run 成功/失败。
3. `tests/test_appcore_scheduled_tasks.py`
   - 定时任务定义包含 `task_center_auto_archive`。
4. `python -m compileall appcore/tasks.py appcore/task_center_auto_archive_scheduler.py appcore/scheduler.py appcore/scheduled_tasks.py`
