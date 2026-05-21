# 任务中心任务总览子 TAB 与创建时间排序设计

- **日期**：2026-05-21
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `web/templates/CLAUDE.md`
  - `web/static/CLAUDE.md`

## 背景

任务中心 `/tasks/` 当前把“任务总览”作为页面标题区域，把“待处理任务 / 待审核任务 / 已完成任务”作为状态筛选按钮。用户需要“任务总览”本身成为与这三个状态筛选平行的子 TAB，并作为第一个默认 TAB。

现有列表默认选中 `todo`，只加载待处理任务；列表时间列展示更新时间，服务层按 `id DESC` 排序。用户需要默认加载所有任务，并按任务创建时间倒序展示，新任务在前、老任务在后。

## 目标

1. “任务总览”成为状态子 TAB 的第一个选项，后面依次是“待处理任务 / 待审核任务 / 已完成任务”。
2. 页面默认选中“任务总览”，默认请求加载所有任务，不带待处理状态过滤。
3. “待处理任务 / 待审核任务 / 已完成任务”继续作为状态筛选。
4. 列表按 `tasks.created_at DESC, tasks.id DESC` 排序。
5. 表格时间列展示“创建时间”，避免用户误以为是更新时间排序。

## 交互设计

任务中心顶部大 TAB 仍保留：

```text
任务总览 | 待派单素材
```

当处于“任务总览”大 TAB 时，下方展示状态子 TAB：

```text
任务总览 | 待处理任务 | 待审核任务 | 已完成任务
```

默认状态：

- `TC_CURRENT_BUCKET = "all"`
- “任务总览”子 TAB active
- API 请求参数传 `bucket=all`

子 TAB 语义：

| 子 TAB | bucket | 行为 |
| --- | --- | --- |
| 任务总览 | `all` | 不加状态过滤，加载当前权限范围内所有任务 |
| 待处理任务 | `todo` | 仅显示待处理任务 |
| 待审核任务 | `review` | 仅显示待审核任务 |
| 已完成任务 | `done` | 仅显示完成相关任务 |

## 后端设计

`GET /tasks/api/list` 接受 `bucket=all`，并归一化为空字符串传给 `appcore.tasks.list_task_center_items()`。这样服务层沿用“空 bucket = 不加状态过滤”的既有语义。

`appcore.tasks.list_task_center_items()` 查询排序改为：

```sql
ORDER BY t.created_at DESC, t.id DESC
```

保留 `id DESC` 作为同一秒创建任务的稳定兜底。

## 不做范围

1. 不改任务状态机。
2. 不改“我的 / 全部”权限规则。
3. 不新增数据库字段或迁移。
4. 不改待派单素材 TAB。

## 验证

1. `tests/test_tasks_routes.py`
   - `/tasks/api/list?bucket=all` 被接受并归一化为空 bucket。
   - `/tasks/` 模板默认 `TC_CURRENT_BUCKET = 'all'`。
   - 子 TAB 包含平行的“任务总览 / 待处理任务 / 待审核任务 / 已完成任务”。
   - 表头为“创建时间”。
2. `tests/test_appcore_tasks_supporting_data.py`
   - 服务层 SQL 使用 `ORDER BY t.created_at DESC, t.id DESC`。
3. `python3 -m compileall appcore/tasks.py web/routes/tasks.py`
