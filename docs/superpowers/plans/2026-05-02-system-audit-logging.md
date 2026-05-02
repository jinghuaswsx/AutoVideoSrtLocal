# System Audit Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a superadmin-only system security audit module that records critical account actions and media download/access behavior.

**Architecture:** Add a dedicated append-only `system_audit_logs` table and `appcore.system_audit` DAO that never blocks business requests on logging errors. Expose a superadmin-only Flask admin page/API, then add explicit audit calls to authentication, media access/download routes, media write routes, task actions, push actions, and user management actions.

**Tech Stack:** Flask, Flask-Login, Jinja2, vanilla JavaScript, MySQL migrations, pytest.

---

## File Structure

- Create `db/migrations/2026_05_02_system_audit_logs.sql`
  Creates the audit table and indexes.
- Create `appcore/system_audit.py`
  Owns audit writes and read queries. This file catches all write exceptions.
- Create `web/routes/security_audit.py`
  Superadmin-only page and JSON APIs.
- Create `web/templates/admin_security_audit.html`
  Admin UI shell with filters, stats, logs tab, media downloads tab, loading/empty/error states.
- Create `web/static/admin_security_audit.js`
  Fetches audit data, renders tables, handles filters and pagination.
- Modify `web/app.py`
  Registers the security audit blueprint.
- Modify `web/templates/layout.html`
  Adds the sidebar entry visible only to `current_user.is_superadmin`.
- Modify `web/routes/auth.py`
  Records login success/failure and logout.
- Modify `web/routes/medias.py`
  Records media video access, raw-source video access, ZIP downloads, and key successful media mutations.
- Modify `web/routes/tasks.py`
  Records task center actions after successful service calls.
- Modify `web/routes/pushes.py`
  Records push-management write actions after success/failure state is known.
- Modify `web/routes/admin.py`
  Records superadmin user-management and admin settings actions.
- Create `tests/test_system_audit.py`
  DAO tests for write/read/filter behavior.
- Create `tests/test_security_audit_routes.py`
  Route permission and template/API tests.
- Create `tests/test_medias_audit.py`
  Focused media access/download/mutation audit tests.

---

### Task 1: Database Migration and DAO

**Files:**
- Create: `db/migrations/2026_05_02_system_audit_logs.sql`
- Create: `appcore/system_audit.py`
- Test: `tests/test_system_audit.py`

- [x] **Step 1: Write failing DAO tests**

Create `tests/test_system_audit.py`:

```python
from datetime import datetime, timedelta


def test_record_inserts_audit_row(monkeypatch):
    from appcore import system_audit

    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 123

    monkeypatch.setattr(system_audit, "execute", fake_execute)

    log_id = system_audit.record(
        actor_user_id=7,
        actor_username="alice",
        action="media_video_access",
        module="medias",
        target_type="media_item",
        target_id=42,
        target_label="demo.mp4",
        status="success",
        request_method="GET",
        request_path="/medias/object",
        ip_address="1.2.3.4",
        user_agent="pytest",
        detail={"object_key": "7/medias/1/demo.mp4"},
    )

    assert log_id == 123
    assert "INSERT INTO system_audit_logs" in captured["sql"]
    assert captured["args"][0] == 7
    assert captured["args"][2] == "media_video_access"
    assert captured["args"][3] == "medias"
    assert captured["args"][5] == "42"
    assert '"object_key": "7/medias/1/demo.mp4"' in captured["args"][-1]


def test_record_swallows_db_errors(monkeypatch):
    from appcore import system_audit

    def boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(system_audit, "execute", boom)

    assert system_audit.record(
        actor_user_id=1,
        actor_username="admin",
        action="login_success",
        module="auth",
    ) is None


def test_list_logs_builds_parameterized_filters(monkeypatch):
    from appcore import system_audit

    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"id": 1, "action": "login_success"}]

    monkeypatch.setattr(system_audit, "query", fake_query)

    rows = system_audit.list_logs(
        date_from="2026-05-01",
        date_to="2026-05-02",
        actor_user_id=2,
        module="medias",
        action="media_video_access",
        keyword="demo",
        limit=50,
        offset=0,
    )

    assert rows == [{"id": 1, "action": "login_success"}]
    assert "actor_user_id = %s" in captured["sql"]
    assert "module = %s" in captured["sql"]
    assert "action = %s" in captured["sql"]
    assert "LIKE %s" in captured["sql"]
    assert captured["args"][:5] == ("2026-05-01", "2026-05-02", 2, "medias", "media_video_access")
    assert captured["args"][-2:] == (50, 0)


def test_list_daily_media_downloads_limits_actions(monkeypatch):
    from appcore import system_audit

    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(system_audit, "query", fake_query)

    system_audit.list_daily_media_downloads(date_from="2026-05-02", date_to="2026-05-02")

    assert "media_video_access" in captured["sql"]
    assert "raw_source_video_access" in captured["sql"]
    assert "detail_images_zip_download" in captured["sql"]
    assert captured["args"][:2] == ("2026-05-02", "2026-05-02")
```

