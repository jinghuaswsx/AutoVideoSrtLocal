# Web Platform Plan A — Auth, Projects, API Keys

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-user authentication, MySQL-backed project persistence, per-user API key configuration, admin user management, and project list/detail pages to the existing AutoVideoSrt web app.

**Architecture:** Add `appcore/db.py` (MySQL pool), `appcore/users.py`, `appcore/api_keys.py`; upgrade `appcore/task_state.py` to persist to DB; add Flask-Login auth; add new blueprints for auth/settings/admin; replace single-page `index.html` with multi-page Jinja2 templates. Deploy to server port 80.

**Tech Stack:** Flask-Login, bcrypt, pymysql, DBUtils, existing Flask+SocketIO stack.

---

## File Map

**New files:**
- `appcore/db.py` — MySQL connection pool, `get_conn()`, `query()`, `execute()`
- `appcore/users.py` — `get_by_username()`, `get_by_id()`, `create_user()`, `list_users()`, `set_active()`
- `appcore/api_keys.py` — `get_key(user_id, service)`, `set_key(user_id, service, value, extra)`, `get_all(user_id)`
- `web/auth.py` — Flask-Login `User` class + `login_manager` instance
- `web/routes/auth.py` — `/login`, `/logout` blueprint
- `web/routes/settings.py` — `/settings` GET/POST blueprint
- `web/routes/admin.py` — `/admin/users` GET/POST blueprint
- `web/routes/projects.py` — `/` project list, `/projects/<task_id>` detail blueprint
- `web/templates/login.html`
- `web/templates/projects.html` — project list with card grid
- `web/templates/project_detail.html` — read-only step artifacts view
- `web/templates/settings.html`
- `web/templates/admin_users.html`
- `web/templates/layout.html` — base template with nav
- `db/schema.sql` — full CREATE TABLE statements
- `db/migrate.py` — run schema.sql against configured DB

**Modified files:**
- `appcore/task_state.py` — add DB write-through on `create()`, `update()`, `set_step()`, `set_artifact()`, `set_preview_file()`, `set_variant_artifact()`, `set_variant_preview_file()`, `confirm_alignment()`, `confirm_segments()`; `get()` falls back to DB if not in memory
- `config.py` — add `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- `web/app.py` — register new blueprints, init Flask-Login
- `web/services/pipeline_runner.py` — accept `user_id` param, pass through to `task_state.create()`
- `web/routes/task.py` — pass `current_user.id` to `pipeline_runner.start()`
- `main.py` — keep as-is (port stays 5000 locally; server uses 80 via gunicorn)
- `requirements.txt` — add Flask-Login, bcrypt, pymysql, DBUtils

---

## Task 1: DB schema and connection pool

**Files:**
- Create: `db/schema.sql`
- Create: `db/migrate.py`
- Create: `appcore/db.py`
- Modify: `config.py`

- [ ] **Step 1: Add DB config to config.py**

Add to end of `config.py`:
```python
# MySQL
DB_HOST = _env("DB_HOST", "172.30.254.14")
DB_PORT = int(_env("DB_PORT", "3306"))
DB_NAME = _env("DB_NAME", "auto_video")
DB_USER = _env("DB_USER", "root")
DB_PASSWORD = _env("DB_PASSWORD", "<server-managed-password>")
```

- [ ] **Step 2: Write schema.sql**

Create `db/schema.sql`:
```sql
CREATE DATABASE IF NOT EXISTS auto_video CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE auto_video;

