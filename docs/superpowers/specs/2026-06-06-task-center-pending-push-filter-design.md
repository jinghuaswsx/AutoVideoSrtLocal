# Task Center Pending Push Filter Design

## Context

Task center overview tabs are defined in `2026-05-21-task-center-overview-tab-sort-design.md`, child acceptance checks are defined in `2026-05-20-task-center-child-acceptance-design.md`, and push management defines pending push as material not pushed yet while all readiness conditions are satisfied.

Operators need a separate "素材待推送" view. For translation users, these tasks are no longer active translation work, so they must not remain in "进行中任务".

## Definition

`素材待推送` is a derived filter, not a new persisted `tasks.status`.

A task matches `pending_push` when all conditions are true:

- `tasks.parent_task_id IS NOT NULL`
- `tasks.status IN ('assigned', 'review')`
- the target-language `media_items` row is resolved by `media_items.task_id = tasks.id`, matching `media_items.lang` to `tasks.country_code`
- that target-language `media_items.pushed_at IS NULL`
- all push readiness prerequisites except `final_push_confirmation` are confirmed by existing task acceptance data
- `final_push_confirmation` is not confirmed yet

Because `final_push_confirmation` currently completes a child task into `status='done'`, completed child tasks are outside this filter even when their material has not been pushed yet.

## UI Behavior

Task center overview adds a sub tab:

- label: `素材待推送`
- bucket: `pending_push`

The task status dropdown adds:

- label: `素材待推送`
- value: `pending_push`

`进行中任务` excludes tasks matching `pending_push`.

## Backend Behavior

`GET /tasks/api/list` accepts `bucket=pending_push` and `task_status=pending_push`.

`appcore.tasks.list_task_center_items()` applies the derived condition for `pending_push` instead of checking only `tasks.status`.

The derived condition must reuse persisted readiness override/event data and material fields. It must not compute heavy external checks or trigger network operations during list loading.

## Verification

- Route tests prove `bucket=pending_push` and `task_status=pending_push` are accepted.
- Service tests prove `pending_push` uses the derived condition and `todo` excludes it.
- Template tests prove the new tab and dropdown option render.
- `python3 -m compileall appcore/tasks.py web/routes/tasks.py`