- [x] **Step 2: Run DAO tests to verify RED**

Run:

```powershell
pytest tests/test_system_audit.py -q
```

Expected: fails with `ModuleNotFoundError` or missing `appcore.system_audit`.

- [x] **Step 3: Add migration**

Create `db/migrations/2026_05_02_system_audit_logs.sql`:

```sql
CREATE TABLE IF NOT EXISTS system_audit_logs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  actor_user_id INT NULL,
  actor_username VARCHAR(64) NULL,
  action VARCHAR(64) NOT NULL,
  module VARCHAR(64) NOT NULL,
  target_type VARCHAR(64) NULL,
  target_id VARCHAR(64) NULL,
  target_label VARCHAR(255) NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'success',
  request_method VARCHAR(8) NULL,
  request_path VARCHAR(512) NULL,
  ip_address VARCHAR(64) NULL,
  user_agent VARCHAR(512) NULL,
  detail_json JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_created_at (created_at),
  KEY idx_actor_created (actor_user_id, created_at),
  KEY idx_action_created (action, created_at),
  KEY idx_module_created (module, created_at),
  KEY idx_target (target_type, target_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [x] **Step 4: Implement DAO**

Create `appcore/system_audit.py` with:

```python
from __future__ import annotations

import json
import logging
from typing import Any

from appcore.db import execute, query, query_one

log = logging.getLogger(__name__)

MEDIA_DOWNLOAD_ACTIONS = (
    "media_video_access",
    "raw_source_video_access",
    "detail_images_zip_download",
    "localized_detail_images_zip_download",
)


