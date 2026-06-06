# Task Center Skip-Push Completion Design

## Context

Push management already supports marking a material as `skip_push=1` when an administrator decides the material should not be pushed. Task center completion currently needs the same business interpretation for two terminal push decisions:

- the task-bound material was pushed successfully;
- an administrator marked the task-bound material as not to be pushed.

This extends:

- `2026-05-07-pushes-skip-push-design.md`
- `2026-06-01-task-center-auto-archive-design.md`
- `2026-06-06-task-center-status-taxonomy-cleanup-design.md`

## Required Behavior

For a child task bound to a target-language `media_items` row, task center must treat both of these as task completion:

1. Push management successfully pushes the material.
2. An administrator marks the material as `skip_push=1`.

These two push-management decisions are the only business events allowed to move a small-language child task to `status='done'`. Asset readiness, successful translation output generation, admin approval, and `final_push_confirmation` are prerequisites for reaching "pending push"; they must not mark a child task completed by themselves.

Both paths must move the child task to `status='done'`, set `completed_at` if it is empty, clear `last_reason`, and write a task event. If all children under the parent are now terminal and at least one child is `done`, the parent task moves to `status='all_done'`.

`unskip` only clears the material's skip flag. It does not reopen a task by itself; reopening remains an explicit rework/reset action.

Historical rows can have `media_items.task_id IS NULL`. For those rows, push management must infer the task from the target-language material's `product_id` and `lang` using the same task-resolution logic already used by push rework.

English/source materials can be automatically marked `skip_push=1` by the system. That flag is not a task-completion signal for small-language translation child tasks bound to the same source material. Task completion from skip-push only applies to the corresponding target-language material, not to the EN source material.

The completion service must validate the material-task relationship before writing `done`: the material must either be bound to the child task or be a historical unbound target-language material for the same product and child `country_code`. A source material, wrong-language material, wrong-product material, or material bound to another task is not a valid completion input even if it has push history or `skip_push=1`.

The completion service must also verify the terminal push decision on the material row itself: `push_material_approved` requires `media_items.pushed_at IS NOT NULL`, and `push_material_skipped` requires `COALESCE(media_items.skip_push, 0)=1`.

## Backend Design

`appcore.tasks` owns task status transitions. Push routes should not hand-write task SQL.

Add one shared completion helper for push-management decisions:

- successful push records `push_material_approved`;
- skip push records `push_material_skipped`;
- both paths also write a `completed` task event when the child was not already done.

The shared helper is the only writer of child `status='done'` for small-language push completion. Other task-center actions may submit, approve, confirm readiness, or reopen tasks, but they must not complete a child task without one of the two push-management decisions above.

Existing `record_push_material_approved()` should call the shared helper. Add `record_push_material_skipped()` for the skip path.

`web.routes.pushes` must call task completion from:

- `/pushes/api/items/<id>/push` after downstream push success;
- `/pushes/api/items/<id>/mark-pushed` after manual success marking;
- `/pushes/api/items/<id>/skip` after `skip_push` is set.

Task completion failures should be logged without masking the material push/skip response, matching the existing push-success behavior.

Add a backfill helper that scans child tasks still in `assigned` or `review` and completes them when there is a matching target-language material with `skip_push=1`. Matching uses:

- exact `media_items.task_id = tasks.id`, or
- `media_items.product_id = tasks.media_product_id` and `LOWER(media_items.lang) = LOWER(tasks.country_code)` for historical unbound material rows.

The helper is idempotent: already completed children are skipped, and it only writes completion events when the task status changes.

## Auto Archive

Automatic task archive must consider a completed child resolved when the latest bound material is either:

- pushed: `media_items.pushed_at IS NOT NULL`;
- explicitly skipped: `COALESCE(media_items.skip_push, 0)=1`.

Parent auto-archive uses the same resolved-material condition for all completed children.

## Verification

- Service tests prove successful push completion marks child `done` and can advance the parent to `all_done`.
- Service tests prove skip completion marks `assigned` and `review` children `done`.
- Service tests prove source, wrong-language, wrong-product, or otherwise unrelated materials cannot mark a child `done`.
- Route tests prove manual `mark-pushed` and `skip` resolve historical unbound target-language materials and call the task service.
- Route tests prove EN/source material skip-push does not complete child translation tasks.
- Auto-archive tests prove skipped completed materials are eligible for archive and unresolved materials are not.
- Compile checks cover `appcore/tasks.py` and `web/routes/pushes.py`.