CREATE TABLE IF NOT EXISTS users (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    username     VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role         ENUM('admin','user') NOT NULL DEFAULT 'user',
    is_active    TINYINT(1) NOT NULL DEFAULT 1,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT NOT NULL,
    service      VARCHAR(32) NOT NULL,
    key_value    VARCHAR(512) NOT NULL,
    extra_config JSON,
    updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_service (user_id, service),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS projects (
    id               VARCHAR(36) PRIMARY KEY,
    user_id          INT NOT NULL,
    original_filename VARCHAR(255),
    thumbnail_path   VARCHAR(512),
    status           VARCHAR(32) NOT NULL DEFAULT 'uploaded',
    task_dir         VARCHAR(512),
    state_json       LONGTEXT,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at       DATETIME NOT NULL,
    deleted_at       DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS usage_logs (
    id                     BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id                INT NOT NULL,
    project_id             VARCHAR(36),
    service                VARCHAR(32) NOT NULL,
    model_name             VARCHAR(128),
    called_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    success                TINYINT(1) NOT NULL DEFAULT 1,
    input_tokens           INT,
    output_tokens          INT,
    audio_duration_seconds FLOAT,
    extra_data             JSON,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

- [ ] **Step 3: Write db/migrate.py**

Create `db/migrate.py`:
```python
"""Run once to create tables. Safe to re-run (uses IF NOT EXISTS)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv()
import pymysql
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
with open(schema_path, encoding="utf-8") as f:
    sql = f.read()

conn = pymysql.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                       password=DB_PASSWORD, charset="utf8mb4")
cursor = conn.cursor()
for stmt in sql.split(";"):
    stmt = stmt.strip()
    if stmt:
        cursor.execute(stmt)
conn.commit()
cursor.close()
conn.close()
print("Migration complete.")
```

- [ ] **Step 4: Write appcore/db.py**

Create `appcore/db.py`:
```python
"""MySQL connection pool. All other appcore modules import from here."""
from __future__ import annotations
import json
from typing import Any

import pymysql
import pymysql.cursors
from dbutils.pooled_db import PooledDB

from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

_pool: PooledDB | None = None


def _get_pool() -> PooledDB:
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=10,
            mincached=2,
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
    return _pool


def get_conn():
    return _get_pool().connection()


def query(sql: str, args: tuple = ()) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def query_one(sql: str, args: tuple = ()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: tuple = ()) -> int:
    """Returns lastrowid for INSERT, rowcount for UPDATE/DELETE."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.lastrowid or cur.rowcount
    finally:
        conn.close()
```

- [ ] **Step 5: Write test for db.py**

Create `tests/test_appcore_db.py`:
```python
"""Smoke-test DB connectivity. Requires live MySQL at configured host."""
import pytest
from appcore.db import query, execute, query_one


def test_query_users_table_exists():
    rows = query("SHOW TABLES LIKE 'users'")
    assert len(rows) == 1


def test_execute_and_query_one():
    # Insert a temporary row and clean up
    execute(
        "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
        ("_test_db_user_", "x", "user"),
    )
    row = query_one("SELECT * FROM users WHERE username = %s", ("_test_db_user_",))
    assert row is not None
    assert row["role"] == "user"
    execute("DELETE FROM users WHERE username = %s", ("_test_db_user_",))
```

- [ ] **Step 6: Run migration and test**

```bash
pip install pymysql DBUtils Flask-Login bcrypt
python db/migrate.py
pytest tests/test_appcore_db.py -v
```

Expected: `Migration complete.` then `1 passed`.

- [ ] **Step 7: Commit**

```bash
git add db/ appcore/db.py config.py tests/test_appcore_db.py requirements.txt
git commit -m "feat: add MySQL schema, migration, and db connection pool"
```

---

## Task 2: Users module + bcrypt auth

**Files:**
- Create: `appcore/users.py`
- Create: `tests/test_appcore_users.py`

- [ ] **Step 1: Write test first**

Create `tests/test_appcore_users.py`:
```python
import pytest
from appcore.users import create_user, get_by_username, get_by_id, list_users, set_active
from appcore.db import execute


@pytest.fixture(autouse=True)
def cleanup():
    yield
    execute("DELETE FROM users WHERE username LIKE '_test_%'")


def test_create_and_get_by_username():
    create_user("_test_alice_", "secret123", role="user")
    u = get_by_username("_test_alice_")
    assert u is not None
    assert u["username"] == "_test_alice_"
    assert u["role"] == "user"
    assert u["is_active"] == 1


def test_password_hash_not_plaintext():
    create_user("_test_bob_", "mypassword")
    u = get_by_username("_test_bob_")
    assert u["password_hash"] != "mypassword"


def test_check_password():
    from appcore.users import check_password
    create_user("_test_carol_", "pass1")
    u = get_by_username("_test_carol_")
    assert check_password("pass1", u["password_hash"]) is True
    assert check_password("wrong", u["password_hash"]) is False


def test_get_by_id():
    create_user("_test_dan_", "x")
    u = get_by_username("_test_dan_")
    u2 = get_by_id(u["id"])
    assert u2["username"] == "_test_dan_"


def test_set_active():
    create_user("_test_eve_", "x")
    u = get_by_username("_test_eve_")
    set_active(u["id"], False)
    u2 = get_by_id(u["id"])
    assert u2["is_active"] == 0


def test_list_users_returns_list():
    result = list_users()
    assert isinstance(result, list)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_appcore_users.py -v
```
Expected: ImportError — `appcore.users` doesn't exist yet.

- [ ] **Step 3: Implement appcore/users.py**

Create `appcore/users.py`:
```python
from __future__ import annotations
import bcrypt
from appcore.db import query, query_one, execute


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_user(username: str, password: str, role: str = "user") -> int:
    pw_hash = hash_password(password)
    return execute(
        "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
        (username, pw_hash, role),
    )


def get_by_username(username: str) -> dict | None:
    return query_one("SELECT * FROM users WHERE username = %s", (username,))


def get_by_id(user_id: int) -> dict | None:
    return query_one("SELECT * FROM users WHERE id = %s", (user_id,))


def list_users() -> list[dict]:
    return query("SELECT id, username, role, is_active, created_at FROM users ORDER BY id")


def set_active(user_id: int, active: bool) -> None:
    execute("UPDATE users SET is_active = %s WHERE id = %s", (int(active), user_id))
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_appcore_users.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add appcore/users.py tests/test_appcore_users.py
git commit -m "feat: add appcore/users.py with bcrypt password hashing"
```

---

## Task 3: API Keys module

**Files:**
- Create: `appcore/api_keys.py`
- Create: `tests/test_appcore_api_keys.py`

- [ ] **Step 1: Write test first**

Create `tests/test_appcore_api_keys.py`:
```python
import pytest
from appcore.users import create_user
from appcore.api_keys import set_key, get_key, get_all
from appcore.db import execute, query_one


@pytest.fixture
def user_id():
    uid = create_user("_test_keys_user_", "x")
    yield uid
    execute("DELETE FROM api_keys WHERE user_id = %s", (uid,))
    execute("DELETE FROM users WHERE id = %s", (uid,))


def test_set_and_get_key(user_id):
    set_key(user_id, "openrouter", "sk-abc123")
    assert get_key(user_id, "openrouter") == "sk-abc123"


def test_get_key_missing_returns_none(user_id):
    assert get_key(user_id, "elevenlabs") is None


def test_set_key_upsert(user_id):
    set_key(user_id, "elevenlabs", "old-key")
    set_key(user_id, "elevenlabs", "new-key")
    assert get_key(user_id, "elevenlabs") == "new-key"


def test_get_all_returns_all_services(user_id):
    set_key(user_id, "openrouter", "k1")
    set_key(user_id, "elevenlabs", "k2")
    result = get_all(user_id)
    assert result["openrouter"]["key_value"] == "k1"
    assert result["elevenlabs"]["key_value"] == "k2"


def test_set_key_with_extra_config(user_id):
    set_key(user_id, "doubao_asr", "tok", extra={"app_id": "123", "cluster": "prod"})
    row = query_one("SELECT extra_config FROM api_keys WHERE user_id=%s AND service='doubao_asr'", (user_id,))
    import json
    extra = json.loads(row["extra_config"]) if isinstance(row["extra_config"], str) else row["extra_config"]
    assert extra["app_id"] == "123"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_appcore_api_keys.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement appcore/api_keys.py**

Create `appcore/api_keys.py`:
```python
from __future__ import annotations
import json
from appcore.db import query_one, execute, query


def set_key(user_id: int, service: str, key_value: str, extra: dict | None = None) -> None:
    extra_json = json.dumps(extra) if extra else None
    execute(
        """INSERT INTO api_keys (user_id, service, key_value, extra_config)
           VALUES (%s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE key_value = VALUES(key_value), extra_config = VALUES(extra_config)""",
        (user_id, service, key_value, extra_json),
    )


def get_key(user_id: int, service: str) -> str | None:
    row = query_one(
        "SELECT key_value FROM api_keys WHERE user_id = %s AND service = %s",
        (user_id, service),
    )
    return row["key_value"] if row else None


def get_all(user_id: int) -> dict[str, dict]:
    rows = query("SELECT service, key_value, extra_config FROM api_keys WHERE user_id = %s", (user_id,))
    result = {}
    for row in rows:
        extra = row["extra_config"]
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        result[row["service"]] = {"key_value": row["key_value"], "extra": extra or {}}
    return result
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_appcore_api_keys.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add appcore/api_keys.py tests/test_appcore_api_keys.py
git commit -m "feat: add appcore/api_keys.py — per-user API key storage"
```

---

## Task 4: Persist task_state to DB

**Files:**
- Modify: `appcore/task_state.py`
- Create: `tests/test_appcore_task_state_db.py`

The existing `task_state.py` uses an in-process dict. We add DB write-through: every mutation also writes `state_json` + indexed columns to the `projects` table. `get()` falls back to DB if the task isn't in memory (supports page reload / server restart).

- [ ] **Step 1: Write test first**

Create `tests/test_appcore_task_state_db.py`:
```python
"""Tests that task_state persists to and restores from DB."""
import pytest
import appcore.task_state as ts
from appcore.db import execute, query_one


@pytest.fixture(autouse=True)
def cleanup():
    yield
    execute("DELETE FROM projects WHERE id LIKE 'test_ts_%'")
    execute("DELETE FROM users WHERE username = '_test_ts_user_'")


@pytest.fixture
def user_id():
    from appcore.users import create_user
    return create_user("_test_ts_user_", "x")


def test_create_persists_to_db(user_id, tmp_path):
    task_id = "test_ts_001"
    ts.create(task_id, "/tmp/v.mp4", str(tmp_path), "v.mp4", user_id=user_id)
    row = query_one("SELECT * FROM projects WHERE id = %s", (task_id,))
    assert row is not None
    assert row["user_id"] == user_id
    assert row["status"] == "uploaded"
    assert row["expires_at"] is not None


def test_get_falls_back_to_db(user_id, tmp_path):
    task_id = "test_ts_002"
    ts.create(task_id, "/tmp/v.mp4", str(tmp_path), "v.mp4", user_id=user_id)
    # Remove from memory
    from appcore.task_state import _tasks
    _tasks.pop(task_id, None)
    # Should restore from DB
    task = ts.get(task_id)
    assert task is not None
    assert task["id"] == task_id


def test_set_step_updates_db(user_id, tmp_path):
    task_id = "test_ts_003"
    ts.create(task_id, "/tmp/v.mp4", str(tmp_path), "v.mp4", user_id=user_id)
    ts.set_step(task_id, "extract", "done")
    row = query_one("SELECT state_json FROM projects WHERE id = %s", (task_id,))
    import json
    state = json.loads(row["state_json"])
    assert state["steps"]["extract"] == "done"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_appcore_task_state_db.py -v
```
Expected: FAIL — `create()` doesn't accept `user_id` and doesn't write to DB.

- [ ] **Step 3: Modify appcore/task_state.py**

Read the file first, then make these changes:

**3a. Add imports at top of file:**
```python
import json
from datetime import datetime, timedelta
```

**3b. Change `create()` signature and add DB write:**
```python
def create(task_id: str, video_path: str, task_dir: str,
           original_filename: str | None = None,
           user_id: int | None = None) -> dict:
    task = {
        # ... existing dict contents unchanged ...
    }
    _tasks[task_id] = task
    # DB write-through
    if user_id is not None:
        _db_upsert(task_id, user_id, task, original_filename)
    return task
```

**3c. Add `_db_upsert()` helper at module level:**
```python
def _db_upsert(task_id: str, user_id: int, task: dict, original_filename: str | None = None) -> None:
    """Write or update the projects row for this task."""
    try:
        from appcore.db import execute as db_execute
        state_json = json.dumps(task, ensure_ascii=False, default=str)
        expires_at = datetime.now() + timedelta(hours=24)
        db_execute(
            """INSERT INTO projects (id, user_id, original_filename, status, task_dir, state_json, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                 status = VALUES(status),
                 state_json = VALUES(state_json),
                 task_dir = VALUES(task_dir)""",
            (task_id, user_id, original_filename,
             task.get("status", "uploaded"),
             task.get("task_dir", ""),
             state_json,
             expires_at.strftime("%Y-%m-%d %H:%M:%S")),
        )
    except Exception:
        pass  # DB errors never break pipeline
```

**3d. Add DB fallback to `get()`:**
```python
def get(task_id: str) -> dict | None:
    if task_id in _tasks:
        return _tasks[task_id]
    # Fall back to DB
    try:
        from appcore.db import query_one
        row = query_one("SELECT state_json, user_id FROM projects WHERE id = %s", (task_id,))
        if row and row.get("state_json"):
            task = json.loads(row["state_json"])
            task["_user_id"] = row["user_id"]
            _tasks[task_id] = task
            return task
    except Exception:
        pass
    return None
```

**3e. Add DB sync to mutation functions — add this one-liner at the end of `update()`, `set_step()`, `set_artifact()`, `set_preview_file()`, `set_variant_artifact()`, `set_variant_preview_file()`, `confirm_alignment()`, `confirm_segments()`:**
```python
    _sync_task_to_db(task_id)
```

**3f. Add `_sync_task_to_db()` helper:**
```python
def _sync_task_to_db(task_id: str) -> None:
    """Sync current in-memory state to DB state_json and status column."""
    task = _tasks.get(task_id)
    if not task:
        return
    user_id = task.get("_user_id")
    if user_id is None:
        return
    try:
        from appcore.db import execute as db_execute
        state_json = json.dumps(task, ensure_ascii=False, default=str)
        db_execute(
            "UPDATE projects SET state_json = %s, status = %s WHERE id = %s",
            (state_json, task.get("status", "uploaded"), task_id),
        )
    except Exception:
        pass
```

- [ ] **Step 4: Run all task_state tests**

```bash
pytest tests/test_appcore_task_state.py tests/test_appcore_task_state_db.py -v
```
Expected: all pass (existing 7 tests + 3 new DB tests).

- [ ] **Step 5: Commit**

```bash
git add appcore/task_state.py tests/test_appcore_task_state_db.py
git commit -m "feat: add DB write-through and restore to appcore/task_state"
```

---

## Task 5: Flask-Login auth blueprint

**Files:**
- Create: `web/auth.py`
- Create: `web/routes/auth.py`
- Create: `web/templates/login.html`
- Modify: `web/app.py`

- [ ] **Step 1: Create web/auth.py — Flask-Login User class**

Create `web/auth.py`:
```python
from __future__ import annotations
from flask_login import LoginManager, UserMixin
from appcore.users import get_by_id

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "请先登录"


class User(UserMixin):
    def __init__(self, row: dict):
        self.id = row["id"]
        self.username = row["username"]
        self.role = row["role"]
        self.is_active_flag = bool(row["is_active"])

    @property
    def is_active(self):
        return self.is_active_flag


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    row = get_by_id(int(user_id))
    if row and row["is_active"]:
        return User(row)
    return None


def admin_required(f):
    """Decorator: require admin role. Use after @login_required."""
    from functools import wraps
    from flask import abort
    from flask_login import current_user
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated
```

- [ ] **Step 2: Create web/routes/auth.py**

Create `web/routes/auth.py`:
```python
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from appcore.users import get_by_username, check_password
from web.auth import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = get_by_username(username)
        if row and row["is_active"] and check_password(password, row["password_hash"]):
            login_user(User(row), remember=True)
            return redirect(url_for("projects.index"))
        flash("用户名或密码错误")
    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
```

- [ ] **Step 3: Create web/templates/login.html**

Create `web/templates/login.html`:
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>登录 — AutoVideoSrt</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: "Segoe UI","PingFang SC",sans-serif; background: #0c0d10; color: #edf0f6; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .card { background: #17191f; border: 1px solid #282c36; border-radius: 16px; padding: 36px 32px; width: 360px; }
    h1 { font-size: 22px; margin-bottom: 24px; }
    label { display: block; color: #aab2c0; font-size: 12px; text-transform: uppercase; margin-bottom: 6px; margin-top: 16px; }
    input { width: 100%; background: #22262f; border: 1px solid #343946; border-radius: 10px; color: #eff2f8; padding: 10px 12px; font-size: 14px; }
    .btn { width: 100%; margin-top: 24px; background: #fe2c55; color: white; border: none; border-radius: 10px; padding: 12px; font-size: 15px; font-weight: 700; cursor: pointer; }
    .error { color: #fe2c55; font-size: 13px; margin-top: 12px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>AutoVideoSrt</h1>
    {% with messages = get_flashed_messages() %}
      {% if messages %}<p class="error">{{ messages[0] }}</p>{% endif %}
    {% endwith %}
    <form method="post">
      <label>用户名</label>
      <input type="text" name="username" autofocus required>
      <label>密码</label>
      <input type="password" name="password" required>
      <button class="btn" type="submit">登录</button>
    </form>
  </div>
</body>
</html>
```

- [ ] **Step 4: Register auth blueprint and Flask-Login in web/app.py**

Modify `web/app.py`:
```python
from web.auth import login_manager
from web.routes.auth import bp as auth_bp

def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

    login_manager.init_app(app)
    socketio.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(task_bp)
    app.register_blueprint(voice_bp)
    # (projects, settings, admin blueprints added in later tasks)

    @app.route("/")
    def index():
        return render_template("index.html")   # temporary, replaced in Task 7

    @socketio.on("join_task")
    def on_join(data):
        task_id = data.get("task_id")
        if task_id:
            join_room(task_id)

    return app
```

- [ ] **Step 5: Create first admin user via script**

Create `db/create_admin.py`:
```python
"""Run once to create the initial admin user."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv; load_dotenv()
from appcore.users import create_user, get_by_username

username = "admin"
password = "admin123"   # change after first login
if get_by_username(username):
    print(f"User '{username}' already exists.")
else:
    create_user(username, password, role="admin")
    print(f"Admin user '{username}' created. Password: {password}")
```

- [ ] **Step 6: Test login flow manually**

```bash
python db/create_admin.py
python main.py
# Open http://localhost:5000/login
# Login with admin / admin123 — should redirect to /
# Visit /logout — should redirect to /login
```

- [ ] **Step 7: Commit**

```bash
git add web/auth.py web/routes/auth.py web/templates/login.html web/app.py db/create_admin.py
git commit -m "feat: add Flask-Login auth — login/logout with admin decorator"
```

---

## Task 6: Base layout + project list page

**Files:**
- Create: `web/templates/layout.html`
- Create: `web/templates/projects.html`
- Create: `web/routes/projects.py`
- Modify: `web/app.py`

- [ ] **Step 1: Create layout.html base template**

Create `web/templates/layout.html`:
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}AutoVideoSrt{% endblock %}</title>
  <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: "Segoe UI","PingFang SC",sans-serif; background: #0c0d10; color: #edf0f6; min-height: 100vh; }
    nav { background: #13141a; border-bottom: 1px solid #1f222b; padding: 0 24px; height: 52px; display: flex; align-items: center; gap: 24px; }
    nav .brand { font-weight: 700; font-size: 16px; color: #fe2c55; text-decoration: none; }
    nav a { color: #aab2c0; text-decoration: none; font-size: 14px; }
    nav a:hover, nav a.active { color: #edf0f6; }
    nav .spacer { flex: 1; }
    nav .user { color: #98a0af; font-size: 13px; }
    .container { max-width: 1100px; margin: 0 auto; padding: 28px 18px 48px; }
    .btn { border: none; border-radius: 10px; padding: 10px 16px; font-size: 14px; font-weight: 700; cursor: pointer; }
    .btn-primary { background: #fe2c55; color: white; }
    .btn-sm { padding: 6px 12px; font-size: 13px; }
    .btn-ghost { background: transparent; border: 1px solid #343946; color: #aab2c0; }
    {% block extra_style %}{% endblock %}
  </style>
</head>
<body>
  <nav>
    <a class="brand" href="{{ url_for('projects.index') }}">AutoVideoSrt</a>
    <a href="{{ url_for('projects.index') }}" {% if request.endpoint == 'projects.index' %}class="active"{% endif %}>项目</a>
    <a href="{{ url_for('settings.index') }}" {% if request.endpoint == 'settings.index' %}class="active"{% endif %}>API 配置</a>
    {% if current_user.role == 'admin' %}
    <a href="{{ url_for('admin.users') }}" {% if 'admin' in request.endpoint %}class="active"{% endif %}>用户管理</a>
    {% endif %}
    <span class="spacer"></span>
    <span class="user">{{ current_user.username }}</span>
    <a href="{{ url_for('auth.logout') }}">退出</a>
  </nav>
  <div class="container">
    {% block content %}{% endblock %}
  </div>
  {% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Step 2: Create web/routes/projects.py**

Create `web/routes/projects.py`:
```python
from __future__ import annotations
import json
from flask import Blueprint, render_template, abort
from flask_login import login_required, current_user
from appcore.db import query, query_one

bp = Blueprint("projects", __name__)


@bp.route("/")
@login_required
def index():
    rows = query(
        """SELECT id, original_filename, thumbnail_path, status, created_at, expires_at, deleted_at
           FROM projects WHERE user_id = %s ORDER BY created_at DESC""",
        (current_user.id,),
    )
    return render_template("projects.html", projects=rows)


@bp.route("/projects/<task_id>")
@login_required
def detail(task_id: str):
    row = query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)
    state = {}
    if row.get("state_json"):
        try:
            state = json.loads(row["state_json"])
        except Exception:
            pass
    return render_template("project_detail.html", project=row, state=state)
```

- [ ] **Step 3: Create web/templates/projects.html**

Create `web/templates/projects.html`:
```html
{% extends "layout.html" %}
{% block title %}我的项目 — AutoVideoSrt{% endblock %}
{% block extra_style %}
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
.page-header h1 { font-size: 22px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
.project-card { background: #17191f; border: 1px solid #282c36; border-radius: 14px; overflow: hidden; text-decoration: none; color: inherit; display: block; transition: border-color .15s; }
.project-card:hover { border-color: #fe2c55; }
.project-card .thumb { width: 100%; height: 148px; object-fit: cover; background: #0e0f13; display: flex; align-items: center; justify-content: center; color: #3a3f4d; font-size: 32px; }
.project-card .thumb img { width: 100%; height: 148px; object-fit: cover; }
.project-card .info { padding: 14px; }
.project-card .filename { font-size: 14px; font-weight: 600; margin-bottom: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.project-card .meta { font-size: 12px; color: #98a0af; display: flex; justify-content: space-between; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 600; }
.badge-done { background: #1a3a2a; color: #4ade80; }
.badge-running { background: #3a1a1a; color: #fe2c55; }
.badge-expired { background: #222; color: #666; }
.badge-uploaded { background: #1a2a3a; color: #60a5fa; }
.empty { text-align: center; padding: 80px 0; color: #3a3f4d; }
.new-btn { background: #fe2c55; color: white; border: none; border-radius: 10px; padding: 10px 20px; font-size: 14px; font-weight: 700; cursor: pointer; text-decoration: none; }
{% endblock %}
{% block content %}
<div class="page-header">
  <h1>我的项目</h1>
  <a href="{{ url_for('task.upload_page') }}" class="new-btn">+ 新建项目</a>
</div>
{% if projects %}
<div class="grid">
  {% for p in projects %}
  <a class="project-card" href="{{ url_for('projects.detail', task_id=p.id) }}">
    <div class="thumb">
      {% if p.thumbnail_path %}
        <img src="/api/tasks/{{ p.id }}/thumbnail" alt="">
      {% else %}
        🎬
      {% endif %}
    </div>
    <div class="info">
      <div class="filename">{{ p.original_filename or p.id }}</div>
      <div class="meta">
        <span class="badge badge-{{ p.status }}">{{ p.status }}</span>
        <span>{{ p.created_at.strftime('%m-%d %H:%M') if p.created_at else '' }}</span>
      </div>
    </div>
  </a>
  {% endfor %}
</div>
{% else %}
<div class="empty">
  <p style="font-size:48px;margin-bottom:16px">🎬</p>
  <p>还没有项目，点击右上角新建</p>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Create web/templates/project_detail.html**

Create `web/templates/project_detail.html`:
```html
{% extends "layout.html" %}
{% block title %}{{ project.original_filename }} — AutoVideoSrt{% endblock %}
{% block extra_style %}
.back { color: #98a0af; text-decoration: none; font-size: 13px; display: inline-block; margin-bottom: 16px; }
.back:hover { color: #edf0f6; }
.proj-header { margin-bottom: 24px; }
.proj-header h1 { font-size: 20px; margin-bottom: 6px; }
.proj-meta { font-size: 13px; color: #98a0af; }
.steps { display: flex; flex-direction: column; gap: 12px; }
.step-card { background: #17191f; border: 1px solid #282c36; border-radius: 14px; padding: 16px; }
.step-card h3 { font-size: 14px; margin-bottom: 10px; color: #aab2c0; }
.artifact-text { background: #0e0f13; border-radius: 8px; padding: 12px; font-size: 13px; white-space: pre-wrap; color: #c8d0de; max-height: 300px; overflow-y: auto; }
.download-link { display: inline-block; margin-top: 8px; color: #fe2c55; font-size: 13px; text-decoration: none; }
.download-link:hover { text-decoration: underline; }
.expired-notice { background: #1a1a1a; border: 1px solid #333; border-radius: 12px; padding: 32px; text-align: center; color: #666; }
{% endblock %}
{% block content %}
<a class="back" href="{{ url_for('projects.index') }}">← 返回项目列表</a>
<div class="proj-header">
  <h1>{{ project.original_filename or project.id }}</h1>
  <div class="proj-meta">
    创建时间: {{ project.created_at }} &nbsp;|&nbsp;
    状态: {{ project.status }}
    {% if project.expires_at and not project.deleted_at %}
      &nbsp;|&nbsp; 过期时间: {{ project.expires_at }}
    {% endif %}
  </div>
</div>

{% if project.deleted_at %}
<div class="expired-notice">
  <p style="font-size:32px;margin-bottom:12px">🗑</p>
  <p>该项目已过期，文件已清理。</p>
</div>
{% else %}
<div class="steps">
  {% set step_labels = [
    ('extract','音频提取'), ('asr','语音识别'), ('alignment','分段对齐'),
    ('translate','本土化翻译'), ('tts','英文配音'), ('subtitle','字幕生成'),
    ('compose','视频合成'), ('export','CapCut 导出')
  ] %}
  {% for step_id, step_name in step_labels %}
  {% set step_status = state.get('steps', {}).get(step_id, 'pending') %}
  {% if step_status in ['done','error'] %}
  <div class="step-card">
    <h3>{{ step_name }} — {{ step_status }}</h3>
    {% set artifact = state.get('artifacts', {}).get(step_id) %}
    {% if artifact %}
      {% if artifact.get('items') %}
        {% for item in artifact['items'][:2] %}
          {% if item.type == 'text' and item.get('content') %}
            <div class="artifact-text">{{ item.content[:800] }}</div>
          {% endif %}
        {% endfor %}
      {% endif %}
    {% endif %}
    {% if step_id in ['compose','export'] %}
      <a class="download-link" href="/api/tasks/{{ project.id }}/artifact/soft_video?variant=normal" download>⬇ 下载软字幕视频 (普通版)</a><br>
      <a class="download-link" href="/api/tasks/{{ project.id }}/artifact/soft_video?variant=hook_cta" download>⬇ 下载软字幕视频 (hook版)</a><br>
      <a class="download-link" href="/api/tasks/{{ project.id }}/artifact/srt?variant=normal" download>⬇ 下载 SRT (普通版)</a>
    {% endif %}
  </div>
  {% endif %}
  {% endfor %}
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Register projects blueprint and add upload_page route**

Modify `web/app.py` — add to imports and registration:
```python
from web.routes.projects import bp as projects_bp
# in create_app():
app.register_blueprint(projects_bp)
# remove the existing @app.route("/") index route — projects blueprint handles /
```

Add a named `upload_page` route to `web/routes/task.py` (needed for `url_for('task.upload_page')`):
```python
@bp.route("/upload-page", endpoint="upload_page")
@login_required
def upload_page():
    return render_template("index.html")
```

- [ ] **Step 6: Test manually**

```bash
python main.py
# Visit http://localhost:5000/ — should redirect to /login if not logged in
# Login as admin → should see empty project list
# Click "+ 新建项目" → should go to /upload-page (existing index.html)
```

- [ ] **Step 7: Commit**

```bash
git add web/routes/projects.py web/templates/layout.html web/templates/projects.html web/templates/project_detail.html web/app.py web/routes/task.py
git commit -m "feat: add project list and detail pages with base layout"
```

---

## Task 7: Settings page (API Key configuration)

**Files:**
- Create: `web/routes/settings.py`
- Create: `web/templates/settings.html`
- Modify: `web/app.py`

- [ ] **Step 1: Create web/routes/settings.py**

Create `web/routes/settings.py`:
```python
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from appcore.api_keys import set_key, get_all

bp = Blueprint("settings", __name__)

SERVICES = [
    ("doubao_asr", "豆包 ASR", ["key_value", "app_id", "cluster"]),
    ("elevenlabs", "ElevenLabs", ["key_value"]),
    ("openrouter", "OpenRouter", ["key_value"]),
]


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        for service, _, fields in SERVICES:
            key_value = request.form.get(f"{service}_key", "").strip()
            if key_value:
                extra = {}
                for f in fields[1:]:  # extra fields beyond key_value
                    val = request.form.get(f"{service}_{f}", "").strip()
                    if val:
                        extra[f] = val
                set_key(current_user.id, service, key_value, extra or None)
        flash("API Key 已保存")
        return redirect(url_for("settings.index"))
    keys = get_all(current_user.id)
    return render_template("settings.html", keys=keys, services=SERVICES)
```

- [ ] **Step 2: Create web/templates/settings.html**

Create `web/templates/settings.html`:
```html
{% extends "layout.html" %}
{% block title %}API 配置 — AutoVideoSrt{% endblock %}
{% block extra_style %}
.settings-card { background: #17191f; border: 1px solid #282c36; border-radius: 14px; padding: 24px; margin-bottom: 16px; }
.settings-card h2 { font-size: 15px; margin-bottom: 16px; }
label { display: block; color: #aab2c0; font-size: 12px; text-transform: uppercase; margin-bottom: 6px; margin-top: 14px; }
input[type=text], input[type=password] { width: 100%; background: #22262f; border: 1px solid #343946; border-radius: 10px; color: #eff2f8; padding: 10px 12px; font-size: 14px; }
.save-btn { margin-top: 24px; background: #fe2c55; color: white; border: none; border-radius: 10px; padding: 10px 24px; font-size: 14px; font-weight: 700; cursor: pointer; }
.success { color: #4ade80; font-size: 13px; margin-bottom: 16px; }
.hint { color: #98a0af; font-size: 12px; margin-top: 4px; }
{% endblock %}
{% block content %}
<h1 style="font-size:20px;margin-bottom:20px">API Key 配置</h1>
{% with messages = get_flashed_messages() %}
  {% if messages %}<p class="success">{{ messages[0] }}</p>{% endif %}
{% endwith %}
<form method="post">
  <div class="settings-card">
    <h2>豆包 ASR（火山引擎语音识别）</h2>
    <label>API Key</label>
    <input type="password" name="doubao_asr_key" placeholder="留空则使用系统默认"
           value="{{ keys.get('doubao_asr', {}).get('key_value', '') }}">
    <label>App ID</label>
    <input type="text" name="doubao_asr_app_id" placeholder="可选"
           value="{{ keys.get('doubao_asr', {}).get('extra', {}).get('app_id', '') }}">
    <label>Cluster</label>
    <input type="text" name="doubao_asr_cluster" placeholder="可选，默认 volc.seedasr.auc"
           value="{{ keys.get('doubao_asr', {}).get('extra', {}).get('cluster', '') }}">
  </div>
  <div class="settings-card">
    <h2>ElevenLabs（TTS 配音）</h2>
    <label>API Key</label>
    <input type="password" name="elevenlabs_key" placeholder="留空则使用系统默认"
           value="{{ keys.get('elevenlabs', {}).get('key_value', '') }}">
  </div>
  <div class="settings-card">
    <h2>OpenRouter（翻译 / TTS 文案）</h2>
    <label>API Key</label>
    <input type="password" name="openrouter_key" placeholder="留空则使用系统默认"
           value="{{ keys.get('openrouter', {}).get('key_value', '') }}">
    <p class="hint">OpenRouter 用于调用翻译和 TTS 文案生成模型</p>
  </div>
  <button class="save-btn" type="submit">保存配置</button>
</form>
{% endblock %}
```

- [ ] **Step 3: Register settings blueprint in web/app.py**

Add to `web/app.py`:
```python
from web.routes.settings import bp as settings_bp
# in create_app():
app.register_blueprint(settings_bp)
```

- [ ] **Step 4: Test manually**

```bash
python main.py
# Login → click "API 配置" in nav
# Enter a test key for openrouter → Save → should show "API Key 已保存"
# Reload page → key value should pre-fill
```

- [ ] **Step 5: Commit**

```bash
git add web/routes/settings.py web/templates/settings.html web/app.py
git commit -m "feat: add API key settings page per user"
```

---

## Task 8: Admin user management page

**Files:**
- Create: `web/routes/admin.py`
- Create: `web/templates/admin_users.html`
- Modify: `web/app.py`

- [ ] **Step 1: Create web/routes/admin.py**

Create `web/routes/admin.py`:
```python
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from web.auth import admin_required
from appcore.users import list_users, create_user, set_active, get_by_username

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/users", methods=["GET", "POST"])
@login_required
@admin_required
def users():
    error = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            role = request.form.get("role", "user")
            if not username or not password:
                error = "用户名和密码不能为空"
            elif get_by_username(username):
                error = f"用户名 '{username}' 已存在"
            else:
                create_user(username, password, role=role)
                flash(f"用户 '{username}' 创建成功")
                return redirect(url_for("admin.users"))
        elif action == "toggle_active":
            user_id = int(request.form.get("user_id"))
            active = request.form.get("active") == "1"
            set_active(user_id, active)
            return redirect(url_for("admin.users"))
    all_users = list_users()
    return render_template("admin_users.html", users=all_users, error=error)
```

- [ ] **Step 2: Create web/templates/admin_users.html**

Create `web/templates/admin_users.html`:
```html
{% extends "layout.html" %}
{% block title %}用户管理 — AutoVideoSrt{% endblock %}
{% block extra_style %}
.admin-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; color: #98a0af; font-size: 12px; text-transform: uppercase; padding: 8px 12px; border-bottom: 1px solid #282c36; }
td { padding: 12px; border-bottom: 1px solid #1f222b; font-size: 14px; }
.create-card { background: #17191f; border: 1px solid #282c36; border-radius: 14px; padding: 20px; margin-bottom: 24px; }
.create-card h2 { font-size: 15px; margin-bottom: 16px; }
.form-row { display: grid; grid-template-columns: 1fr 1fr 120px 120px; gap: 12px; align-items: end; }
label { display: block; color: #aab2c0; font-size: 12px; margin-bottom: 6px; }
input[type=text],input[type=password],select { width: 100%; background: #22262f; border: 1px solid #343946; border-radius: 8px; color: #eff2f8; padding: 9px 10px; font-size: 14px; }
.error { color: #fe2c55; font-size: 13px; margin-bottom: 12px; }
.success-msg { color: #4ade80; font-size: 13px; margin-bottom: 12px; }
{% endblock %}
{% block content %}
<div class="admin-header"><h1 style="font-size:20px">用户管理</h1></div>

<div class="create-card">
  <h2>创建新用户</h2>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  {% with messages = get_flashed_messages() %}
    {% if messages %}<p class="success-msg">{{ messages[0] }}</p>{% endif %}
  {% endwith %}
  <form method="post">
    <input type="hidden" name="action" value="create">
    <div class="form-row">
      <div><label>用户名</label><input type="text" name="username" required></div>
      <div><label>密码</label><input type="password" name="password" required></div>
      <div><label>角色</label>
        <select name="role">
          <option value="user">普通用户</option>
          <option value="admin">管理员</option>
        </select>
      </div>
      <div style="padding-top:18px"><button class="btn btn-primary" type="submit">创建</button></div>
    </div>
  </form>
</div>

<table>
  <thead><tr><th>ID</th><th>用户名</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>
  <tbody>
    {% for u in users %}
    <tr>
      <td>{{ u.id }}</td>
      <td>{{ u.username }}</td>
      <td>{{ u.role }}</td>
      <td>{{ '正常' if u.is_active else '已禁用' }}</td>
      <td>{{ u.created_at }}</td>
      <td>
        <form method="post" style="display:inline">
          <input type="hidden" name="action" value="toggle_active">
          <input type="hidden" name="user_id" value="{{ u.id }}">
          <input type="hidden" name="active" value="{{ '0' if u.is_active else '1' }}">
          <button class="btn btn-ghost btn-sm" type="submit">{{ '禁用' if u.is_active else '启用' }}</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 3: Register admin blueprint in web/app.py**

```python
from web.routes.admin import bp as admin_bp
# in create_app():
app.register_blueprint(admin_bp)
```

- [ ] **Step 4: Test manually**

```bash
python main.py
# Login as admin → click "用户管理"
# Create a new user "testuser" with password "test123"
# Login as testuser → nav should NOT show "用户管理"
# Visit /admin/users as testuser → should get 403
```

- [ ] **Step 5: Commit**

```bash
git add web/routes/admin.py web/templates/admin_users.html web/app.py
git commit -m "feat: add admin user management page"
```

---

## Task 9: Wire user_id through pipeline + thumbnail generation

**Files:**
- Modify: `web/routes/task.py`
- Modify: `web/services/pipeline_runner.py`
- Modify: `appcore/runtime.py` (pass user_id to task_state.create)

- [ ] **Step 1: Pass user_id to pipeline_runner.start()**

Modify `web/services/pipeline_runner.py` — change `start()` signature:
```python
def start(task_id: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = PipelineRunner(bus=bus)
    thread = threading.Thread(target=runner.run, args=(task_id,), daemon=True)
    thread.start()
```

- [ ] **Step 2: Pass current_user.id from upload route**

In `web/routes/task.py`, find the `upload()` route where `store.create()` and `pipeline_runner.start()` are called. Modify:
```python
from flask_login import current_user

# in upload():
user_id = current_user.id if current_user.is_authenticated else None
store.create(task_id, video_path, task_dir,
             original_filename=os.path.basename(file.filename),
             user_id=user_id)
# ...
pipeline_runner.start(task_id, user_id=user_id)
```

Add `@login_required` decorator to the `upload()` route.

- [ ] **Step 3: Generate thumbnail on upload**

Add thumbnail extraction to `web/routes/task.py` `upload()` function after saving the video:
```python
import subprocess

def _extract_thumbnail(video_path: str, task_dir: str) -> str | None:
    """Extract first frame as JPEG. Returns path or None on failure."""
    try:
        thumb_path = os.path.join(task_dir, "thumbnail.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vframes", "1",
             "-f", "image2", thumb_path],
            capture_output=True, timeout=15,
        )
        if os.path.exists(thumb_path):
            return thumb_path
    except Exception:
        pass
    return None
```

After `store.create()`, call:
```python
thumb = _extract_thumbnail(video_path, task_dir)
if thumb:
    from appcore.db import execute as db_execute
    db_execute("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (thumb, task_id))
```

- [ ] **Step 4: Add thumbnail serve endpoint**

Add to `web/routes/task.py`:
```python
@bp.route("/<task_id>/thumbnail")
@login_required
def thumbnail(task_id: str):
    from appcore.db import query_one as db_query_one
    row = db_query_one(
        "SELECT thumbnail_path FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row or not row.get("thumbnail_path") or not os.path.exists(row["thumbnail_path"]):
        abort(404)
    return send_file(row["thumbnail_path"], mimetype="image/jpeg")
```

- [ ] **Step 5: Add @login_required to existing task routes**

In `web/routes/task.py`, add `@login_required` to all existing `@bp.route` endpoints (upload, start, confirm_alignment, confirm_segments, artifact, etc.).

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -v --ignore=tests/test_appcore_db.py
```
Expected: 88+ passed.

- [ ] **Step 7: Commit**

```bash
git add web/routes/task.py web/services/pipeline_runner.py
git commit -m "feat: wire user_id through pipeline, add thumbnail generation"
```

---

## Task 10: Deploy to server port 80

**Files:**
- Create: `deploy/setup.sh`
- Create: `deploy/autovideosrt.service`
- Modify: `.env` on server (manual)

- [ ] **Step 1: Create systemd service file**

Create `deploy/autovideosrt.service`:
```ini
[Unit]
Description=AutoVideoSrt Web Service
After=network.target

[Service]
User=root
WorkingDirectory=/opt/autovideosrt
Environment="PATH=/opt/autovideosrt/venv/bin"
ExecStart=/opt/autovideosrt/venv/bin/gunicorn -w 1 -k eventlet --bind 0.0.0.0:80 --timeout 300 main:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create deploy/setup.sh**

Create `deploy/setup.sh`:
```bash
#!/bin/bash
set -e
APP_DIR=/opt/autovideosrt

# Pull latest code
cd $APP_DIR
git pull

# Install/update deps
source venv/bin/activate
pip install -r requirements.txt

# Run DB migration
python db/migrate.py

# Create admin user if not exists
python db/create_admin.py

# Restart service
systemctl restart autovideosrt
systemctl status autovideosrt --no-pager
echo "Deploy complete. Running on port 80."
```

- [ ] **Step 3: First-time server setup**

SSH to server and run:
```bash
ssh -i C:\Users\admin\.ssh\CC.pem root@172.30.254.14

# On server:
mkdir -p /opt/autovideosrt
cd /opt/autovideosrt
git clone https://github.com/jinghuaswsx/AutoVideoSrtLocal.git .
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env
cat > .env << 'EOF'
FLASK_SECRET_KEY=change-this-to-random-string
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=auto_video
DB_USER=root
DB_PASSWORD=<server-managed-password>
OUTPUT_DIR=/opt/autovideosrt/output
UPLOAD_DIR=/opt/autovideosrt/uploads
# System fallback API keys:
VOLC_API_KEY=...
OPENROUTER_API_KEY=...
ELEVENLABS_API_KEY=...
TOS_ACCESS_KEY=...
TOS_SECRET_KEY=...
EOF

mkdir -p output uploads
python db/migrate.py
python db/create_admin.py

cp deploy/autovideosrt.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable autovideosrt
systemctl start autovideosrt
```

- [ ] **Step 4: Verify deployment**

```bash
# From server:
systemctl status autovideosrt
curl http://localhost/login
```

Expected: HTML response with login page.

- [ ] **Step 5: Test from browser**

Open `http://172.30.254.14` in browser.
Login as `admin` / `admin123`.
Verify project list page loads.

- [ ] **Step 6: Commit deploy scripts**

```bash
git add deploy/
git commit -m "chore: add deploy scripts and systemd service for port 80"
git push
```

---

## Self-Review

**Spec coverage check:**
- ✅ User login (Task 5)
- ✅ Admin creates users, admin/user roles (Tasks 5, 8)
- ✅ Admin user management page (Task 8)
- ✅ User API key config: doubao_asr, elevenlabs, openrouter (Tasks 3, 7)
- ✅ Project persistence to DB (Task 4)
- ✅ Project list with thumbnails (Tasks 6, 9)
- ✅ Project detail read-only view (Task 6)
- ✅ user_id wired through pipeline (Task 9)
- ✅ Deploy to port 80 (Task 10)
- ⏭ TOS upload/download → Plan B
- ⏭ 24h expiry cleanup → Plan B
- ⏭ Usage logging → Plan B

**Type consistency:**
- `create_user()` returns `int` (lastrowid) — used as `user_id` fixture in tests ✅
- `get_by_id()` / `get_by_username()` return `dict | None` — consistent ✅
- `task_state.create()` new param `user_id: int | None = None` — backwards compatible ✅
- `pipeline_runner.start(task_id, user_id)` — added optional param ✅

**Note:** `appcore/runtime.py` has a method called `run()` in the plan's `PipelineRunner` — verify actual method name is `start()` vs `run()` before Task 9. Check `appcore/runtime.py` line ~56.
