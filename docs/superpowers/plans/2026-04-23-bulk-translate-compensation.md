# Bulk Translate Compensation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every item in a persisted bulk translation batch gets a real child task even if the web service restarts during dispatch.

**Architecture:** Keep the parent plan as the source of truth, assign deterministic child task ids from `parent_id + item.idx`, and make child creation idempotent by reusing an existing project row. Startup recovery prepares incomplete bulk parents and starts their scheduler again; the scheduler dispatches due pending items even while other children are running, waiting for voice selection, or already failed.

**Tech Stack:** Python, Flask, pytest, existing `projects.state_json` task state.

---

### Task 1: Scheduler no longer lets one item block dispatch

**Files:**
- Modify: `tests/test_bulk_translate_runtime.py`
- Modify: `appcore/bulk_translate_runtime.py`

- [ ] **Step 1: Write failing tests** for dispatching due pending items while previous children are active, waiting for voice, or failed.
- [ ] **Step 2: Run focused tests** with `pytest tests/test_bulk_translate_runtime.py -q` and confirm the new tests fail.
- [ ] **Step 3: Update scheduler loop** to poll all active items, dispatch due pending items, and only finalize parent status after no pending or running items remain.
- [ ] **Step 4: Re-run focused tests** and confirm runtime tests pass.

### Task 2: Child task creation is idempotent

**Files:**
- Modify: `tests/test_bulk_translate_runtime.py`
- Modify: `appcore/bulk_translate_runtime.py`

- [ ] **Step 1: Write a failing test** showing `_create_child_task()` reuses a deterministic existing child project instead of creating a duplicate.
- [ ] **Step 2: Implement deterministic ids** with `uuid.uuid5(...).hex`, store them on `child_task_id` and `sub_task_id` before creation, and reuse any existing `projects.id`.
- [ ] **Step 3: Re-run focused tests** and confirm no duplicate-creation path remains.

### Task 3: Startup recovery restarts incomplete bulk schedulers

**Files:**
- Modify: `tests/test_bulk_translate_recovery.py`
- Modify: `appcore/bulk_translate_recovery.py`
- Modify: `web/app.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_bulk_translate_routes.py`

- [ ] **Step 1: Write failing recovery tests** for running/interrupted parents with pending items.
- [ ] **Step 2: Add `prepare_bulk_translate_startup_recovery()`** returning task ids that need scheduler restart and updating recoverable parents back to `running`.
- [ ] **Step 3: Wire app startup** to start background schedulers for those ids.
- [ ] **Step 4: Patch no-DB route fixtures** so startup recovery stays mocked in unit tests.
- [ ] **Step 5: Run `pytest tests/test_bulk_translate_runtime.py tests/test_bulk_translate_recovery.py -q`** and report any route-suite baseline limitations separately.
