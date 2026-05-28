# Task Center Urgent Priority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add administrator-controlled urgent task marking, urgent/non-urgent filtering, and urgent-first task ordering in task center lists.

**Architecture:** Store urgency as `tasks.is_urgent` so filtering and pagination remain simple SQL. The existing `/tasks/api/list` route carries the filter into `appcore.tasks.list_task_center_items()`, while a new admin-only mutation toggles urgency and writes a `task_events` audit event. The existing `tasks_list.html` inline script renders filters, badges, list actions, and detail actions from the returned `is_urgent` field.

**Tech Stack:** Python 3.12, Flask, MySQL SQL migrations, Jinja template inline JavaScript, pytest.

---

### Task 1: RED Tests for Schema, Service, Route, and Template

**Files:**
- Modify: `tests/test_db_migration_tasks_tables.py`
- Modify: `tests/test_appcore_tasks_supporting_data.py`
- Modify: `tests/test_tasks_routes.py`

- [ ] **Step 1: Add schema tests**

Add tests that expect the base task table and the new migration to contain `is_urgent` and `idx_urgent_created`:

```python
def test_migration_has_urgent_column_on_tasks():
    sql = Path("db/migrations/2026_04_26_add_tasks_tables.sql").read_text(
        encoding="utf-8"
    )
    assert "is_urgent" in sql


def test_task_center_urgent_priority_migration_exists():
    sql = Path("db/migrations/2026_05_28_task_center_urgent_priority.sql").read_text(
        encoding="utf-8"
    )
    assert "ALTER TABLE tasks" in sql
    assert "is_urgent" in sql
    assert "idx_urgent_created" in sql
```

- [ ] **Step 2: Add service tests**

Add tests in `tests/test_appcore_tasks_supporting_data.py` for urgent sorting/filtering and mutation delegation:

```python
def test_list_task_center_items_orders_urgent_before_created(monkeypatch):
    from appcore import tasks

    captured = {}
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)
    _mock_task_center_count(monkeypatch, tasks)

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    tasks.list_task_center_items(
        tab="all",
        user_id=1,
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket="",
        page=1,
        page_size=20,
    )

    assert "ORDER BY t.is_urgent DESC, t.created_at DESC, t.id DESC" in captured["sql"]


def test_list_task_center_items_filters_urgent_and_normal(monkeypatch):
    from appcore import tasks

    captured = []
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)

    def fake_query_one(sql, args=()):
        captured.append(("count", sql, args))
        return {"total": 0}

    def fake_query_all(sql, args=()):
        captured.append(("list", sql, args))
        return []

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    tasks.list_task_center_items(
        tab="all",
        user_id=1,
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket="",
        page=1,
        page_size=20,
        urgency="urgent",
    )
    tasks.list_task_center_items(
        tab="all",
        user_id=1,
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket="",
        page=1,
        page_size=20,
        urgency="normal",
    )

    assert "t.is_urgent=1" in captured[0][1]
    assert "t.is_urgent=0" in captured[2][1]
```

- [ ] **Step 3: Add route and template tests**

Add tests in `tests/test_tasks_routes.py` for `/tasks/api/list?urgency=...`, invalid urgency, admin-only mutation, and template strings:

```python
def test_api_list_accepts_urgency_filter(authed_user_client_no_db, monkeypatch):
    captured = {}

    def fake_list_task_center_items(**kwargs):
        captured.update(kwargs)
        return {"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 1}

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        fake_list_task_center_items,
        raising=False,
    )

    rsp = authed_user_client_no_db.get("/tasks/api/list?tab=mine&urgency=urgent")

    assert rsp.status_code == 200
    assert captured["urgency"] == "urgent"


def test_api_list_rejects_invalid_urgency(authed_user_client_no_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        lambda **kwargs: calls.append(kwargs),
        raising=False,
    )

    rsp = authed_user_client_no_db.get("/tasks/api/list?tab=mine&urgency=soon")

    assert rsp.status_code == 400
    assert calls == []


def test_task_urgency_endpoint_admin_only_and_delegates(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_set_task_urgency(**kwargs):
        captured.update(kwargs)
        return {"changed": True, "is_urgent": True, "previous_is_urgent": False}

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.set_task_urgency",
        fake_set_task_urgency,
        raising=False,
    )

    rsp = authed_client_no_db.post("/tasks/api/44/urgency", json={"is_urgent": True})

    assert rsp.status_code == 200
    assert captured == {"task_id": 44, "actor_user_id": 1, "is_urgent": True}


def test_task_center_template_contains_urgent_controls(authed_client_no_db):
    body = authed_client_no_db.get("/tasks/").data.decode("utf-8")

    assert "tcUrgencyFilter" in body
    assert "紧急" in body
    assert "标记紧急" in body
    assert "取消紧急" in body
```

- [ ] **Step 4: Run RED tests**

Run:

```bash
pytest tests/test_db_migration_tasks_tables.py::test_migration_has_urgent_column_on_tasks tests/test_db_migration_tasks_tables.py::test_task_center_urgent_priority_migration_exists tests/test_appcore_tasks_supporting_data.py::test_list_task_center_items_orders_urgent_before_created tests/test_appcore_tasks_supporting_data.py::test_list_task_center_items_filters_urgent_and_normal tests/test_tasks_routes.py::test_api_list_accepts_urgency_filter tests/test_tasks_routes.py::test_api_list_rejects_invalid_urgency tests/test_tasks_routes.py::test_task_urgency_endpoint_admin_only_and_delegates tests/test_tasks_routes.py::test_task_center_template_contains_urgent_controls -q
```

