# Task Center Niuma Idempotent Attach Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent repeated `raw_niuma_done` events when automatic Niuma result attach is retried after a parent task assignee changes.

**Architecture:** Keep the fix inside `appcore/task_raw_video_processing.py`, where the Niuma result is attached. Add regression tests in `tests/test_task_raw_video_processing.py` using existing monkeypatch patterns.

**Tech Stack:** Python 3.12, pytest, existing task-center service modules.

---

### Task 1: Regression Tests

**Files:**
- Modify: `tests/test_task_raw_video_processing.py`

- [ ] Add a failing test proving a completed Niuma attach uses the current parent assignee for `tasks.mark_uploaded()`.
- [ ] Add a failing test proving an existing `raw_niuma_done` event suppresses duplicate event writes on retry.
- [ ] Run the new tests and confirm they fail for the expected reasons.

### Task 2: Minimal Implementation

**Files:**
- Modify: `appcore/task_raw_video_processing.py`

- [ ] Add a small helper or inline check that detects an existing `raw_niuma_done` for the same `subtitle_task_id`.
- [ ] Keep the result upload idempotent and suppress only duplicate timeline events.
- [ ] Call `tasks.mark_uploaded()` and oversize rejection with the current parent assignee when available.

### Task 3: Verification

**Files:**
- Test: `tests/test_task_raw_video_processing.py`
- Test: related pytest script output

- [ ] Run `pytest tests/test_task_raw_video_processing.py -q`.
- [ ] Run `python scripts/pytest_related.py --base origin/master --run`.
- [ ] Run `python -m py_compile appcore/task_raw_video_processing.py`.
