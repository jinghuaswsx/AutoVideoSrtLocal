# 任务中心已归档 TAB 设计

- **日期**：2026-05-28
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-overview-tab-sort-design.md`
  - `web/templates/CLAUDE.md`
  - `web/static/CLAUDE.md`

## 背景

任务中心当前状态子 TAB 包含任务总览、待处理任务、待审核任务、已完成任务。已完成任务长期留在默认总览和已完成列表里，会干扰日常处理视图。用户需要把已完成任务手动归档：归档后不再出现在任务总览、待处理、待审核、已完成，仅出现在新的已归档 TAB。

## 目标

1. 任务中心状态子 TAB 新增“已归档”。
2. 已完成任务的操作区新增“归档”按钮。
3. 点击归档后，任务持久标记为归档，并刷新当前列表。
4. 未归档列表默认隐藏已归档任务；已归档 TAB 只显示已归档任务。
5. 归档不改变任务原有 `status`，详情、审计和统计仍能看到真实完成状态。

## 数据模型

在 `tasks` 表新增两个可空字段：

```sql
archived_at DATETIME DEFAULT NULL
archived_by INT DEFAULT NULL
```

字段语义：

- `archived_at IS NULL`：未归档，出现在默认任务列表。
- `archived_at IS NOT NULL`：已归档，只出现在已归档 TAB。
- `archived_by`：触发归档的用户 id，用于追责。

归档是列表可见性属性，不是状态机状态，因此不新增 `status='archived'`。

## 后端设计

`appcore.tasks.list_task_center_items()` 新增归档过滤参数：

- `archived=False`：追加 `t.archived_at IS NULL`。
- `archived=True`：追加 `t.archived_at IS NOT NULL`。
- `archived=None`：不追加归档过滤，仅用于 `/tasks/detail/<id>` 精确深链取数，避免已归档 TAB 行跳详情后找不到任务。

状态 bucket 语义：

| 子 TAB | bucket | 归档过滤 | 状态过滤 |
| --- | --- | --- | --- |
| 任务总览 | `all` | 未归档 | 无 |
| 待处理任务 | `todo` | 未归档 | `raw_in_progress` / `assigned` |
| 待审核任务 | `review` | 未归档 | `raw_review` / `review` |
| 已完成任务 | `done` | 未归档 | `raw_done` / `all_done` / `done` |
| 已归档 | `archived` | 已归档 | 无 |

新增 `archive_task(task_id, actor_user_id, is_admin)`：

- 仅管理员可归档。
- 仅允许 `raw_done` / `all_done` / `done`。
- 已归档任务重复归档不报错，保持幂等。
- 首次归档写 `archived_at=NOW(), archived_by=<actor>`，并写 `task_events(event_type='archived')`。

新增路由：

```text
POST /tasks/api/<task_id>/archive
```

路由加 `@login_required` + `@admin_required`，返回 `{"ok": true}`。

详情深链 `/tasks/detail/<id>` 的前端精确取数会带 `include_archived=1`，路由仅在同时存在 `task_id` 时把 `archived=None` 传给服务层；普通列表请求仍默认隐藏归档任务。

## 前端设计

`tasks_list.html` 的状态子 TAB 追加：

```text
已归档
```

完成态行的操作区展示两个按钮：

- `查看结果`：进入详情页。
- `归档`：管理员可见，调用 `POST /tasks/api/<id>/archive`。

归档成功后刷新当前列表。如果当前在“已完成任务”，该行消失；如果当前在“已归档”，刷新后留在已归档列表中。

## 不做范围

1. 不新增“取消归档/恢复”。
2. 不改已有任务状态机和完成统计。
3. 不改待派单素材 TAB。
4. 不批量归档历史任务。

## 验证

1. `tests/test_appcore_tasks_supporting_data.py`
   - 默认列表追加 `t.archived_at IS NULL`。
   - `archived=True` 列表追加 `t.archived_at IS NOT NULL`。
   - 归档已完成任务写字段和 `task_events`。
2. `tests/test_tasks_routes.py`
   - 模板包含“已归档” TAB 和 `bucket=archived`。
   - 已完成任务操作区包含归档按钮与 `/tasks/api/<id>/archive` 请求。
   - `/tasks/api/list?bucket=archived` 将 `archived=True` 传入服务层。
   - `/tasks/api/list?task_id=<id>&include_archived=1` 将 `archived=None` 传入服务层。
   - 归档路由调用服务层并返回成功。
3. `python -m compileall appcore/tasks.py web/routes/tasks.py`
