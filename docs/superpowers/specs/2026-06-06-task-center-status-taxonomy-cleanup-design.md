# Task Center Status Taxonomy Cleanup Design

## Context

Task center child tasks still expose legacy `review` wording as "待审核", while push management now treats a task-bound material as `pending` once all readiness gates, including `final_push_confirmation`, are satisfied. This creates inconsistent UI for tasks such as `609`: the persisted task row is `review`, but its material is already push-ready and should be shown as "待推送".

This design extends:

- `2026-05-20-task-center-child-acceptance-design.md`
- `2026-05-21-task-center-overview-tab-sort-design.md`
- `2026-06-06-task-center-pending-push-filter-design.md`

## Canonical User-Facing States

Task center must use these labels for operators:

| Label | Meaning |
| --- | --- |
| `待处理` | The current user/admin can continue processing the task. Legacy child `review` is also treated as a processable task state, not a separate review queue. |
| `阻塞中` | The task lacks a prerequisite, for example a subtitle-removal parent task is not ready. |
| `管理员打回` | Admin rejected/reworked material; the worker can process it again, then it can return to `待推送`. |
| `待推送` | The corresponding material is `pending` in push management: required assets exist, push readiness is satisfied, `final_push_confirmation` is confirmed, and the material has not been pushed. |
| `已完成` | The task was completed by a successful push flow. |
| `已取消` | An admin cancelled the task. |

`review` remains an internal persisted status for compatibility, but it must not be shown as "待审核" in task-center list rows, detail headers, tabs, or filters.

## Tab Model

Task center overview tabs become:

| Tab Label | bucket | Behavior |
| --- | --- | --- |
| `全部任务` | `all` | All tasks in the selected scope. |
| `进行中` | `todo` | `待处理`, `阻塞中`, and `管理员打回`; excludes `待推送`. |
| `阻塞中` | `blocked` | Blocked tasks only. |
| `待推送` | `pending_push` | Tasks whose task-bound material is push-management `pending`. |
| `已完成` | `done` | Completed tasks only. |
| `已归档` | `archived` | Archived tasks, unchanged. |

The old `待审核任务` tab is removed. Legacy URLs or API requests using `bucket=review` remain accepted temporarily and are normalized to `bucket=todo` so old links do not break.

## Backend Behavior

`list_task_center_items()` must calculate a derived display state for returned rows:

- `is_pending_push`: true when the row matches the existing pending-push derived condition.
- `display_status`: one of `todo`, `blocked`, `admin_rework`, `pending_push`, `done`, `cancelled`, or a parent/raw-specific fallback.
- `display_high_level`: `in_progress`, `pending_push`, `completed`, or `terminated`.

The derived state is calculated for ordinary list rows and exact task detail loads, not only when filtering by `bucket=pending_push`.

Filtering rules:

- `bucket=todo` / `task_status=todo`: include processable, blocked, and admin-rework tasks, excluding `pending_push`.
- `bucket=blocked` / `task_status=blocked`: blocked tasks only.
- `bucket=pending_push` / `task_status=pending_push`: existing derived pending-push condition.
- `bucket=done` / `task_status=done`: completed statuses only.
- `bucket=review` / `task_status=review`: accepted as a legacy alias for `todo`.

`high_status=in_progress` must also exclude pending-push tasks.

## Frontend Behavior

`tasks_list.html` must use the display fields for badges and detail headers. It must not render "待审核任务" or "待审核" for child `review` tasks. For a row like task `609`, the list and detail header should show `待推送`.

The status dropdown must expose:

- 全部状态
- 待处理
- 阻塞中
- 管理员打回
- 待推送
- 已完成
- 已取消

## Verification

- Route/template tests prove the removed tab and labels no longer render.
- Service tests prove legacy `review` filters normalize to `todo`.
- Service tests prove ordinary rows receive `is_pending_push` and display status fields.
- Service tests prove `todo` includes `review` but excludes pending-push.
- Compile checks cover `appcore/tasks.py` and `web/routes/tasks.py`.