def _json_dumps(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    return json.dumps(data, ensure_ascii=False, default=str)


def _clean_str(value: Any, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if limit and len(text) > limit:
        return text[:limit]
    return text


def record(
    *,
    actor_user_id: int | None,
    actor_username: str | None,
    action: str,
    module: str,
    target_type: str | None = None,
    target_id: int | str | None = None,
    target_label: str | None = None,
    status: str = "success",
    request_method: str | None = None,
    request_path: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    detail: dict[str, Any] | None = None,
) -> int | None:
    try:
        return execute(
            """
            INSERT INTO system_audit_logs
              (actor_user_id, actor_username, action, module, target_type,
               target_id, target_label, status, request_method, request_path,
               ip_address, user_agent, detail_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(actor_user_id) if actor_user_id is not None else None,
                _clean_str(actor_username, 64),
                _clean_str(action, 64),
                _clean_str(module, 64),
                _clean_str(target_type, 64),
                _clean_str(target_id, 64),
                _clean_str(target_label, 255),
                _clean_str(status or "success", 16),
                _clean_str(request_method, 8),
                _clean_str(request_path, 512),
                _clean_str(ip_address, 64),
                _clean_str(user_agent, 512),
                _json_dumps(detail),
            ),
        ) or None
    except Exception:
        log.debug("system_audit.record failed", exc_info=True)
        return None


def record_from_request(
    *,
    user: Any,
    request_obj: Any,
    action: str,
    module: str,
    target_type: str | None = None,
    target_id: int | str | None = None,
    target_label: str | None = None,
    status: str = "success",
    detail: dict[str, Any] | None = None,
) -> int | None:
    actor_user_id = getattr(user, "id", None)
    username = getattr(user, "username", None)
    remote_addr = getattr(request_obj, "headers", {}).get("X-Forwarded-For", "")
    ip_address = (remote_addr.split(",", 1)[0].strip() if remote_addr else None) or getattr(request_obj, "remote_addr", None)
    user_agent = getattr(getattr(request_obj, "user_agent", None), "string", None)
    return record(
        actor_user_id=actor_user_id if actor_user_id is not None else None,
        actor_username=username,
        action=action,
        module=module,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        status=status,
        request_method=getattr(request_obj, "method", None),
        request_path=getattr(request_obj, "full_path", None) or getattr(request_obj, "path", None),
        ip_address=ip_address,
        user_agent=user_agent,
        detail=detail,
    )


def list_logs(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    actor_user_id: int | None = None,
    module: str | None = None,
    action: str | None = None,
    keyword: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    where = ["1=1"]
    args: list[Any] = []
    if date_from:
        where.append("DATE(created_at) >= %s")
        args.append(date_from)
    if date_to:
        where.append("DATE(created_at) <= %s")
        args.append(date_to)
    if actor_user_id:
        where.append("actor_user_id = %s")
        args.append(int(actor_user_id))
    if module:
        where.append("module = %s")
        args.append(module)
    if action:
        where.append("action = %s")
        args.append(action)
    if keyword:
        where.append("(target_label LIKE %s OR target_id LIKE %s OR request_path LIKE %s OR actor_username LIKE %s)")
        like = f"%{keyword}%"
        args.extend([like, like, like, like])
    args.extend([max(1, min(int(limit), 200)), max(0, int(offset))])
    return query(
        f"""
        SELECT * FROM system_audit_logs
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(args),
    )


def list_daily_media_downloads(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    actor_user_id: int | None = None,
    keyword: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    where = ["action IN ('media_video_access', 'raw_source_video_access', 'detail_images_zip_download', 'localized_detail_images_zip_download')"]
    args: list[Any] = []
    if date_from:
        where.append("DATE(created_at) >= %s")
        args.append(date_from)
    if date_to:
        where.append("DATE(created_at) <= %s")
        args.append(date_to)
    if actor_user_id:
        where.append("actor_user_id = %s")
        args.append(int(actor_user_id))
    if keyword:
        where.append("(target_label LIKE %s OR target_id LIKE %s OR detail_json LIKE %s)")
        like = f"%{keyword}%"
        args.extend([like, like, like])
    args.extend([max(1, min(int(limit), 200)), max(0, int(offset))])
    return query(
        f"""
        SELECT * FROM system_audit_logs
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(args),
    )
```

- [x] **Step 5: Verify GREEN**

Run:

```powershell
pytest tests/test_system_audit.py -q
```

Expected: `4 passed`.

- [x] **Step 6: Commit**

```powershell
git add db/migrations/2026_05_02_system_audit_logs.sql appcore/system_audit.py tests/test_system_audit.py
git commit -m "feat: add system audit log storage"
```

---

### Task 2: Superadmin Audit Page and APIs

**Files:**
- Create: `web/routes/security_audit.py`
- Create: `web/templates/admin_security_audit.html`
- Create: `web/static/admin_security_audit.js`
- Modify: `web/app.py`
- Modify: `web/templates/layout.html`
- Test: `tests/test_security_audit_routes.py`

- [x] **Step 1: Write failing route tests**

Create `tests/test_security_audit_routes.py`:

```python
def _client_with_user(monkeypatch, username="admin", role="superadmin", user_id=1):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["en", "de"])
    from web.app import create_app

    fake_user = {"id": user_id, "username": username, "role": role, "is_active": 1}
    monkeypatch.setattr("web.auth.get_by_id", lambda uid: fake_user if int(uid) == user_id else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def test_security_audit_page_visible_to_reserved_superadmin(monkeypatch):
    client = _client_with_user(monkeypatch, username="admin", role="superadmin", user_id=1)

    resp = client.get("/admin/security-audit")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "系统安全审计" in body
    assert "data-security-audit" in body
    assert "素材下载明细" in body


def test_security_audit_page_forbidden_for_normal_admin(monkeypatch):
    client = _client_with_user(monkeypatch, username="manager", role="admin", user_id=2)

    assert client.get("/admin/security-audit").status_code == 403


def test_security_audit_api_forbidden_for_normal_user(monkeypatch):
    client = _client_with_user(monkeypatch, username="user", role="user", user_id=3)

    assert client.get("/admin/security-audit/api/logs").status_code == 403


def test_security_audit_api_returns_logs(monkeypatch):
    client = _client_with_user(monkeypatch, username="admin", role="superadmin", user_id=1)
    from web.routes import security_audit

    monkeypatch.setattr(security_audit.system_audit, "list_logs", lambda **kwargs: [{"id": 1, "action": "login_success"}])
    monkeypatch.setattr(security_audit.system_audit, "count_logs", lambda **kwargs: 1)

    resp = client.get("/admin/security-audit/api/logs?module=auth")

    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["action"] == "login_success"


def test_layout_shows_security_audit_only_to_superadmin(monkeypatch):
    client = _client_with_user(monkeypatch, username="admin", role="superadmin", user_id=1)
    resp = client.get("/medias/")
    assert "/admin/security-audit" in resp.get_data(as_text=True)

    client = _client_with_user(monkeypatch, username="manager", role="admin", user_id=2)
    resp = client.get("/medias/")
    assert "/admin/security-audit" not in resp.get_data(as_text=True)
```

- [x] **Step 2: Run route tests to verify RED**

Run:

```powershell
pytest tests/test_security_audit_routes.py -q
```

Expected: failures for missing route/blueprint/template.

- [x] **Step 3: Implement `web/routes/security_audit.py`**

Use `current_user.is_superadmin`, not `admin_required`:

```python
from __future__ import annotations

from datetime import date
from functools import wraps

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import system_audit

bp = Blueprint("security_audit", __name__, url_prefix="/admin/security-audit")


def superadmin_only(fn):
    @wraps(fn)
    def _wrap(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, "is_superadmin", False):
            abort(403)
        return fn(*args, **kwargs)
    return _wrap


def _filters():
    today = date.today().isoformat()
    def _int_or_none(raw):
        try:
            return int(raw) if raw else None
        except (TypeError, ValueError):
            return None
    page = max(1, _int_or_none(request.args.get("page")) or 1)
    page_size = min(200, max(1, _int_or_none(request.args.get("page_size")) or 50))
    return {
        "date_from": request.args.get("from") or today,
        "date_to": request.args.get("to") or today,
        "actor_user_id": _int_or_none(request.args.get("user_id")),
        "module": (request.args.get("module") or "").strip() or None,
        "action": (request.args.get("action") or "").strip() or None,
        "keyword": (request.args.get("keyword") or "").strip() or None,
        "limit": page_size,
        "offset": (page - 1) * page_size,
        "page": page,
        "page_size": page_size,
    }


@bp.route("", methods=["GET"])
@login_required
@superadmin_only
def page():
    today = date.today().isoformat()
    return render_template("admin_security_audit.html", today=today)


@bp.route("/api/logs", methods=["GET"])
@login_required
@superadmin_only
def api_logs():
    f = _filters()
    rows = system_audit.list_logs(**{k: f[k] for k in ("date_from", "date_to", "actor_user_id", "module", "action", "keyword", "limit", "offset")})
    total = system_audit.count_logs(**{k: f[k] for k in ("date_from", "date_to", "actor_user_id", "module", "action", "keyword")})
    return jsonify({"items": rows, "total": total, "page": f["page"], "page_size": f["page_size"]})


@bp.route("/api/media-downloads", methods=["GET"])
@login_required
@superadmin_only
def api_media_downloads():
    f = _filters()
    rows = system_audit.list_daily_media_downloads(**{k: f[k] for k in ("date_from", "date_to", "actor_user_id", "keyword", "limit", "offset")})
    total = system_audit.count_daily_media_downloads(**{k: f[k] for k in ("date_from", "date_to", "actor_user_id", "keyword")})
    return jsonify({"items": rows, "total": total, "page": f["page"], "page_size": f["page_size"]})
```

- [x] **Step 4: Extend DAO count functions**

Add `count_logs()` and `count_daily_media_downloads()` to `appcore/system_audit.py` using the same filters as the list functions, returning `int(row["cnt"] or 0)`.

- [x] **Step 5: Register blueprint**

Modify `web/app.py`:

```python
from web.routes.security_audit import bp as security_audit_bp
```

and in `create_app()`:

```python
app.register_blueprint(security_audit_bp)
```

- [x] **Step 6: Add sidebar link**

Modify `web/templates/layout.html` near other system links:

```jinja2
{% if current_user.is_superadmin %}
<a href="{{ url_for('security_audit.page') }}" target="_blank" rel="noopener noreferrer" {% if request.path.startswith('/admin/security-audit') %}class="active"{% endif %}>
  <span class="nav-icon">◎</span> 系统安全审计
</a>
{% endif %}
```

- [x] **Step 7: Create template and JS**

`web/templates/admin_security_audit.html` must include:

```html
{% extends "layout.html" %}
{% block title %}系统安全审计 - AutoVideoSrt{% endblock %}
{% block page_title %}系统安全审计{% endblock %}
{% block content %}
<section class="audit-page" data-security-audit>
  <div class="audit-tabs">
    <button type="button" data-tab="logs" class="active">操作日志</button>
    <button type="button" data-tab="downloads">素材下载明细</button>
  </div>
  <form class="audit-filters" data-audit-filters>
    <input type="date" name="from" value="{{ today }}">
    <input type="date" name="to" value="{{ today }}">
    <input type="number" name="user_id" placeholder="账号 ID">
    <select name="module">
      <option value="">全部模块</option>
      <option value="auth">账号</option>
      <option value="medias">素材</option>
      <option value="tasks">任务</option>
      <option value="pushes">推送</option>
      <option value="admin">管理</option>
    </select>
    <input type="text" name="action" placeholder="动作编码">
    <input type="search" name="keyword" placeholder="对象/路径/账号关键词">
    <button type="submit" class="btn btn-primary btn-sm">查询</button>
  </form>
  <div class="audit-state" data-audit-state>加载中...</div>
  <div class="audit-table-wrap">
    <table data-audit-table></table>
  </div>
</section>
<script src="{{ url_for('static', filename='admin_security_audit.js') }}"></script>
{% endblock %}
```

`web/static/admin_security_audit.js` should fetch `/admin/security-audit/api/logs` or `/admin/security-audit/api/media-downloads` and render rows with escaped HTML.

- [x] **Step 8: Verify GREEN**

Run:

```powershell
pytest tests/test_security_audit_routes.py -q
```

Expected: all tests pass.

- [x] **Step 9: Commit**

```powershell
git add appcore/system_audit.py web/routes/security_audit.py web/app.py web/templates/layout.html web/templates/admin_security_audit.html web/static/admin_security_audit.js tests/test_security_audit_routes.py
git commit -m "feat: add superadmin security audit page"
```

---

### Task 3: Authentication Audit Events

**Files:**
- Modify: `web/routes/auth.py`
- Test: `tests/test_auth_audit.py`

- [x] **Step 1: Write failing auth audit tests**

Create `tests/test_auth_audit.py`:

```python
def test_login_success_records_audit(monkeypatch):
    from web.routes import auth

    calls = []
    row = {"id": 9, "username": "alice", "role": "user", "is_active": 1, "password_hash": "hash"}
    monkeypatch.setattr(auth, "get_by_username", lambda username: row)
    monkeypatch.setattr(auth, "check_password", lambda password, hashed: True)
    monkeypatch.setattr(auth, "login_user", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth.system_audit, "record_from_request", lambda **kwargs: calls.append(kwargs))

    from web.app import create_app
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    app = create_app()
    client = app.test_client()

    resp = client.post("/login", data={"username": "alice", "password": "pw"})

    assert resp.status_code == 302
    assert calls[0]["action"] == "login_success"
    assert calls[0]["module"] == "auth"
    assert calls[0]["target_label"] == "alice"


def test_login_failure_records_audit(monkeypatch):
    from web.routes import auth

    calls = []
    monkeypatch.setattr(auth, "get_by_username", lambda username: None)
    monkeypatch.setattr(auth.system_audit, "record_from_request", lambda **kwargs: calls.append(kwargs))

    from web.app import create_app
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    app = create_app()
    client = app.test_client()

    client.post("/login", data={"username": "missing", "password": "pw"})

    assert calls[0]["action"] == "login_failed"
    assert calls[0]["status"] == "failed"
    assert calls[0]["detail"]["username"] == "missing"
```

- [x] **Step 2: Run auth audit tests to verify RED**

Run:

```powershell
pytest tests/test_auth_audit.py -q
```

Expected: failures because `web.routes.auth` does not import/use `system_audit`.

- [x] **Step 3: Implement audit in auth routes**

Modify `web/routes/auth.py`:

```python
from appcore import system_audit
```

On successful login before redirect:

```python
system_audit.record_from_request(
    user=User(row),
    request_obj=request,
    action="login_success",
    module="auth",
    target_type="user",
    target_id=row["id"],
    target_label=row["username"],
)
```

On failed login:

```python
system_audit.record_from_request(
    user=None,
    request_obj=request,
    action="login_failed",
    module="auth",
    target_type="user",
    target_label=username,
    status="failed",
    detail={"username": username},
)
```

In logout before `logout_user()`:

```python
system_audit.record_from_request(
    user=current_user,
    request_obj=request,
    action="logout",
    module="auth",
    target_type="user",
    target_id=current_user.id,
    target_label=current_user.username,
)
```

- [x] **Step 4: Verify GREEN**

Run:

```powershell
pytest tests/test_auth_audit.py -q
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```powershell
git add web/routes/auth.py tests/test_auth_audit.py
git commit -m "feat: audit authentication events"
```

---

### Task 4: Media Access and Download Audit

**Files:**
- Modify: `web/routes/medias.py`
- Test: `tests/test_medias_audit.py`

- [x] **Step 1: Write failing media audit tests**

Create `tests/test_medias_audit.py`:

```python
def test_raw_source_video_access_records_audit(authed_client_no_db, monkeypatch):
    from web.routes import medias as routes

    calls = []
    monkeypatch.setattr(routes.medias, "get_raw_source", lambda rid: {
        "id": rid,
        "product_id": 44,
        "display_name": "raw demo",
        "video_object_key": "1/medias/44/raw.mp4",
        "file_size": 10,
    })
    monkeypatch.setattr(routes.medias, "get_product", lambda pid: {"id": pid, "name": "Product A", "product_code": "p-a"})
    monkeypatch.setattr(routes, "_send_media_object", lambda object_key: ("ok", 200))
    monkeypatch.setattr(routes.system_audit, "record_from_request", lambda **kwargs: calls.append(kwargs))

    resp = authed_client_no_db.get("/medias/raw-sources/99/video")

    assert resp.status_code == 200
    assert calls[0]["action"] == "raw_source_video_access"
    assert calls[0]["target_type"] == "media_raw_source"
    assert calls[0]["target_id"] == 99
    assert calls[0]["detail"]["product_id"] == 44


def test_media_object_proxy_records_media_item_access(authed_client_no_db, monkeypatch):
    from web.routes import medias as routes

    calls = []
    monkeypatch.setattr(routes.medias, "find_item_by_object_key", lambda key: {
        "id": 7,
        "product_id": 3,
        "filename": "demo.mp4",
        "display_name": "Demo Video",
        "object_key": key,
        "lang": "en",
        "file_size": 123,
    })
    monkeypatch.setattr(routes.medias, "get_product", lambda pid: {"id": pid, "name": "Product B", "product_code": "p-b"})
    monkeypatch.setattr(routes, "_send_media_object", lambda object_key: ("ok", 200))
    monkeypatch.setattr(routes.system_audit, "record_from_request", lambda **kwargs: calls.append(kwargs))

    resp = authed_client_no_db.get("/medias/object?object_key=1/medias/3/demo.mp4")

    assert resp.status_code == 200
    assert calls[0]["action"] == "media_video_access"
    assert calls[0]["target_type"] == "media_item"
    assert calls[0]["target_id"] == 7
    assert calls[0]["detail"]["lang"] == "en"


def test_detail_images_zip_download_records_audit(authed_client_no_db, monkeypatch):
    from web.routes import medias as routes

    calls = []
    monkeypatch.setattr(routes.medias, "get_product", lambda pid: {"id": pid, "name": "Product C", "product_code": "p-c"})
    monkeypatch.setattr(routes.medias, "is_valid_language", lambda lang: True)
    monkeypatch.setattr(routes.medias, "list_detail_images", lambda pid, lang: [{"id": 1, "object_key": "1/medias/2/detail.jpg"}])
    monkeypatch.setattr(routes, "_download_media_object", lambda object_key, dest: open(dest, "wb").write(b"x") or dest)
    monkeypatch.setattr(routes.system_audit, "record_from_request", lambda **kwargs: calls.append(kwargs))

    resp = authed_client_no_db.get("/medias/api/products/2/detail-images/download-zip?lang=en")

    assert resp.status_code == 200
    assert calls[0]["action"] == "detail_images_zip_download"
    assert calls[0]["target_type"] == "media_product"
    assert calls[0]["target_id"] == 2
```

- [x] **Step 2: Run media audit tests to verify RED**

Run:

```powershell
pytest tests/test_medias_audit.py -q
```

Expected: failures for missing `system_audit` import and `find_item_by_object_key`.

- [x] **Step 3: Add DAO helper**

Modify `appcore/medias.py`:

```python
def find_item_by_object_key(object_key: str) -> dict | None:
    return query_one(
        "SELECT * FROM media_items WHERE object_key=%s AND deleted_at IS NULL LIMIT 1",
        ((object_key or "").strip(),),
    )
```

- [x] **Step 4: Add media audit helpers**

Modify `web/routes/medias.py`:

```python
from appcore import system_audit
```

Add helpers:

```python
def _audit_media_item_access(item: dict, product: dict | None, *, action: str = "media_video_access") -> None:
    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action=action,
        module="medias",
        target_type="media_item",
        target_id=item.get("id"),
        target_label=item.get("display_name") or item.get("filename"),
        detail={
            "product_id": item.get("product_id"),
            "product_name": (product or {}).get("name"),
            "product_code": (product or {}).get("product_code"),
            "filename": item.get("filename"),
            "display_name": item.get("display_name"),
            "lang": item.get("lang"),
            "object_key": item.get("object_key"),
            "file_size": item.get("file_size"),
            "range_header": request.headers.get("Range"),
        },
    )
```

Add a similar `_audit_raw_source_video_access(row, product)`.

- [x] **Step 5: Wire media object routes**

In `media_object_proxy()`, after object key validation and before returning:

```python
item = medias.find_item_by_object_key(object_key)
if item:
    product = medias.get_product(int(item["product_id"]))
    _audit_media_item_access(item, product)
```

In `raw_source_video_url()`, before returning:

```python
_audit_raw_source_video_access(row, p)
```

In both ZIP download routes, after rows are confirmed and before `send_file()`:

```python
system_audit.record_from_request(
    user=current_user,
    request_obj=request,
    action="detail_images_zip_download",
    module="medias",
    target_type="media_product",
    target_id=pid,
    target_label=p.get("name"),
    detail={"product_id": pid, "product_code": p.get("product_code"), "lang": lang, "kind": kind, "count": len(rows)},
)
```

Use `localized_detail_images_zip_download` for localized ZIP.

- [x] **Step 6: Verify GREEN**

Run:

```powershell
pytest tests/test_medias_audit.py -q
```

Expected: all tests pass.

- [x] **Step 7: Commit**

```powershell
git add appcore/medias.py web/routes/medias.py tests/test_medias_audit.py
git commit -m "feat: audit media access and downloads"
```

---

### Task 5: Key Mutation Audit Events

**Files:**
- Modify: `web/routes/medias.py`
- Modify: `web/routes/tasks.py`
- Modify: `web/routes/pushes.py`
- Modify: `web/routes/admin.py`
- Test: extend `tests/test_medias_audit.py`, create `tests/test_task_push_admin_audit.py`

- [x] **Step 1: Write failing mutation tests**

Create `tests/test_task_push_admin_audit.py` with route-level monkeypatch tests:

```python
def test_task_claim_records_audit(authed_client_no_db, monkeypatch):
    from web.routes import tasks

    calls = []
    monkeypatch.setattr(tasks, "_has_capability", lambda code: True)
    monkeypatch.setattr(tasks.tasks_svc, "claim_parent", lambda **kwargs: None)
    monkeypatch.setattr(tasks.system_audit, "record_from_request", lambda **kwargs: calls.append(kwargs))

    resp = authed_client_no_db.post("/tasks/api/parent/5/claim")

    assert resp.status_code == 200
    assert calls[0]["action"] == "task_claimed"
    assert calls[0]["module"] == "tasks"
    assert calls[0]["target_id"] == 5


def test_push_reset_records_audit(authed_client_no_db, monkeypatch):
    from web.routes import pushes as route

    calls = []
    monkeypatch.setattr(route.pushes, "reset_push_state", lambda item_id: None)
    monkeypatch.setattr(route.system_audit, "record_from_request", lambda **kwargs: calls.append(kwargs))

    resp = authed_client_no_db.post("/pushes/api/items/8/reset")

    assert resp.status_code == 200
    assert calls[0]["action"] == "push_reset"
    assert calls[0]["module"] == "pushes"
    assert calls[0]["target_id"] == 8
```

Extend `tests/test_medias_audit.py` with delete item success audit:

```python
def test_delete_media_item_records_audit(authed_client_no_db, monkeypatch):
    from web.routes import medias as routes

    calls = []
    monkeypatch.setattr(routes.medias, "get_item", lambda item_id: {"id": item_id, "product_id": 3, "filename": "demo.mp4", "object_key": "x.mp4"})
    monkeypatch.setattr(routes.medias, "get_product", lambda pid: {"id": pid, "name": "Product D", "product_code": "p-d"})
    monkeypatch.setattr(routes.medias, "soft_delete_item", lambda item_id: None)
    monkeypatch.setattr(routes, "_delete_media_object", lambda key: None)
    monkeypatch.setattr(routes.system_audit, "record_from_request", lambda **kwargs: calls.append(kwargs))

    resp = authed_client_no_db.delete("/medias/api/items/12")

    assert resp.status_code == 200
    assert calls[0]["action"] == "media_item_deleted"
    assert calls[0]["target_type"] == "media_item"
    assert calls[0]["target_id"] == 12
```

- [x] **Step 2: Run tests to verify RED**

Run:

```powershell
pytest tests/test_medias_audit.py tests/test_task_push_admin_audit.py -q
```

Expected: failures for missing imports/audit calls.

- [x] **Step 3: Implement lightweight audit calls**

Add `from appcore import system_audit` to `web/routes/tasks.py`, `web/routes/pushes.py`, and `web/routes/admin.py`.

After successful operations:

- `tasks.api_parent_claim` -> `task_claimed`
- `tasks.api_parent_upload_done` -> `task_raw_uploaded`
- `tasks.api_parent_approve` -> `task_raw_approved`
- `tasks.api_parent_reject` -> `task_raw_rejected`
- `tasks.api_parent_cancel` -> `task_parent_cancelled`
- `tasks.api_child_submit` -> `task_child_submitted`
- `tasks.api_child_approve` -> `task_child_approved`
- `tasks.api_child_reject` -> `task_child_rejected`
- `tasks.api_child_cancel` -> `task_child_cancelled`
- `pushes.api_push` -> `push_requested` on success and `push_failed` on handled failure
- `pushes.api_mark_pushed` -> `push_marked_success`
- `pushes.api_mark_failed` -> `push_marked_failed`
- `pushes.api_reset` -> `push_reset`
- `pushes.api_push_localized_texts` -> `push_localized_texts`
- `admin.users` create/toggle/update role -> `user_created`, `user_active_changed`, `user_role_changed`
- `admin.set_user_permissions` -> `user_permissions_changed`
- `admin.settings` POST -> `system_settings_updated`

For each call:

```python
system_audit.record_from_request(
    user=current_user,
    request_obj=request,
    action="task_claimed",
    module="tasks",
    target_type="task",
    target_id=tid,
)
```

- [x] **Step 4: Verify GREEN**

Run:

```powershell
pytest tests/test_medias_audit.py tests/test_task_push_admin_audit.py -q
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```powershell
git add web/routes/medias.py web/routes/tasks.py web/routes/pushes.py web/routes/admin.py tests/test_medias_audit.py tests/test_task_push_admin_audit.py
git commit -m "feat: audit key platform actions"
```

---

### Task 6: Focused Verification and Branch Completion

**Files:**
- No new files unless fixes are needed.

- [x] **Step 1: Run focused audit suite**

Run:

```powershell
pytest tests/test_system_audit.py tests/test_security_audit_routes.py tests/test_auth_audit.py tests/test_medias_audit.py tests/test_task_push_admin_audit.py -q
```

Expected: all pass.

- [x] **Step 2: Run adjacent route tests**

Run:

```powershell
pytest tests/test_web_routes.py tests/test_tasks_routes.py tests/test_pushes_routes.py -q
```

Expected: all pass or identify pre-existing failures with evidence.

- [x] **Step 3: Inspect git status**

Run:

```powershell
git status --short --branch
```

Expected: clean worktree on feature branch after commits.

- [x] **Step 4: Commit any verification fixes**

If fixes were needed:

```powershell
git add <changed files>
git commit -m "fix: stabilize system audit logging"
```

---

### Task 7: Merge and Online Release

**Files:**
- No code edits expected.

- [x] **Step 1: Sync with origin**

Run:

```powershell
git fetch origin
git status --short --branch
git rebase origin/master
```

Expected: branch rebased or conflicts resolved manually.

- [x] **Step 2: Re-run focused verification after rebase**

Run:

```powershell
pytest tests/test_system_audit.py tests/test_security_audit_routes.py tests/test_auth_audit.py tests/test_medias_audit.py tests/test_task_push_admin_audit.py -q
```

Expected: all pass.

- [x] **Step 3: Merge to master**

Because this work is in an isolated worktree, merge through git refs without editing the main checkout:

```powershell
git checkout master
git pull --ff-only origin master
git merge --no-ff feat/system-audit-logging -m "Merge system audit logging"
```

Expected: merge succeeds.

- [x] **Step 4: Push master**

Run:

```powershell
git push origin master
```

Expected: push succeeds.

- [x] **Step 5: Publish online**

Use project release rule: user requested online release, so commit, merge to main, then publish online. The current repository publish script is `deploy/publish.sh`; it pushes the current branch, SSHes to `/opt/autovideosrt`, runs `git pull`, restarts `autovideosrt`, and performs a local HTTP health check. New SQL migrations under `db/migrations/` are applied by `main.py` startup through `appcore.db_migrations.ensure_up_to_date()`.

From the merged `master` branch, run:

```powershell
bash deploy/publish.sh "feat: add system security audit logging"
```

Then verify service and HTTP explicitly:

```powershell
ssh -i C:\Users\admin\.ssh\CC.pem root@172.30.254.14 "systemctl status autovideosrt.service --no-pager -l | head -n 20"
curl -I http://172.30.254.14/
```

Expected: publish script exits 0, service is active, and HTTP is reachable.

Do not connect to Windows local MySQL. If database verification is needed, use the server environment only.
