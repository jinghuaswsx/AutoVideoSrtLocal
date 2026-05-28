# 任务中心紧急任务标记与置顶设计

- **日期**：2026-05-28
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-overview-tab-sort-design.md`
  - `docs/superpowers/specs/2026-05-28-task-center-cancel-action-and-history-design.md`

## 背景

任务中心已经支持管理员查看全部任务、普通运营查看自己的任务，并按创建时间倒序展示。实际排班中，管理员需要临时提高某些任务的处理优先级；运营人员进入任务中心时，紧急任务必须始终排在列表顶部，避免被普通任务淹没。

## 目标

1. 管理员可以在任务中心每个任务的操作区将任务标记为紧急，或取消紧急标记。
2. 任务列表筛选区新增紧急状态筛选：全部、紧急、非紧急。
3. 所有任务列表默认排序先按紧急状态降序，再按创建时间倒序，再按 id 倒序。
4. 运营人员登录后在“我的任务”列表中也遵循紧急置顶排序。
5. 列表和详情中显示紧急状态，便于管理员和运营人员识别。

## 数据模型

在 `tasks` 表新增字段：

```sql
is_urgent TINYINT(1) NOT NULL DEFAULT 0
```

新增索引用于列表排序和筛选：

```sql
KEY idx_urgent_created (is_urgent, created_at, id)
```

字段语义：

- `0`：非紧急任务，默认值。
- `1`：紧急任务。
- 父任务和子任务各自独立标记，不做父子级联。

## 后端设计

### 列表查询

`GET /tasks/api/list` 新增 query 参数 `urgency`：

| 参数值 | 行为 |
| --- | --- |
| `all` 或空 | 不按紧急状态过滤 |
| `urgent` | 只显示 `is_urgent=1` |
| `normal` | 只显示 `is_urgent=0` |

`appcore.tasks.list_task_center_items()` 增加 `urgency` 参数，并将排序改为：

```sql
ORDER BY t.is_urgent DESC, t.created_at DESC, t.id DESC
```

返回的每个 item 增加：

```json
{"is_urgent": true}
```

### 管理员标记接口

新增接口：

- `POST /tasks/api/<id>/urgency`
- 权限：`@login_required + @admin_required`
- 请求体：

```json
{"is_urgent": true}
```

规则：

1. `is_urgent` 必须是 boolean。
2. 任务不存在返回 404。
3. 标记成功后写 `task_events`：
   - `event_type='urgent_marked'`
   - payload：`{"is_urgent": true, "previous_is_urgent": false}`
4. 取消紧急写同一个事件类型，payload 中 `is_urgent=false`。
5. 重复设置为当前值允许成功返回，但不重复写事件。

## 前端设计

### 筛选区

在任务类型和负责人筛选旁增加紧急筛选：

```text
全部紧急状态 | 紧急 | 非紧急
```

筛选变化后重置页码为 1，并重新请求列表。

### 列表行

紧急任务在任务名旁显示“紧急”徽标。操作区对管理员显示：

- 非紧急任务：`标记紧急`
- 紧急任务：`取消紧急`

普通运营人员不显示标记按钮，但能看到“紧急”徽标。

### 详情抽屉

详情标题区域显示紧急状态；管理员在详情操作区同样可以切换紧急状态。切换成功后刷新列表和当前详情。

## 不做范围

1. 不做父子任务紧急状态级联。
2. 不新增批量标记紧急。
3. 不改变任务状态机、审核、取消、提交和 readiness 门禁。
4. 不改变站内通知和员工产能统计。
5. 不改变现有 `created_at DESC, id DESC` 的同优先级排序规则。

## 验证

1. `tests/test_appcore_tasks_supporting_data.py`
   - 列表 SQL 包含 `ORDER BY t.is_urgent DESC, t.created_at DESC, t.id DESC`。
   - `urgency=urgent` 过滤 `t.is_urgent=1`。
   - `urgency=normal` 过滤 `t.is_urgent=0`。
   - 返回 item 包含 boolean `is_urgent`。
   - 标记接口服务层写 `tasks.is_urgent` 和 `task_events`。
2. `tests/test_tasks_routes.py`
   - `/tasks/api/list?urgency=urgent|normal` 正确透传。
   - 非法 `urgency` 返回 400。
   - 管理员紧急标记接口成功，普通用户返回 403。
   - 模板包含紧急筛选、紧急徽标和管理员操作按钮。
3. `tests/test_db_migration_tasks_tables.py`
   - 初始建表 DDL 包含 `is_urgent`。
   - 新增 migration 包含 `ALTER TABLE tasks` 和 `idx_urgent_created`。
4. `python -m compileall appcore/tasks.py web/routes/tasks.py`。
