# Task Center Overview Tab Sort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make “任务总览” the first overview sub-tab, load all visible tasks by default, and sort tasks by creation time newest first.

**Architecture:** Keep the change inside the existing task center page and list service. The route normalizes `bucket=all` to the service’s existing empty-bucket behavior, the template changes the default UI state, and the service changes only SQL ordering.

**Tech Stack:** Python 3.12, Flask, Jinja, pytest, vanilla JavaScript.

---

### Task 1: Route And Template Contract

**Files:**
- Modify: `tests/test_tasks_routes.py`
- Modify: `web/routes/tasks.py`
- Modify: `web/templates/tasks_list.html`

- [ ] **Step 1: Write the failing route/template tests**

Add tests asserting `bucket=all` is accepted and normalized, the rendered page defaults `TC_CURRENT_BUCKET` to `all`, the first sub-tab is “任务总览”, and the table header says “创建时间”.

- [ ] **Step 2: Run the route/template tests to verify they fail**

Run: `pytest tests/test_tasks_routes.py::test_api_list_accepts_all_bucket_as_unfiltered_overview tests/test_tasks_routes.py::test_index_html_contains_tab_buttons -q`

Expected: failures showing `bucket=all` is rejected or the template still defaults to `todo`.

- [ ] **Step 3: Implement the minimal route/template changes**

In `web/routes/tasks.py`, allow `bucket=all` and pass an empty string to `list_task_center_items()`. In `web/templates/tasks_list.html`, set `TC_CURRENT_BUCKET = 'all'`, render the first sub-tab as “任务总览” with `data-bucket="all"`, and show the created time column.

- [ ] **Step 4: Run the focused route/template tests**

Run: `pytest tests/test_tasks_routes.py::test_api_list_accepts_all_bucket_as_unfiltered_overview tests/test_tasks_routes.py::test_index_html_contains_tab_buttons -q`

Expected: both tests pass.

### Task 2: Service Sorting

**Files:**
- Modify: `tests/test_appcore_tasks_supporting_data.py`
- Modify: `appcore/tasks.py`

- [ ] **Step 1: Write the failing service test**

Add an assertion to the existing list tests that the SQL contains `ORDER BY t.created_at DESC, t.id DESC`.

- [ ] **Step 2: Run the service test to verify it fails**

Run: `pytest tests/test_appcore_tasks_supporting_data.py::test_list_task_center_items_filters_and_serializes_rows -q`

Expected: failure because the service currently orders by `t.id DESC`.

- [ ] **Step 3: Implement the minimal service change**

Change `appcore.tasks.list_task_center_items()` SQL ordering to `ORDER BY t.created_at DESC, t.id DESC`.

- [ ] **Step 4: Run the service test**

Run: `pytest tests/test_appcore_tasks_supporting_data.py::test_list_task_center_items_filters_and_serializes_rows -q`

Expected: pass.

### Task 3: Final Verification

**Files:**
- Verify: `tests/test_tasks_routes.py`
- Verify: `tests/test_appcore_tasks_supporting_data.py`
- Verify: `appcore/tasks.py`
- Verify: `web/routes/tasks.py`

- [ ] **Step 1: Run focused pytest**

Run: `pytest tests/test_tasks_routes.py tests/test_appcore_tasks_supporting_data.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Run compile check**

Run: `python3 -m compileall appcore/tasks.py web/routes/tasks.py`

Expected: both modules compile.
