# Raw Video Pool Tabs Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the raw video processing page into task-center-style tabs with pagination and a direct subtitle-removal task entry.

**Architecture:** Keep the existing `raw_video_pool` blueprint and service. Replace the old three-section payload with a paginated bucket payload, and render the page with one table whose active tab controls the API query.

**Tech Stack:** Python 3.12, Flask, Jinja, pytest, existing `tasks` and `task_events` tables.

---

### Task 1: Service Contract Tests

**Files:**
- Modify: `tests/test_raw_video_pool_service_unit.py`
- Modify: `appcore/raw_video_pool.py`

- [ ] Add a failing test that `list_visible_tasks(bucket="todo", page=2, page_size=10)` returns `items/page/page_size/total/total_pages/counts`.
- [ ] Assert `pending` is not queried for the raw-video-pool page.
- [ ] Assert each shaped row includes `status`, `updated_at`, and `task_detail_url`.
- [ ] Run: `pytest tests/test_raw_video_pool_service_unit.py::test_list_visible_tasks_returns_paginated_bucket_payload -q`
- [ ] Implement the smallest service change to pass.

### Task 2: Route And Template Tests

**Files:**
- Modify: `tests/test_raw_video_pool_routes.py`
- Modify: `web/routes/raw_video_pool.py`
- Modify: `web/templates/raw_video_pool_list.html`

- [ ] Add a failing route test that `/raw-video-pool/api/list?bucket=todo&page=3&page_size=150` delegates sanitized `bucket="todo"`, `page=3`, `page_size=100`.
- [ ] Add a template smoke test for four tabs, no “待认领”, `rvpRenderTaskPager`, and the “任务入口” column.
- [ ] Run the two new tests and confirm they fail for missing behavior.
- [ ] Implement route parameter parsing and replace the template with the tabbed table renderer.

### Task 3: Verification

**Files:**
- Test: `tests/test_raw_video_pool_service_unit.py`
- Test: `tests/test_raw_video_pool_routes.py`

- [ ] Run: `pytest tests/test_raw_video_pool_service_unit.py tests/test_raw_video_pool_routes.py -q`
- [ ] Run a syntax check: `python -m compileall appcore/raw_video_pool.py web/routes/raw_video_pool.py`
- [ ] Run unauthenticated smoke for `/raw-video-pool/` through pytest route coverage.

## Self-Review

- Spec coverage: covers tab removal, four buckets, pagination, route query parsing, task-detail entry, and focused verification.
- Placeholder scan: no placeholder steps.
- Type consistency: `bucket/page/page_size/task_detail_url` names match the spec.