Expected: FAIL because migration, service parameter, route handling, endpoint, and template controls do not exist yet.

### Task 2: Implement Database and Service Layer

**Files:**
- Create: `db/migrations/2026_05_28_task_center_urgent_priority.sql`
- Modify: `db/migrations/2026_04_26_add_tasks_tables.sql`
- Modify: `appcore/tasks.py`

- [ ] **Step 1: Add migration**

Create the migration:

```sql
-- db/migrations/2026_05_28_task_center_urgent_priority.sql
-- 任务中心紧急任务标记；详见 docs/superpowers/specs/2026-05-28-task-center-urgent-priority-design.md

ALTER TABLE tasks
  ADD COLUMN is_urgent TINYINT(1) NOT NULL DEFAULT 0,
  ADD KEY idx_urgent_created (is_urgent, created_at, id);
```

- [ ] **Step 2: Update base DDL**

Add `is_urgent TINYINT(1) NOT NULL DEFAULT 0` to the task table and `KEY idx_urgent_created (is_urgent, created_at, id)`.

- [ ] **Step 3: Update list service**

Change `list_task_center_items()` signature to add `urgency: str = ""`. Apply:

```python
if urgency == "urgent":
    where.append("t.is_urgent=1")
elif urgency == "normal":
    where.append("t.is_urgent=0")
elif urgency:
    raise ValueError("invalid urgency")
```

Select `t.*` already includes the field; return `is_urgent: bool(row.get("is_urgent"))`. Change order to `ORDER BY t.is_urgent DESC, t.created_at DESC, t.id DESC`.

- [ ] **Step 4: Add mutation service**

Add `set_task_urgency(task_id: int, actor_user_id: int, is_urgent: bool) -> dict` that locks the task row, updates `is_urgent`, writes `urgent_marked` only when changed, and returns `{changed, is_urgent, previous_is_urgent}`.

- [ ] **Step 5: Run service/schema tests**

Run:

```bash
pytest tests/test_db_migration_tasks_tables.py tests/test_appcore_tasks_supporting_data.py::test_list_task_center_items_orders_urgent_before_created tests/test_appcore_tasks_supporting_data.py::test_list_task_center_items_filters_urgent_and_normal -q
```

Expected: PASS.

### Task 3: Implement Route and Template UI

**Files:**
- Modify: `web/routes/tasks.py`
- Modify: `web/templates/tasks_list.html`

- [ ] **Step 1: Route list filter**

Parse `urgency = (request.args.get("urgency") or "").strip()`, normalize `all` to empty, reject values outside `urgent|normal`, and pass `urgency=urgency` to `list_task_center_items()`.

- [ ] **Step 2: Route mutation**

Add:

```python
@bp.route("/api/<int:tid>/urgency", methods=["POST"])
@login_required
@admin_required
def api_task_urgency(tid: int):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("is_urgent"), bool):
        return _json_response({"error": "is_urgent must be boolean"}, 400)
    try:
        result = tasks_svc.set_task_urgency(
            task_id=tid,
            actor_user_id=int(current_user.id),
            is_urgent=payload["is_urgent"],
        )
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 404)
    _audit_task_action(tid, "task_urgency_changed", result)
    return _json_response({"ok": True, **result})
```

- [ ] **Step 3: Template filter and badge**

Add `tcUrgencyFilter` select, include it in `tcRenderTaskList()` URL params, render `tcUrgentBadge(it)`, and reset pagination on change.

- [ ] **Step 4: Template actions**

Add admin-only `tcTaskUrgencyAction(it)` to row actions and detail toolbar. Implement `tcSetTaskUrgency(id, isUrgent)` using existing `tcFetchJson` so CSRF headers are preserved.

- [ ] **Step 5: Run route/template tests**

Run:

```bash
pytest tests/test_tasks_routes.py::test_api_list_accepts_urgency_filter tests/test_tasks_routes.py::test_api_list_rejects_invalid_urgency tests/test_tasks_routes.py::test_task_urgency_endpoint_admin_only_and_delegates tests/test_tasks_routes.py::test_task_center_template_contains_urgent_controls -q
```

Expected: PASS.

### Task 4: Final Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_db_migration_tasks_tables.py tests/test_appcore_tasks_supporting_data.py::test_list_task_center_items_orders_urgent_before_created tests/test_appcore_tasks_supporting_data.py::test_list_task_center_items_filters_urgent_and_normal tests/test_tasks_routes.py::test_api_list_accepts_urgency_filter tests/test_tasks_routes.py::test_api_list_rejects_invalid_urgency tests/test_tasks_routes.py::test_task_urgency_endpoint_admin_only_and_delegates tests/test_tasks_routes.py::test_task_center_template_contains_urgent_controls -q
```

Expected: PASS.

- [ ] **Step 2: Compile changed Python modules**

Run:

```bash
python -m compileall appcore/tasks.py web/routes/tasks.py
```

Expected: both files compile successfully.
