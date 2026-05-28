# 任务中心取消入口与历史详情设计

- **日期**：2026-05-28
- **上位锚点**：
  - `docs/任务中心需求文档-2026-04-26.md`
  - `docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-review-process-view-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-detail-sticky-refresh-route-design.md`

## 背景

任务中心已有父任务和子任务的取消 API，详情抽屉里也能看到审核流程和 `task_events` 历史。但列表操作栏缺少管理员直接取消入口，普通处理人看到的操作入口和管理员批量处理入口不一致。旧规则中父任务取消会级联取消非 `done` 子任务；本次业务要求是“所有状态保留在当前节点不再动，状态变更为已取消”，因此取消必须只作用于当前行对应的任务。

## 目标

1. 任务中心列表操作栏始终提供“查看详情”入口，用于查看任务当前状态和历史流程。
2. 管理员在可取消任务的操作栏看到“取消任务”按钮；普通用户不显示该按钮。
3. 点击“取消任务”后要求填写原因，原因仍沿用 `>=10` 字符门禁。
4. 取消只更新当前任务：`status='cancelled'`、写 `cancelled_at`、保留 `last_reason`、写 `task_events(event_type='cancelled')`。
5. 父任务取消不再级联子任务；子任务取消不改变父任务。
6. 取消后的操作栏只提供“查看详情”，详情内继续展示历史流程、取消原因和技术详情。

## 行为规则

### 可取消范围

父任务可取消状态保持为：

- `pending`
- `raw_in_progress`
- `raw_review`
- `raw_done`

子任务可取消状态保持为：

- `blocked`
- `assigned`
- `review`

终态任务不可再次取消：

- 父任务：`all_done` / `cancelled`
- 子任务：`done` / `cancelled`

### 不联动原则

本设计覆盖旧规格里的“父取消级联非 done 子任务”规则。新的取消语义为：

- 父任务取消：只更新父任务本身，所有子任务状态、负责人、完成时间和取消时间保持原值。
- 子任务取消：只更新子任务本身，父任务状态保持原值。
- 已完成、审核中、阻塞中、处理中等其他节点都不被当前任务取消动作牵连。

### 历史详情

“查看详情”复用现有详情抽屉和 `/tasks/detail/<id>` 深链。详情抽屉继续加载：

- 精确任务记录：`/tasks/api/list?...&task_id=<id>`
- 审核流程：`/tasks/api/<id>/events`
- 当前审核素材：`/tasks/api/<id>/review-assets`
- 产出素材：`/tasks/api/<id>/artifacts`
- 子任务 readiness：`/tasks/api/child/<id>/readiness`

## 前端行为

列表操作栏改成按钮组：

- 所有可见任务都有“查看详情”按钮。
- 管理员看到可取消任务时，按钮组额外显示危险按钮“取消任务”。
- 普通用户只看到符合权限的处理按钮和“查看详情”，不显示“取消任务”。
- 取消确认文案不再提示“非已完成子任务也会一起终止”，改成强调“只取消当前任务，不影响关联任务”。
- 取消成功后刷新列表；如果当前详情抽屉正在看同一任务，同步刷新详情。

## 后端行为

`appcore.tasks.cancel_parent()` 改为单点取消父任务，不查询或更新子任务。

`appcore.tasks.cancel_child()` 保持单点取消子任务，不改变父任务。

路由继续复用：

- `POST /tasks/api/parent/<id>/cancel`
- `POST /tasks/api/child/<id>/cancel`

两条路由继续 `@login_required + @admin_required`，前端 mutating 请求继续带 `X-CSRFToken`。

## 不做范围

- 不新增数据库表或迁移。
- 不新增独立历史详情页。
- 不改变任务创建、审核、打回、验收门禁和素材绑定逻辑。
- 不改变已完成任务自动汇总为 `all_done` 的规则。

## 验证

1. `pytest tests/test_appcore_tasks.py::test_cancel_parent_service_only_updates_current_parent -q`
2. `pytest tests/test_tasks_routes.py::test_task_center_overview_action_column_shows_detail_and_admin_cancel tests/test_tasks_routes.py::test_task_center_cancel_confirm_text_is_non_cascading tests/test_tasks_routes.py::test_parent_admin_endpoints_forbid_non_admin tests/test_tasks_routes.py::test_child_admin_endpoints_forbid_non_admin -q`
3. `python -m compileall appcore/tasks.py web/routes/tasks.py`
4. 手工打开 `/tasks/`：管理员列表可见“查看详情 / 取消任务”；普通用户列表不显示“取消任务”；取消后对应任务进入“已取消”，关联父/子任务状态不变。
