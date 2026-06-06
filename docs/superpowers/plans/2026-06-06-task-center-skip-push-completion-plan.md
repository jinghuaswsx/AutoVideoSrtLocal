# Task Center Skip-Push Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both push success and administrator "mark not to push" complete the task-center child task, backfill historical skipped materials, and allow resolved completed tasks to auto-archive.

**Architecture:** `appcore.tasks` remains the owner of task state transitions. `web.routes.pushes` only detects the material decision and delegates to task service helpers. Auto-archive uses a shared resolved-material SQL condition of `pushed_at IS NOT NULL OR skip_push=1`.

**Tech Stack:** Python 3.12, Flask routes, appcore service functions, pytest.

---

### Task 1: Document And Test Task Completion From Push Decisions

**Files:**
- Create: `docs/superpowers/specs/2026-06-06-task-center-skip-push-completion-design.md`
- Modify: `tests/test_appcore_tasks.py`

- [x] **Step 1: Add service tests**

Add tests covering `record_push_material_approved()` and `record_push_material_skipped()` so they prove child status becomes `done`, completion events are written, and parent status can become `all_done`. Add a regression test for historical source-material skip correcting a `cancelled` child task to `done`.

- [x] **Step 2: Run RED tests**

Run:

```bash
pytest tests/test_appcore_tasks.py::test_record_push_material_approved_completes_child_and_parent tests/test_appcore_tasks.py::test_record_push_material_skipped_completes_child -q
```

Expected: fail because the completion helper does not exist yet.

### Task 2: Implement Shared Completion Helper

**Files:**
- Modify: `appcore/tasks.py`

- [x] **Step 1: Add constants and helper**

Add `CHILD_PUSH_MATERIAL_SKIPPED_EVENT`, push completion reason constants, and a private helper that updates the child task and parent task inside one transaction.

- [x] **Step 2: Wire public service functions**

Make `record_push_material_approved()` call the helper and add `record_push_material_skipped()`.

- [x] **Step 3: Run service tests**

Run:

```bash
pytest tests/test_appcore_tasks.py::test_record_push_material_approved_completes_child_and_parent tests/test_appcore_tasks.py::test_record_push_material_skipped_completes_child -q
```

Expected: pass.

### Task 3: Wire Push Routes

**Files:**
- Modify: `web/routes/pushes.py`
- Modify: `tests/test_pushes_routes.py`

- [x] **Step 1: Add route tests**

Add tests proving `/mark-pushed` and `/skip` call the corresponding task service when historical material has no `task_id` and must be resolved by product/language. Add a route test proving an English/source material skip completes every child task bound through `tasks.media_item_id`.

- [x] **Step 2: Run RED route tests**

Run:

```bash
pytest tests/test_pushes_routes.py::test_mark_pushed_records_task_completion_for_unbound_item tests/test_pushes_routes.py::test_skip_records_task_completion_for_unbound_item -q
```

Expected: fail because the route only updates the material.

- [x] **Step 3: Implement route delegation**

Add small route helpers that load the item/product context and call `record_push_material_approved()` or `record_push_material_skipped()`.

### Task 4: Update Auto Archive Resolution

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `tests/test_appcore_tasks_supporting_data.py`

- [x] **Step 1: Add archive tests**

Add tests proving completed skipped child tasks are candidates and parent checks accept skipped completed children.

- [x] **Step 2: Implement SQL condition**

Replace strict `mi.pushed_at IS NOT NULL` checks in auto-archive candidate SQL with `(mi.pushed_at IS NOT NULL OR COALESCE(mi.skip_push, 0)=1)`.

### Task 5: Add Historical Backfill

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `tests/test_appcore_tasks_supporting_data.py`

- [x] **Step 1: Add backfill tests**

Add tests proving active child tasks are completed when a matching material is already `skip_push=1`, including unbound historical rows matched by product/language, source-material-bound child tasks, and historical `cancelled` child tasks.

- [x] **Step 2: Implement idempotent backfill helper**

Add `backfill_skip_push_completed_tasks(limit: int | None = None) -> dict` that scans `assigned/review/cancelled` child tasks and calls the shared skip completion helper for each candidate.

### Task 6: Verification, Commit, Publish

**Files:**
- All modified files.

- [x] **Step 1: Run focused tests**

```bash
pytest tests/test_appcore_tasks.py::test_record_push_material_approved_writes_task_event tests/test_appcore_tasks.py::test_record_push_material_approved_completes_child_and_parent tests/test_appcore_tasks.py::test_record_push_material_skipped_completes_child tests/test_pushes_routes.py::test_push_success_records_task_review_flow_event_no_db tests/test_pushes_routes.py::test_mark_pushed_records_task_completion_for_unbound_item tests/test_pushes_routes.py::test_skip_records_task_completion_for_unbound_item tests/test_appcore_tasks_supporting_data.py::test_auto_archive_completed_pushed_tasks_archives_done_child_with_pushed_material tests/test_appcore_tasks_supporting_data.py::test_auto_archive_completed_pushed_tasks_archives_done_child_with_skipped_material tests/test_appcore_tasks_supporting_data.py::test_backfill_skip_push_completed_tasks_completes_unbound_matching_material -q
```

- [x] **Step 2: Run compile and diff checks**

```bash
python3 -m compileall appcore/tasks.py web/routes/pushes.py
git diff --check
```

- [ ] **Step 3: Commit with docs anchor**

Commit message includes:

```text
Docs-anchor: docs/superpowers/specs/2026-06-06-task-center-skip-push-completion-design.md
```

- [ ] **Step 4: Push and deploy**

Push to `master`, restart test and production services, then verify HTTP and a task-bound skip path.
