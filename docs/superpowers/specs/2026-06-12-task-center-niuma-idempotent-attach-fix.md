# Task Center Niuma Idempotent Attach Fix

- Date: 2026-06-12
- Status: confirmed for implementation
- Anchors:
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-20-task-center-assignment-and-niuma-automation-fix.md`
  - `docs/superpowers/specs/2026-05-21-task-center-niuma-status-link-design.md`

## Incident

Task center parent task `1039` was stuck in `raw_in_progress` after its
Niuma subtitle-removal result had completed. The task had been reassigned from
raw processor user `238` to user `77` after submission. The automatic attach
path still called `tasks.mark_uploaded()` with the original actor `238`, so the
service raised `only assignee can mark uploaded`.

Because `attach_niuma_result_to_parent_task()` writes `raw_niuma_done` before
advancing the parent task status, the periodic reconciliation retried the same
completed subtitle-removal task every minute and appended thousands of duplicate
`raw_niuma_done` events. The detail page fetched all task events and rendered
each one as a timeline card, freezing the browser main thread.

## Target Behavior

1. Attaching the same `subtitle_task_id` to the same parent task is idempotent:
   an existing `raw_niuma_done` event prevents another duplicate event from
   being written.
2. Automatic Niuma attach advances the parent task using the current parent
   assignee when the parent is still `raw_in_progress`. This keeps the state
   transition compatible with `tasks.mark_uploaded()` while preserving the
   original actor on the `raw_niuma_done` event.
3. If a later failure occurs after the `raw_niuma_done` event has already been
   recorded, future retries do not grow the timeline unboundedly.
4. The task detail events endpoint remains compatible with existing event
   rendering; no schema or route change is required for this fix.

## Verification

1. `pytest tests/test_task_raw_video_processing.py -q`
2. `python scripts/pytest_related.py --base origin/master --run`
3. `python -m py_compile appcore/task_raw_video_processing.py`
4. Production task `1039` remains at a small events payload after the data
   repair, and duplicate `raw_niuma_done` rows do not resume.
