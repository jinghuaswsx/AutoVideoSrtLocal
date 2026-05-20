# Task Center Step Review Assets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the exact video and image assets that need approval inside their corresponding task process steps.

**Architecture:** Add a read-only `review-assets` service payload that groups current review assets by event step. The task drawer fetches this payload with events and renders video/image previews inside the timeline card for the matching step.

**Tech Stack:** Python 3.12, Flask, Jinja template JavaScript, pytest.

---

### Task 1: Backend Review Assets Payload

**Files:**
- Modify: `appcore/tasks.py`
- Test: `tests/test_task_review_assets_service.py`

- [ ] **Step 1: Write failing tests**

Add tests for parent `raw_review` returning a `raw_uploaded` video asset, and child `review` returning `submitted` video, cover, and detail image assets.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_task_review_assets_service.py -q`
Expected: FAIL because `get_task_review_assets` does not exist.

- [ ] **Step 3: Implement service**

Add `get_task_review_assets(task_id)` plus small helpers for media object URLs and image filenames. Use existing tables only.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_task_review_assets_service.py -q`
Expected: PASS.

### Task 2: Route and Frontend Timeline Rendering

**Files:**
- Modify: `web/routes/tasks.py`
- Modify: `web/templates/tasks_list.html`
- Test: `tests/test_tasks_routes.py`
- Test: `tests/test_task_center_closure_assets.py`

- [ ] **Step 1: Write failing tests**

Assert the new route delegates to `tasks_svc.get_task_review_assets`, and the page contains timeline asset rendering functions and the current review entry.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_tasks_routes.py tests/test_task_center_closure_assets.py -q`
Expected: FAIL because the route and frontend hooks are absent.

- [ ] **Step 3: Implement route and JS**

Add `GET /tasks/api/<id>/review-assets`; fetch it from `tcOpenDetail`; render assets in `tcRenderEventTimeline`; add a current review button that scrolls to the target step.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_tasks_routes.py tests/test_task_center_closure_assets.py -q`
Expected: PASS.

### Task 3: Final Verification

**Files:**
- Verify: `appcore/tasks.py`
- Verify: `web/routes/tasks.py`
- Verify: `web/templates/tasks_list.html`

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_tasks_routes.py tests/test_task_center_closure_assets.py tests/test_task_review_assets_service.py -q`
Expected: PASS.

- [ ] **Step 2: Compile changed Python modules**

Run: `python3 -m compileall appcore/tasks.py web/routes/tasks.py`
Expected: exit code 0.
