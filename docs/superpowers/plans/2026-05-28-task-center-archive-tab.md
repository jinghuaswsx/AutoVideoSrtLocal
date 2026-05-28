# Task Center Archive Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent “已归档” task-center TAB and an archive action for completed tasks.

**Architecture:** Store archive visibility as nullable fields on `tasks`, keep the existing task `status` unchanged, and route all list filtering through `appcore.tasks.list_task_center_items()`. The UI adds one status subtab and a secondary action for completed rows.

**Tech Stack:** Python 3.12, Flask routes, MySQL SQL migrations, Jinja template JavaScript, pytest.

---

### Task 1: Regression Tests

**Files:**
- Modify: `tests/test_appcore_tasks_supporting_data.py`
- Modify: `tests/test_tasks_routes.py`

- [ ] Add tests that prove default task-center lists filter `t.archived_at IS NULL`.
- [ ] Add tests that prove `archived=True` lists filter `t.archived_at IS NOT NULL`.
- [ ] Add tests that prove exact detail fetches can pass `archived=None` to skip archive visibility filtering.
- [ ] Add tests that prove `archive_task()` updates completed tasks and writes an `archived` event.
- [ ] Add route/template tests for `bucket=archived`, the “已归档” TAB, the archive button, and the archive POST route.
- [ ] Run the new tests and confirm they fail before implementation.

### Task 2: Storage And Service

**Files:**
- Create: `db/migrations/2026_05_28_task_center_archive_fields.sql`
- Modify: `appcore/tasks.py`

- [ ] Add nullable `archived_at` and `archived_by` columns to `tasks`.
- [ ] Add `archived` parameter to `list_task_center_items()` and apply the SQL visibility filter.
- [ ] Allow `archived=None` for exact detail fetches that must find archived rows.
- [ ] Serialize `archived_at` and `archived_by` in list rows.
- [ ] Implement `archive_task()` with admin and completed-status guards.
- [ ] Run service tests and confirm green.

### Task 3: Route And UI

**Files:**
- Modify: `web/routes/tasks.py`
- Modify: `web/templates/tasks_list.html`
- Modify: `tests/test_tasks_routes.py`

- [ ] Accept `bucket=archived` in `/tasks/api/list` and pass `archived=True` to the service.
- [ ] Accept `include_archived=1` only with `task_id` and pass `archived=None` for detail deep links.
- [ ] Add `POST /tasks/api/<id>/archive`.
- [ ] Add the “已归档” subtab in the overview panel.
- [ ] Render `归档` next to `查看结果` for completed rows when admin is viewing.
- [ ] Add `tcArchiveTask()` using existing CSRF headers and refresh the list after success.

### Task 4: Verification

**Files:**
- Verify only.

- [ ] Run `pytest tests/test_appcore_tasks_supporting_data.py tests/test_tasks_routes.py -q`.
- [ ] Run `python -m compileall appcore/tasks.py web/routes/tasks.py`.
- [ ] Review `git diff --check`.
