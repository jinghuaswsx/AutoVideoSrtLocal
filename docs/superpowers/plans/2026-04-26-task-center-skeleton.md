# Task Center Skeleton (C 子系统) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Task Center skeleton — `tasks` + `task_events` tables, role-based task pool with claim semantics, dual audits, owner-cascade, readiness-gate completion, cancel terminal state. Manual maintenance mode (no upstream selection auto-feed; that's A 子系统).

**Architecture:** 双层任务模型（父=素材级 / 子=国家级）单表 + parent_task_id 区分；状态机靠 service 层守护；权限走现有 `users.permissions` JSON + 加 2 个能力位；UI 单页多 Tab（参考素材管理列表风格）；与素材管理双窗口协作（不在任务中心内嵌完整翻译能力）。

**Tech Stack:** Python 3 / Flask Blueprint / MySQL（PyMySQL）/ Jinja2 / Vanilla JS / pytest。

**Spec:** [docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md](../specs/2026-04-26-task-center-skeleton-design.md)

---

## File Structure

### New files
| 路径 | 责任 |
|---|---|
| `db/migrations/2026_04_26_add_tasks_tables.sql` | 建 `tasks` + `task_events` 两张表 |
| `appcore/tasks.py` | service 层：状态机、所有 task 动作、owner-cascade、event 写入 |
| `web/routes/tasks.py` | Flask Blueprint `tasks`，前缀 `/tasks`，所有 API + 主页路由 |
| `web/templates/tasks_list.html` | 主页模板（3 Tab + 创建 modal + 详情抽屉 + 打回/取消 modal） |
| `tests/test_db_migration_tasks_tables.py` | migration smoke test（参考 `test_db_migration_*.py` 风格） |
| `tests/test_appcore_tasks.py` | service 层单元测试（状态机、cascade、readiness gate） |
| `tests/test_tasks_routes.py` | API 集成测试（参考 `test_multi_translate_routes.py`） |

### Modified files
| 路径 | 修改 |
|---|---|
| `appcore/permissions.py` | 加 3 个 permission：`task_center` (菜单), `can_process_raw_video`, `can_translate` (能力)；新建 `GROUP_CAPABILITY` |
| `appcore/medias.py` | `update_product_owner()` 末尾加 owner-cascade 钩子调用 |
| `web/app.py` | 注册新 Blueprint `tasks_bp` |
| `web/templates/layout.html` | 加"任务中心"菜单项 |

---

## Task 索引

| # | 标题 | Phase |
|---|---|---|
| 1 | Migration: tasks + task_events 表 | Foundation |
| 2 | Permissions: 加 3 个 capability codes | Foundation |
| 3 | tasks.py 骨架 + 共用 helpers | Service |
| 4 | create_parent_task | Service |
| 5 | claim_parent | Service |
| 6 | mark_uploaded | Service |
| 7 | approve_raw + 自动 unblock children | Service |
| 8 | reject_raw | Service |
| 9 | cancel_parent + 级联 | Service |
| 10 | submit_child + readiness gate | Service |
| 11 | approve_child + 自动 all_done | Service |
| 12 | reject_child | Service |
| 13 | cancel_child | Service |
| 14 | on_product_owner_changed cascade + 钩子集成 | Service |
| 15 | Blueprint 骨架 + 主页路由 + 注册 | API |
| 16 | GET /api/list 任务列表 | API |
| 17 | GET /api/dispatch_pool 待派单 | API |
| 18 | POST /api/parent 创建 | API |
| 19 | 父任务动作端点（claim / upload_done / approve / reject / cancel / bind_item） | API |
| 20 | 子任务动作端点（submit / approve / reject / cancel） | API |
| 21 | GET /api/<id>/events 审计流 | API |
| 22 | layout.html 加菜单项 | Frontend |
| 23 | tasks_list.html 页面骨架 + Tab 切换 + 全局筛选 | Frontend |
| 24 | "我的任务" / "全部任务" Tab 表格 | Frontend |
| 25 | "待派单素材" Tab 表格 + 创建按钮 | Frontend |
| 26 | 创建任务 modal | Frontend |
| 27 | 任务详情抽屉 + 操作按钮 + 审计流 | Frontend |
| 28 | 打回 / 取消 modals | Frontend |
| 29 | 父任务"已上传"跳转流（best effort） | Integration |
| 30 | 最终回归 + 手动验收清单走查 | Verify |

---

## Conventions Used Throughout

- **测试运行命令**：`cd g:/Code/AutoVideoSrtLocal/.worktrees/task-center && python -m pytest <path> -q 2>&1 | tail -20`
- **commit 格式**（参考 git log）：`<type>(<scope>): <subject>`，type ∈ {feat, fix, docs, refactor, test, chore}；scope 用 `task-center`
- **国家代码 ISO 大写常量**：`SUPPORTED_COUNTRIES = ('DE','FR','JA','NL','SV','FI')`（实施时 grep `media_languages.lang` 确认大小写——若现有数据是小写则全部下行 lower()）
- **DB 连接**：用 `appcore.db.get_conn()` / `query_one()` / `execute()`，参考 `appcore/medias.py` 风格
- **JSON 序列化** datetime：`val.isoformat() if val else None`

---

## Phase 1 — Foundation

### Task 1: Migration — `tasks` + `task_events` 表

**Files:**
- Create: `db/migrations/2026_04_26_add_tasks_tables.sql`
- Test: `tests/test_db_migration_tasks_tables.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_migration_tasks_tables.py
"""Smoke test for tasks tables migration."""
from pathlib import Path


def test_migration_file_exists_and_has_required_tables():
    sql = Path("db/migrations/2026_04_26_add_tasks_tables.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE TABLE" in sql and "tasks" in sql
    assert "CREATE TABLE" in sql and "task_events" in sql


def test_migration_has_required_columns_on_tasks():
    sql = Path("db/migrations/2026_04_26_add_tasks_tables.sql").read_text(
        encoding="utf-8"
    )
    for col in (
        "parent_task_id", "media_product_id", "media_item_id",
        "country_code", "assignee_id", "status", "last_reason",
        "created_by", "claimed_at", "completed_at", "cancelled_at",
    ):
        assert col in sql, f"missing column {col} in tasks DDL"


def test_migration_has_unique_index_for_country_per_parent():
    sql = Path("db/migrations/2026_04_26_add_tasks_tables.sql").read_text(
        encoding="utf-8"
    )
    assert "UNIQUE KEY" in sql and "uk_parent_country" in sql


def test_migration_has_required_columns_on_task_events():
    sql = Path("db/migrations/2026_04_26_add_tasks_tables.sql").read_text(
        encoding="utf-8"
    )
    for col in ("task_id", "event_type", "actor_user_id", "payload_json"):
        assert col in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db_migration_tasks_tables.py -q`
Expected: FAIL with "FileNotFoundError" / file not found.

- [ ] **Step 3: Create the migration SQL**

```sql
-- db/migrations/2026_04_26_add_tasks_tables.sql
-- 任务中心骨架（C 子系统）
-- - tasks: 双层任务模型（父=素材级 / 子=国家级），单表 + parent_task_id 区分
-- - task_events: 审计流，未来 F 子系统的统计基础
-- 详见 docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md

CREATE TABLE IF NOT EXISTS tasks (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  parent_task_id   INT DEFAULT NULL,
  media_product_id INT NOT NULL,
  media_item_id    INT DEFAULT NULL,
  country_code     VARCHAR(8) DEFAULT NULL,
  assignee_id      INT DEFAULT NULL,
  status           VARCHAR(24) NOT NULL,
  last_reason      TEXT DEFAULT NULL,
  created_by       INT NOT NULL,
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  claimed_at       DATETIME DEFAULT NULL,
  completed_at     DATETIME DEFAULT NULL,
  cancelled_at     DATETIME DEFAULT NULL,
  KEY idx_parent (parent_task_id),
  KEY idx_product (media_product_id),
  KEY idx_assignee_status (assignee_id, status),
  KEY idx_status_parent (status, parent_task_id),
  UNIQUE KEY uk_parent_country (parent_task_id, country_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS task_events (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id       INT NOT NULL,
  event_type    VARCHAR(32) NOT NULL,
  actor_user_id INT DEFAULT NULL,
  payload_json  JSON DEFAULT NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_task (task_id, created_at),
  KEY idx_actor (actor_user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_db_migration_tasks_tables.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/2026_04_26_add_tasks_tables.sql tests/test_db_migration_tasks_tables.py
git commit -m "feat(task-center): add tasks/task_events migration"
```

---

### Task 2: Permissions — 加 capability codes

**Files:**
- Modify: `appcore/permissions.py`
- Test: `tests/test_appcore_permissions_task_capabilities.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_appcore_permissions_task_capabilities.py
from appcore.permissions import (
    PERMISSION_CODES, default_permissions_for_role,
    ROLE_ADMIN, ROLE_USER, ROLE_SUPERADMIN,
)


def test_task_center_codes_present():
    for code in ("task_center", "can_process_raw_video", "can_translate"):
        assert code in PERMISSION_CODES


def test_admin_defaults_have_capabilities_on():
    perms = default_permissions_for_role(ROLE_ADMIN)
    assert perms["task_center"] is True
    assert perms["can_process_raw_video"] is True
    assert perms["can_translate"] is True


def test_user_defaults_have_capabilities_off():
    perms = default_permissions_for_role(ROLE_USER)
    assert perms["task_center"] is True            # 菜单可见，看到的内容由后端过滤
    assert perms["can_process_raw_video"] is False
    assert perms["can_translate"] is False


def test_superadmin_always_full():
    perms = default_permissions_for_role(ROLE_SUPERADMIN)
    for code in ("task_center", "can_process_raw_video", "can_translate"):
        assert perms[code] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_appcore_permissions_task_capabilities.py -q`
Expected: FAIL — codes not in PERMISSION_CODES.

- [ ] **Step 3: Modify `appcore/permissions.py`**

In the `GROUPS` tuple, add a new group:

```python
GROUP_CAPABILITY = "capability"

GROUPS = (
    (GROUP_BUSINESS, "业务功能"),
    (GROUP_MANAGEMENT, "管理功能"),
    (GROUP_CAPABILITY, "任务能力"),
    (GROUP_SYSTEM, "系统 / 超管"),
)
```

In the `PERMISSIONS` tuple, add three entries (place `task_center` in `GROUP_BUSINESS` block right after `pushes`; place capabilities at the new group's start):

```python
PERMISSIONS: tuple[tuple[str, str, str, bool, bool], ...] = (
    # ... existing entries up to and including pushes ...
    ("pushes",                GROUP_BUSINESS,   "推送管理",         True,  True),
    ("task_center",           GROUP_BUSINESS,   "任务中心",         True,  True),
    # ... existing projects / user_settings / management group ...
    # New capability group (insert before the SYSTEM group in PERMISSIONS tuple):
    ("can_process_raw_video", GROUP_CAPABILITY, "原始视频处理人",   True,  False),
    ("can_translate",         GROUP_CAPABILITY, "翻译员",           True,  False),
    # ... existing SYSTEM group entries ...
)
```

(Apply the additions inline; don't rewrite the whole tuple. Order matters only for UI rendering grouping.)

- [ ] **Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_appcore_permissions_task_capabilities.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add appcore/permissions.py tests/test_appcore_permissions_task_capabilities.py
git commit -m "feat(task-center): add task_center menu + raw_video/translate capabilities"
```

---

## Phase 2 — Service Layer (`appcore/tasks.py`)

> All Phase 2 tasks share one test file: `tests/test_appcore_tasks.py`. Tests use a real DB; `conftest.py` already loads `tests/conftest.py` env. Each task adds a fixture or test on top.

### Task 3: tasks.py 骨架 + 状态常量 + helpers

**Files:**
- Create: `appcore/tasks.py`
- Create: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_appcore_tasks.py
from appcore import tasks


def test_status_constants_present():
    assert tasks.PARENT_PENDING == "pending"
    assert tasks.PARENT_RAW_IN_PROGRESS == "raw_in_progress"
    assert tasks.PARENT_RAW_REVIEW == "raw_review"
    assert tasks.PARENT_RAW_DONE == "raw_done"
    assert tasks.PARENT_ALL_DONE == "all_done"
    assert tasks.PARENT_CANCELLED == "cancelled"
    assert tasks.CHILD_BLOCKED == "blocked"
    assert tasks.CHILD_ASSIGNED == "assigned"
    assert tasks.CHILD_REVIEW == "review"
    assert tasks.CHILD_DONE == "done"
    assert tasks.CHILD_CANCELLED == "cancelled"


def test_high_level_status_rollup():
    assert tasks.high_level_status("pending") == "in_progress"
    assert tasks.high_level_status("raw_in_progress") == "in_progress"
    assert tasks.high_level_status("review") == "in_progress"
    assert tasks.high_level_status("done") == "completed"
    assert tasks.high_level_status("all_done") == "completed"
    assert tasks.high_level_status("cancelled") == "terminated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_appcore_tasks.py -q`
Expected: ImportError / AttributeError.

- [ ] **Step 3: Create `appcore/tasks.py`**

```python
# appcore/tasks.py
"""任务中心 service 层 — 双层任务模型 + 状态机。

- 父任务（parent_task_id IS NULL）: 素材级，原始视频段
- 子任务（parent_task_id IS NOT NULL）: 国家级，翻译段

完整设计见 docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md。
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from appcore.db import execute, get_conn, query_one, query_all

# ---- 状态常量 ----
PARENT_PENDING = "pending"
PARENT_RAW_IN_PROGRESS = "raw_in_progress"
PARENT_RAW_REVIEW = "raw_review"
PARENT_RAW_DONE = "raw_done"
PARENT_ALL_DONE = "all_done"
PARENT_CANCELLED = "cancelled"

CHILD_BLOCKED = "blocked"
CHILD_ASSIGNED = "assigned"
CHILD_REVIEW = "review"
CHILD_DONE = "done"
CHILD_CANCELLED = "cancelled"

PARENT_NON_TERMINAL = (
    PARENT_PENDING, PARENT_RAW_IN_PROGRESS,
    PARENT_RAW_REVIEW, PARENT_RAW_DONE,
)
PARENT_TERMINAL = (PARENT_ALL_DONE, PARENT_CANCELLED)
CHILD_NON_TERMINAL = (CHILD_BLOCKED, CHILD_ASSIGNED, CHILD_REVIEW)
CHILD_TERMINAL = (CHILD_DONE, CHILD_CANCELLED)

# ---- 高层状态 rollup ----
def high_level_status(status: str) -> str:
    if status in (PARENT_ALL_DONE, CHILD_DONE):
        return "completed"
    if status in (PARENT_CANCELLED, CHILD_CANCELLED):
        return "terminated"
    return "in_progress"


# ---- 共用 helpers (后续 task 用) ----
def _row(task_id: int) -> dict | None:
    return query_one("SELECT * FROM tasks WHERE id=%s", (int(task_id),))


def _write_event(
    cur, task_id: int, event_type: str,
    actor_user_id: int | None, payload: dict | None = None,
) -> None:
    cur.execute(
        "INSERT INTO task_events (task_id, event_type, actor_user_id, payload_json) "
        "VALUES (%s, %s, %s, %s)",
        (
            int(task_id), event_type,
            int(actor_user_id) if actor_user_id is not None else None,
            json.dumps(payload, ensure_ascii=False) if payload else None,
        ),
    )
```

- [ ] **Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_appcore_tasks.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): scaffold tasks service + status constants"
```

---

### Task 4: `create_parent_task`

**Files:**
- Modify: `appcore/tasks.py` (add function)
- Modify: `tests/test_appcore_tasks.py` (add tests + fixtures)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_appcore_tasks.py`:

```python
import pytest
from appcore.db import execute, query_one


@pytest.fixture
def db_user_admin():
    """Make a temporary admin user; yield id; cleanup at end."""
    from appcore.users import create_user, get_by_username
    username = "_t_tc_admin"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="admin")
    uid = get_by_username(username)["id"]
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))


@pytest.fixture
def db_user_translator():
    from appcore.users import create_user, get_by_username
    username = "_t_tc_tr"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="user")
    uid = get_by_username(username)["id"]
    # 给翻译能力位
    execute(
        "UPDATE users SET permissions=JSON_SET(COALESCE(permissions, '{}'), '$.can_translate', true) WHERE id=%s",
        (uid,),
    )
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))


@pytest.fixture
def db_product(db_user_admin):
    """Make a media product owned by db_user_admin."""
    execute(
        "INSERT INTO media_products (user_id, name) VALUES (%s, %s)",
        (db_user_admin, "_t_tc_product"),
    )
    pid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    # 加一条 en item
    execute(
        "INSERT INTO media_items (product_id, user_id, filename, object_key, lang) "
        "VALUES (%s, %s, %s, %s, %s)",
        (pid, db_user_admin, "x.mp4", "k/x.mp4", "en"),
    )
    iid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    yield {"product_id": pid, "item_id": iid}
    execute("DELETE FROM media_items WHERE product_id=%s", (pid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))


def test_create_parent_task_inserts_parent_and_children(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    parent = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert parent["parent_task_id"] is None
    assert parent["status"] == tasks.PARENT_PENDING
    assert parent["assignee_id"] is None
    assert parent["media_item_id"] == db_product["item_id"]

    children = query_all(
        "SELECT * FROM tasks WHERE parent_task_id=%s ORDER BY country_code",
        (parent_id,),
    )
    assert len(children) == 2
    assert {c["country_code"] for c in children} == {"DE", "FR"}
    for c in children:
        assert c["status"] == tasks.CHILD_BLOCKED
        assert c["assignee_id"] == db_user_translator
        assert c["media_item_id"] == db_product["item_id"]

    events = query_all(
        "SELECT * FROM task_events WHERE task_id IN (%s) ORDER BY id",
        (parent_id,),
    )
    assert any(e["event_type"] == "created" for e in events)

    # cleanup
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_create_parent_task_rejects_empty_countries(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    with pytest.raises(ValueError, match="countries"):
        tasks.create_parent_task(
            media_product_id=db_product["product_id"],
            media_item_id=db_product["item_id"],
            countries=[],
            translator_id=db_user_translator,
            created_by=db_user_admin,
        )


def test_create_parent_task_uppercases_countries(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["de", "fr"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    children = query_all(
        "SELECT country_code FROM tasks WHERE parent_task_id=%s",
        (parent_id,),
    )
    assert {c["country_code"] for c in children} == {"DE", "FR"}
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_appcore_tasks.py -q`
Expected: 3 failed (function not defined).

- [ ] **Step 3: Add `create_parent_task` to `appcore/tasks.py`**

```python
def create_parent_task(
    *,
    media_product_id: int,
    media_item_id: int | None,
    countries: list[str],
    translator_id: int,
    created_by: int,
) -> int:
    """创建父任务 + 一并物化子任务 (status=blocked)。返回父任务 id。"""
    if not countries:
        raise ValueError("countries must be non-empty")
    norm_countries = [c.strip().upper() for c in countries if c and c.strip()]
    if not norm_countries:
        raise ValueError("countries must be non-empty after normalization")

    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO tasks "
                    "(parent_task_id, media_product_id, media_item_id, status, created_by) "
                    "VALUES (NULL, %s, %s, %s, %s)",
                    (int(media_product_id),
                     int(media_item_id) if media_item_id is not None else None,
                     PARENT_PENDING, int(created_by)),
                )
                parent_id = cur.lastrowid
                _write_event(cur, parent_id, "created", created_by,
                             {"countries": norm_countries,
                              "translator_id": int(translator_id)})
                for country in norm_countries:
                    cur.execute(
                        "INSERT INTO tasks "
                        "(parent_task_id, media_product_id, media_item_id, "
                        " country_code, assignee_id, status, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (parent_id, int(media_product_id),
                         int(media_item_id) if media_item_id is not None else None,
                         country, int(translator_id), CHILD_BLOCKED, int(created_by)),
                    )
                    child_id = cur.lastrowid
                    _write_event(cur, child_id, "created", created_by,
                                 {"country": country})
            conn.commit()
            return int(parent_id)
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_appcore_tasks.py -q`
Expected: 5 passed (2 original + 3 new).

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): create_parent_task with auto-materialized children"
```

---

### Task 5: `claim_parent` (并发安全)

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_claim_parent_succeeds(db_user_admin, db_user_translator, db_product):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    row = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert row["status"] == tasks.PARENT_RAW_IN_PROGRESS
    assert row["assignee_id"] == db_user_admin
    assert row["claimed_at"] is not None
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_claim_parent_already_claimed_raises(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    with pytest.raises(tasks.ConflictError):
        tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_translator)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
```

- [ ] **Step 2: Run tests, expect 2 failures**

Run: `python -m pytest tests/test_appcore_tasks.py -q`
Expected: 2 failed.

- [ ] **Step 3: Add to `appcore/tasks.py`**

```python
class ConflictError(RuntimeError):
    """Optimistic concurrency violation, e.g., already claimed."""


class StateError(RuntimeError):
    """Invalid state transition / precondition violation."""


def claim_parent(*, task_id: int, actor_user_id: int) -> None:
    """处理人认领父任务。乐观锁防并发。"""
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET assignee_id=%s, status=%s, "
                    "claimed_at=NOW(), updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NULL AND status=%s",
                    (int(actor_user_id), PARENT_RAW_IN_PROGRESS,
                     int(task_id), PARENT_PENDING),
                )
                if cur.rowcount == 0:
                    raise ConflictError("task not pending or already claimed")
                _write_event(cur, task_id, "claimed", actor_user_id, None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests, expect pass**

Run: `python -m pytest tests/test_appcore_tasks.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): claim_parent with optimistic concurrency"
```

---

### Task 6: `mark_uploaded`

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_mark_uploaded_transitions_to_review(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    row = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert row["status"] == tasks.PARENT_RAW_REVIEW
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_mark_uploaded_requires_media_item(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=None,                    # 故意不绑定
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    with pytest.raises(tasks.StateError, match="media_item"):
        tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
```

- [ ] **Step 2: Run tests, expect 2 failures**

- [ ] **Step 3: Add to `appcore/tasks.py`**

```python
def mark_uploaded(*, task_id: int, actor_user_id: int) -> None:
    """处理人标"已上传"，转入待审核。"""
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, assignee_id, media_item_id "
                    "FROM tasks WHERE id=%s AND parent_task_id IS NULL FOR UPDATE",
                    (int(task_id),),
                )
                row = cur.fetchone()
                if not row:
                    raise StateError("parent task not found")
                if row["status"] != PARENT_RAW_IN_PROGRESS:
                    raise StateError(
                        f"expected status raw_in_progress, got {row['status']}"
                    )
                if row["assignee_id"] != int(actor_user_id):
                    raise StateError("only assignee can mark uploaded")
                if row["media_item_id"] is None:
                    raise StateError("media_item not bound; upload first")
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=NULL, updated_at=NOW() "
                    "WHERE id=%s",
                    (PARENT_RAW_REVIEW, int(task_id)),
                )
                _write_event(cur, task_id, "raw_uploaded", actor_user_id, None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests, expect pass (9 total)**

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): mark_uploaded with media_item precondition"
```

---

### Task 7: `approve_raw` + 自动 unblock children

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_approve_raw_unblocks_children(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)

    parent = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert parent["status"] == tasks.PARENT_RAW_DONE

    children = query_all(
        "SELECT * FROM tasks WHERE parent_task_id=%s", (parent_id,)
    )
    assert all(c["status"] == tasks.CHILD_ASSIGNED for c in children)

    events = query_all(
        "SELECT event_type FROM task_events "
        "WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)",
        (parent_id, parent_id),
    )
    types = [e["event_type"] for e in events]
    assert "approved" in types
    assert types.count("unblocked") >= 2
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Add to `appcore/tasks.py`**

```python
def approve_raw(*, task_id: int, actor_user_id: int) -> None:
    """管理员审核通过原始视频，自动 unblock 所有 blocked 子任务。"""
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=NULL, updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NULL AND status=%s",
                    (PARENT_RAW_DONE, int(task_id), PARENT_RAW_REVIEW),
                )
                if cur.rowcount == 0:
                    raise StateError("parent not in raw_review")
                _write_event(cur, task_id, "approved", actor_user_id, None)

                cur.execute(
                    "SELECT id FROM tasks WHERE parent_task_id=%s AND status=%s",
                    (int(task_id), CHILD_BLOCKED),
                )
                child_ids = [r["id"] for r in cur.fetchall()]
                if child_ids:
                    fmt = ",".join(["%s"] * len(child_ids))
                    cur.execute(
                        f"UPDATE tasks SET status=%s, updated_at=NOW() "
                        f"WHERE id IN ({fmt})",
                        (CHILD_ASSIGNED, *child_ids),
                    )
                    for cid in child_ids:
                        _write_event(cur, cid, "unblocked", None, None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
```

- [ ] **Step 4: Run, expect pass (10)**

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): approve_raw + auto-unblock blocked children"
```

---

### Task 8: `reject_raw`

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_reject_raw_returns_to_in_progress_with_same_assignee(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.reject_raw(task_id=parent_id, actor_user_id=db_user_admin,
                     reason="字幕没去干净请重做")
    row = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert row["status"] == tasks.PARENT_RAW_IN_PROGRESS
    assert row["assignee_id"] == db_user_admin
    assert "字幕没去干净" in row["last_reason"]
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_reject_raw_requires_min_reason(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    with pytest.raises(ValueError, match="reason"):
        tasks.reject_raw(task_id=parent_id, actor_user_id=db_user_admin, reason="短")
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
```

- [ ] **Step 2: Run, expect 2 failures**

- [ ] **Step 3: Add to `appcore/tasks.py`**

```python
MIN_REASON_LEN = 10


def reject_raw(*, task_id: int, actor_user_id: int, reason: str) -> None:
    """管理员打回原始视频，状态回 raw_in_progress（同 assignee）。"""
    if not reason or len(reason.strip()) < MIN_REASON_LEN:
        raise ValueError(f"reason must be at least {MIN_REASON_LEN} characters")
    reason = reason.strip()
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=%s, updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NULL AND status=%s",
                    (PARENT_RAW_IN_PROGRESS, reason, int(task_id), PARENT_RAW_REVIEW),
                )
                if cur.rowcount == 0:
                    raise StateError("parent not in raw_review")
                _write_event(cur, task_id, "rejected", actor_user_id, {"reason": reason})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
```

- [ ] **Step 4: Run, expect pass (12)**

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): reject_raw with original-route return + reason gate"
```

---

### Task 9: `cancel_parent` + 级联

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_cancel_parent_cascades_non_done_children(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR", "JA"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    # 走一遍，让 DE 子任务 done
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    de_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    execute("UPDATE tasks SET status='done', completed_at=NOW() WHERE id=%s", (de_id,))

    tasks.cancel_parent(task_id=parent_id, actor_user_id=db_user_admin,
                        reason="商品已下架，整体取消")

    parent = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert parent["status"] == tasks.PARENT_CANCELLED
    assert parent["cancelled_at"] is not None

    de = query_one("SELECT * FROM tasks WHERE id=%s", (de_id,))
    assert de["status"] == tasks.CHILD_DONE     # 已 done 保留

    others = query_all(
        "SELECT * FROM tasks WHERE parent_task_id=%s AND id<>%s",
        (parent_id, de_id),
    )
    assert all(c["status"] == tasks.CHILD_CANCELLED for c in others)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Add to `appcore/tasks.py`**

```python
def cancel_parent(*, task_id: int, actor_user_id: int, reason: str) -> None:
    """admin 取消父任务；级联取消所有非 done 子任务，已 done 保留。"""
    if not reason or len(reason.strip()) < MIN_REASON_LEN:
        raise ValueError(f"reason must be at least {MIN_REASON_LEN} characters")
    reason = reason.strip()
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=%s, "
                    "cancelled_at=NOW(), updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NULL "
                    "AND status IN (%s,%s,%s,%s)",
                    (PARENT_CANCELLED, reason, int(task_id),
                     PARENT_PENDING, PARENT_RAW_IN_PROGRESS,
                     PARENT_RAW_REVIEW, PARENT_RAW_DONE),
                )
                if cur.rowcount == 0:
                    raise StateError("parent not in cancellable state")
                cur.execute(
                    "SELECT id FROM tasks WHERE parent_task_id=%s "
                    "AND status IN (%s,%s,%s)",
                    (int(task_id), CHILD_BLOCKED, CHILD_ASSIGNED, CHILD_REVIEW),
                )
                cascaded = [r["id"] for r in cur.fetchall()]
                if cascaded:
                    fmt = ",".join(["%s"] * len(cascaded))
                    cur.execute(
                        f"UPDATE tasks SET status=%s, last_reason=%s, "
                        f"cancelled_at=NOW(), updated_at=NOW() WHERE id IN ({fmt})",
                        (CHILD_CANCELLED, "parent cancelled: " + reason, *cascaded),
                    )
                    for cid in cascaded:
                        _write_event(cur, cid, "cancelled", actor_user_id,
                                     {"cascaded_from": int(task_id)})
                _write_event(cur, task_id, "cancelled", actor_user_id,
                             {"reason": reason, "cascaded_child_count": len(cascaded)})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
```

- [ ] **Step 4: Run, expect pass (13)**

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): cancel_parent with cascade preserving done children"
```

---

### Task 10: `submit_child` + readiness gate

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Write the failing test**

Append (this test mocks the readiness check by using a monkey-patch since readiness depends on full media_items + copywriting state):

```python
def test_submit_child_passes_with_ready(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    child_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    # Stub readiness: 假装产物齐全 + 假装目标语种 item 存在
    monkeypatch.setattr(tasks, "_find_target_lang_item",
                        lambda product_id, lang: {"id": 1, "object_key": "x", "cover_object_key": "c", "lang": lang, "product_id": product_id})
    monkeypatch.setattr("appcore.pushes.compute_readiness",
                        lambda i, p: {"has_video": True, "has_cover": True,
                                      "has_copywriting": True, "has_push_texts": True,
                                      "is_listed": True})
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: True)

    tasks.submit_child(task_id=child_id, actor_user_id=db_user_translator)
    row = query_one("SELECT * FROM tasks WHERE id=%s", (child_id,))
    assert row["status"] == tasks.CHILD_REVIEW
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_submit_child_fails_when_not_ready(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    child_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    monkeypatch.setattr(tasks, "_find_target_lang_item",
                        lambda product_id, lang: {"id": 1, "lang": lang, "product_id": product_id})
    monkeypatch.setattr("appcore.pushes.compute_readiness",
                        lambda i, p: {"has_video": True, "has_cover": False,
                                      "has_copywriting": False})
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: False)

    with pytest.raises(tasks.NotReadyError) as exc:
        tasks.submit_child(task_id=child_id, actor_user_id=db_user_translator)
    assert "has_cover" in str(exc.value.missing) or "has_cover" in str(exc.value)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_submit_child_fails_when_target_lang_item_missing(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    child_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    monkeypatch.setattr(tasks, "_find_target_lang_item", lambda *a, **k: None)
    with pytest.raises(tasks.NotReadyError, match="lang_item_missing|missing"):
        tasks.submit_child(task_id=child_id, actor_user_id=db_user_translator)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
```

- [ ] **Step 2: Run, expect failures**

- [ ] **Step 3: Add to `appcore/tasks.py`**

```python
class NotReadyError(RuntimeError):
    """compute_readiness gate failed; carries missing keys."""
    def __init__(self, missing: list[str], detail: str = ""):
        self.missing = missing
        super().__init__(detail or f"missing: {missing}")


def _find_target_lang_item(product_id: int, lang: str) -> dict | None:
    return query_one(
        "SELECT * FROM media_items "
        "WHERE product_id=%s AND lang=%s AND deleted_at IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (int(product_id), lang),
    )


def _find_product(product_id: int) -> dict | None:
    return query_one(
        "SELECT * FROM media_products WHERE id=%s", (int(product_id),)
    )


def submit_child(*, task_id: int, actor_user_id: int) -> None:
    """翻译员提交子任务；调 compute_readiness 做产物齐全 gate。"""
    from appcore import pushes
    row = query_one(
        "SELECT * FROM tasks WHERE id=%s AND parent_task_id IS NOT NULL",
        (int(task_id),),
    )
    if not row:
        raise StateError("child task not found")
    if row["status"] != CHILD_ASSIGNED:
        raise StateError(f"expected status assigned, got {row['status']}")
    if row["assignee_id"] != int(actor_user_id):
        raise StateError("only assignee can submit")

    item = _find_target_lang_item(row["media_product_id"], row["country_code"])
    if not item:
        raise NotReadyError(missing=["lang_item_missing"],
                            detail=f"no media_item with lang={row['country_code']}")
    product = _find_product(row["media_product_id"])
    readiness = pushes.compute_readiness(item, product)
    if not pushes.is_ready(readiness):
        missing = [k for k, v in readiness.items()
                   if not str(k).endswith("_reason") and not v]
        raise NotReadyError(missing=missing, detail=f"readiness failed: {missing}")

    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=NULL, updated_at=NOW() "
                    "WHERE id=%s AND status=%s",
                    (CHILD_REVIEW, int(task_id), CHILD_ASSIGNED),
                )
                if cur.rowcount == 0:
                    raise StateError("child not in assigned (race)")
                _write_event(cur, task_id, "submitted", actor_user_id, None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
```

- [ ] **Step 4: Run, expect pass (16)**

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): submit_child with readiness gate"
```

---

### Task 11: `approve_child` + 自动 all_done

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_approve_child_auto_all_done_when_last_child(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    monkeypatch.setattr(tasks, "_find_target_lang_item",
                        lambda product_id, lang: {"id": 1})
    monkeypatch.setattr("appcore.pushes.compute_readiness",
                        lambda i, p: {"ok": True})
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: True)

    de_id, fr_id = (
        query_one("SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'", (parent_id,))["id"],
        query_one("SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='FR'", (parent_id,))["id"],
    )
    tasks.submit_child(task_id=de_id, actor_user_id=db_user_translator)
    tasks.approve_child(task_id=de_id, actor_user_id=db_user_admin)
    parent = query_one("SELECT status FROM tasks WHERE id=%s", (parent_id,))
    assert parent["status"] == tasks.PARENT_RAW_DONE   # 还没全部完成

    tasks.submit_child(task_id=fr_id, actor_user_id=db_user_translator)
    tasks.approve_child(task_id=fr_id, actor_user_id=db_user_admin)
    parent = query_one("SELECT status, completed_at FROM tasks WHERE id=%s", (parent_id,))
    assert parent["status"] == tasks.PARENT_ALL_DONE
    assert parent["completed_at"] is not None
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Add to `appcore/tasks.py`**

```python
def approve_child(*, task_id: int, actor_user_id: int) -> None:
    """管理员审核通过翻译；若该父任务下所有子都 done/cancelled 且至少一条 done，
    则父任务自动 all_done。"""
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=NULL, "
                    "completed_at=NOW(), updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NOT NULL AND status=%s",
                    (CHILD_DONE, int(task_id), CHILD_REVIEW),
                )
                if cur.rowcount == 0:
                    raise StateError("child not in review")
                _write_event(cur, task_id, "approved", actor_user_id, None)

                cur.execute(
                    "SELECT parent_task_id FROM tasks WHERE id=%s",
                    (int(task_id),),
                )
                parent_id = cur.fetchone()["parent_task_id"]
                cur.execute(
                    "SELECT status FROM tasks WHERE parent_task_id=%s", (parent_id,)
                )
                statuses = [r["status"] for r in cur.fetchall()]
                terminal = all(s in (CHILD_DONE, CHILD_CANCELLED) for s in statuses)
                any_done = any(s == CHILD_DONE for s in statuses)
                if terminal and any_done:
                    cur.execute(
                        "UPDATE tasks SET status=%s, completed_at=NOW(), updated_at=NOW() "
                        "WHERE id=%s AND status=%s",
                        (PARENT_ALL_DONE, int(parent_id), PARENT_RAW_DONE),
                    )
                    if cur.rowcount:
                        _write_event(cur, parent_id, "completed", None, None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
```

- [ ] **Step 4: Run, expect pass (17)**

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): approve_child + auto all_done parent"
```

---

### Task 12: `reject_child`

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_reject_child_returns_to_assigned(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    monkeypatch.setattr(tasks, "_find_target_lang_item", lambda *a, **k: {"id": 1})
    monkeypatch.setattr("appcore.pushes.compute_readiness", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: True)
    de_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    tasks.submit_child(task_id=de_id, actor_user_id=db_user_translator)
    tasks.reject_child(task_id=de_id, actor_user_id=db_user_admin,
                       reason="DE 文案翻译有错")
    row = query_one("SELECT * FROM tasks WHERE id=%s", (de_id,))
    assert row["status"] == tasks.CHILD_ASSIGNED
    assert row["assignee_id"] == db_user_translator
    assert "DE 文案翻译有错" in row["last_reason"]
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Add to `appcore/tasks.py`**

```python
def reject_child(*, task_id: int, actor_user_id: int, reason: str) -> None:
    """管理员打回翻译；状态回 assigned（同 assignee）。"""
    if not reason or len(reason.strip()) < MIN_REASON_LEN:
        raise ValueError(f"reason must be at least {MIN_REASON_LEN} characters")
    reason = reason.strip()
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=%s, updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NOT NULL AND status=%s",
                    (CHILD_ASSIGNED, reason, int(task_id), CHILD_REVIEW),
                )
                if cur.rowcount == 0:
                    raise StateError("child not in review")
                _write_event(cur, task_id, "rejected", actor_user_id,
                             {"reason": reason})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
```

- [ ] **Step 4: Run, expect pass (18)**

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): reject_child with original-route return"
```

---

### Task 13: `cancel_child`

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_cancel_child_does_not_change_parent(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    de_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    tasks.cancel_child(task_id=de_id, actor_user_id=db_user_admin,
                       reason="DE 站点暂停上架")
    de = query_one("SELECT * FROM tasks WHERE id=%s", (de_id,))
    assert de["status"] == tasks.CHILD_CANCELLED
    parent = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert parent["status"] == tasks.PARENT_RAW_DONE
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Add to `appcore/tasks.py`**

```python
def cancel_child(*, task_id: int, actor_user_id: int, reason: str) -> None:
    """admin 取消单个子任务；父任务状态不变。"""
    if not reason or len(reason.strip()) < MIN_REASON_LEN:
        raise ValueError(f"reason must be at least {MIN_REASON_LEN} characters")
    reason = reason.strip()
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET status=%s, last_reason=%s, "
                    "cancelled_at=NOW(), updated_at=NOW() "
                    "WHERE id=%s AND parent_task_id IS NOT NULL "
                    "AND status IN (%s,%s,%s)",
                    (CHILD_CANCELLED, reason, int(task_id),
                     CHILD_BLOCKED, CHILD_ASSIGNED, CHILD_REVIEW),
                )
                if cur.rowcount == 0:
                    raise StateError("child not in cancellable state")
                _write_event(cur, task_id, "cancelled", actor_user_id,
                             {"reason": reason})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
```

- [ ] **Step 4: Run, expect pass (19)**

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): cancel_child without affecting parent"
```

---

### Task 14: `on_product_owner_changed` cascade + 钩子集成到 medias.py

**Files:**
- Modify: `appcore/tasks.py`
- Modify: `appcore/medias.py` (`update_product_owner`)
- Modify: `tests/test_appcore_tasks.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_owner_change_cascades_to_non_terminal_children(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    from appcore.users import create_user, get_by_username
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_tr2",))
    create_user("_t_tc_tr2", "x", role="user")
    new_translator = get_by_username("_t_tc_tr2")["id"]

    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    fr_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='FR'",
        (parent_id,),
    )["id"]
    execute("UPDATE tasks SET status='done', completed_at=NOW(), assignee_id=%s WHERE id=%s",
            (db_user_translator, fr_id))

    tasks.on_product_owner_changed(
        product_id=db_product["product_id"],
        new_user_id=new_translator,
        actor_user_id=db_user_admin,
    )

    de = query_one("SELECT assignee_id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
                   (parent_id,))
    fr = query_one("SELECT assignee_id FROM tasks WHERE id=%s", (fr_id,))
    assert de["assignee_id"] == new_translator     # 未完成跟换
    assert fr["assignee_id"] == db_user_translator # 已 done 不变

    events = query_all(
        "SELECT * FROM task_events WHERE event_type='assignee_changed'"
    )
    assert len(events) >= 1
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_tr2",))


def test_update_product_owner_invokes_task_cascade(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    """Verify appcore.medias.update_product_owner triggers tasks.on_product_owner_changed."""
    from appcore import medias, tasks
    called = []
    monkeypatch.setattr(tasks, "on_product_owner_changed",
                        lambda **kw: called.append(kw))
    medias.update_product_owner(db_product["product_id"], db_user_translator)
    assert len(called) == 1
    assert called[0]["product_id"] == db_product["product_id"]
    assert called[0]["new_user_id"] == db_user_translator
```

- [ ] **Step 2: Run, expect 2 failures**

- [ ] **Step 3a: Add `on_product_owner_changed` to `appcore/tasks.py`**

```python
def on_product_owner_changed(
    *, product_id: int, new_user_id: int, actor_user_id: int | None = None,
) -> int:
    """素材产品负责人变更时被调用。把状态非 done/cancelled 的子任务的
    assignee_id 同步到 new_user_id。返回受影响的子任务数。"""
    conn = get_conn()
    try:
        conn.begin()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, assignee_id FROM tasks "
                    "WHERE media_product_id=%s AND parent_task_id IS NOT NULL "
                    "AND status NOT IN (%s, %s)",
                    (int(product_id), CHILD_DONE, CHILD_CANCELLED),
                )
                rows = cur.fetchall()
                affected = 0
                for r in rows:
                    if r["assignee_id"] == int(new_user_id):
                        continue
                    cur.execute(
                        "UPDATE tasks SET assignee_id=%s, updated_at=NOW() "
                        "WHERE id=%s",
                        (int(new_user_id), r["id"]),
                    )
                    _write_event(cur, r["id"], "assignee_changed", actor_user_id,
                                 {"old": r["assignee_id"], "new": int(new_user_id)})
                    affected += 1
            conn.commit()
            return affected
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
```

- [ ] **Step 3b: Modify `appcore/medias.py:update_product_owner`**

In `appcore/medias.py`, find the `update_product_owner` function (around line 505). After the existing `conn.commit()` and `conn.close()`, add the hook call. The function ends around line 553. Modify to:

```python
        # ... existing try/finally that commits the 3 UPDATEs ...
    finally:
        conn.close()

    # 联动任务中心：未完成子任务的 assignee 跟换；已完成 / 已取消保留快照
    try:
        from appcore import tasks
        tasks.on_product_owner_changed(
            product_id=pid, new_user_id=uid, actor_user_id=None,
        )
    except Exception:
        # 任务中心 cascade 失败不应回滚 owner 变更（已 commit）；记日志即可
        import logging
        logging.getLogger(__name__).exception(
            "task-center cascade failed for product_id=%s", pid,
        )
```

- [ ] **Step 4: Run tests, expect pass (21)**

Run: `python -m pytest tests/test_appcore_tasks.py -q`

- [ ] **Step 5: Commit**

```bash
git add appcore/tasks.py appcore/medias.py tests/test_appcore_tasks.py
git commit -m "feat(task-center): owner-cascade hook + integrate with media owner update"
```

---

## Phase 3 — API Routes (`web/routes/tasks.py`)

> All Phase 3 tasks share one test file: `tests/test_tasks_routes.py`. Tests use `logged_in_client` fixture from conftest (live DB, admin).

### Task 15: Blueprint 骨架 + 主页路由 + 注册

**Files:**
- Create: `web/routes/tasks.py`
- Create: `tests/test_tasks_routes.py`
- Modify: `web/app.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tasks_routes.py
def test_index_renders_for_admin(logged_in_client):
    rsp = logged_in_client.get("/tasks/")
    assert rsp.status_code == 200
    assert b"\xe4\xbb\xbb\xe5\x8a\xa1\xe4\xb8\xad\xe5\xbf\x83" in rsp.data  # "任务中心" in UTF-8


def test_index_requires_login():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    rsp = client.get("/tasks/", follow_redirects=False)
    assert rsp.status_code in (302, 401)
```

- [ ] **Step 2: Run, expect failure (404 — route not registered)**

- [ ] **Step 3a: Create `web/routes/tasks.py`**

```python
"""任务中心 Blueprint."""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import tasks as tasks_svc

log = logging.getLogger(__name__)
bp = Blueprint("tasks", __name__, url_prefix="/tasks")


def _is_admin() -> bool:
    return getattr(current_user, "is_admin", False) or \
        getattr(current_user, "role", "") in ("admin", "superadmin")


def _user_perms() -> dict:
    perms = getattr(current_user, "permissions", None) or {}
    if isinstance(perms, str):
        import json
        try:
            perms = json.loads(perms)
        except Exception:
            perms = {}
    return perms or {}


def _has_capability(code: str) -> bool:
    if _is_admin():
        return True
    return bool(_user_perms().get(code, False))


def admin_required(fn):
    @wraps(fn)
    def _wrap(*a, **kw):
        if not _is_admin():
            return jsonify({"error": "仅管理员可操作"}), 403
        return fn(*a, **kw)
    return _wrap


def capability_required(code: str):
    def _dec(fn):
        @wraps(fn)
        def _wrap(*a, **kw):
            if not _has_capability(code):
                return jsonify({"error": f"缺少能力 {code}"}), 403
            return fn(*a, **kw)
        return _wrap
    return _dec


@bp.route("/")
@login_required
def index():
    return render_template(
        "tasks_list.html",
        is_admin=_is_admin(),
        capabilities={
            "can_process_raw_video": _has_capability("can_process_raw_video"),
            "can_translate": _has_capability("can_translate"),
        },
    )
```

- [ ] **Step 3b: Create minimal `web/templates/tasks_list.html`** (just enough to satisfy the test; full UI in Phase 4)

```html
{% extends "layout.html" %}
{% block title %}任务中心 - AutoVideoSrt{% endblock %}
{% block page_title %}任务中心{% endblock %}
{% block content %}
<div id="taskCenterRoot">
  <h1>任务中心</h1>
  <p>页面骨架占位 — Phase 4 完成实际 UI。</p>
</div>
{% endblock %}
```

- [ ] **Step 3c: Register blueprint in `web/app.py`**

In `web/app.py`, find the import block at the top (other route imports) and add:

```python
from web.routes.tasks import bp as tasks_bp
```

In the `create_app()` function where other blueprints register (around lines 135-195), add:

```python
    app.register_blueprint(tasks_bp)
```

(Insert near `pushes_bp` registration to keep related menus together.)

- [ ] **Step 4: Run test, expect pass**

Run: `python -m pytest tests/test_tasks_routes.py -q`

- [ ] **Step 5: Commit**

```bash
git add web/routes/tasks.py web/templates/tasks_list.html web/app.py tests/test_tasks_routes.py
git commit -m "feat(task-center): scaffold tasks blueprint + index page + register"
```

---

### Task 16: GET `/tasks/api/list` — 任务列表

**Files:**
- Modify: `web/routes/tasks.py`
- Modify: `tests/test_tasks_routes.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_api_list_returns_empty_for_fresh_db(logged_in_client):
    rsp = logged_in_client.get("/tasks/api/list?tab=all")
    assert rsp.status_code == 200
    payload = rsp.get_json()
    assert "items" in payload
    assert isinstance(payload["items"], list)


def test_api_list_my_tasks_filters_by_assignee(logged_in_client):
    rsp = logged_in_client.get("/tasks/api/list?tab=mine")
    assert rsp.status_code == 200
    payload = rsp.get_json()
    assert "items" in payload
```

- [ ] **Step 2: Run, expect 404**

- [ ] **Step 3: Add to `web/routes/tasks.py`**

```python
@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    from appcore.db import query_all
    tab = (request.args.get("tab") or "mine").strip()
    keyword = (request.args.get("keyword") or "").strip()
    high_status = (request.args.get("status") or "").strip()
    page = max(1, int(request.args.get("page") or 1))
    page_size = min(100, max(1, int(request.args.get("page_size") or 20)))
    offset = (page - 1) * page_size

    where = ["1=1"]
    args: list = []

    if tab == "all":
        if not _is_admin():
            return jsonify({"error": "需要管理员权限"}), 403
    elif tab == "mine":
        where.append(
            "(t.assignee_id=%s OR (t.parent_task_id IS NULL AND t.status='pending' AND %s))"
        )
        args.extend([current_user.id,
                     1 if _has_capability("can_process_raw_video") else 0])

    if keyword:
        where.append("p.name LIKE %s")
        args.append(f"%{keyword}%")
    if high_status == "in_progress":
        where.append("t.status NOT IN ('all_done', 'done', 'cancelled')")
    elif high_status == "completed":
        where.append("t.status IN ('all_done', 'done')")
    elif high_status == "terminated":
        where.append("t.status='cancelled'")

    sql = (
        "SELECT t.*, p.name AS product_name, "
        "       u.username AS assignee_username "
        "FROM tasks t "
        "JOIN media_products p ON p.id=t.media_product_id "
        "LEFT JOIN users u ON u.id=t.assignee_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY t.id DESC "
        "LIMIT %s OFFSET %s"
    )
    rows = query_all(sql, (*args, page_size, offset))
    items = [
        {
            "id": r["id"],
            "parent_task_id": r["parent_task_id"],
            "media_product_id": r["media_product_id"],
            "product_name": r["product_name"],
            "country_code": r["country_code"],
            "assignee_id": r["assignee_id"],
            "assignee_username": r["assignee_username"],
            "status": r["status"],
            "high_level": tasks_svc.high_level_status(r["status"]),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "claimed_at": r["claimed_at"].isoformat() if r["claimed_at"] else None,
            "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            "cancelled_at": r["cancelled_at"].isoformat() if r["cancelled_at"] else None,
            "last_reason": r["last_reason"],
        }
        for r in rows
    ]
    return jsonify({"items": items, "page": page, "page_size": page_size})
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```bash
git add web/routes/tasks.py tests/test_tasks_routes.py
git commit -m "feat(task-center): GET /tasks/api/list with tab + filter support"
```

---

### Task 17: GET `/tasks/api/dispatch_pool` — 待派单素材

**Files:**
- Modify: `web/routes/tasks.py`
- Modify: `tests/test_tasks_routes.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_api_dispatch_pool_admin_only(logged_in_client):
    rsp = logged_in_client.get("/tasks/api/dispatch_pool")
    assert rsp.status_code == 200
    payload = rsp.get_json()
    assert "items" in payload
```

- [ ] **Step 2: Run, expect 404**

- [ ] **Step 3: Add to `web/routes/tasks.py`**

```python
@bp.route("/api/dispatch_pool", methods=["GET"])
@login_required
@admin_required
def api_dispatch_pool():
    from appcore.db import query_all
    sql = (
        "SELECT p.id AS product_id, p.name AS product_name, p.user_id AS owner_id, "
        "       (SELECT COUNT(*) FROM media_items mi WHERE mi.product_id=p.id "
        "        AND mi.lang='en' AND mi.deleted_at IS NULL) AS en_item_count "
        "FROM media_products p "
        "WHERE p.deleted_at IS NULL AND p.archived=0 "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM tasks t WHERE t.media_product_id=p.id "
        "  AND t.parent_task_id IS NULL "
        "  AND t.status NOT IN ('all_done', 'cancelled')"
        ") "
        "ORDER BY p.id DESC LIMIT 100"
    )
    rows = query_all(sql)
    return jsonify({"items": [dict(r) for r in rows]})
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```bash
git add web/routes/tasks.py tests/test_tasks_routes.py
git commit -m "feat(task-center): GET /tasks/api/dispatch_pool"
```

---

### Task 18: POST `/tasks/api/parent` — 创建父任务

**Files:**
- Modify: `web/routes/tasks.py`
- Modify: `tests/test_tasks_routes.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_create_parent_task_endpoint(logged_in_client):
    from appcore.db import execute, query_one
    from appcore.users import create_user, get_by_username
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t",))
    create_user("_t_tc_t", "x", role="user")
    tid = get_by_username("_t_tc_t")["id"]

    execute("INSERT INTO media_products (user_id, name) VALUES (%s, %s)", (tid, "_t_tc_p2"))
    pid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    execute(
        "INSERT INTO media_items (product_id, user_id, filename, object_key, lang) "
        "VALUES (%s,%s,%s,%s,%s)", (pid, tid, "x.mp4", "k/x.mp4", "en"),
    )
    iid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]

    rsp = logged_in_client.post("/tasks/api/parent", json={
        "media_product_id": pid,
        "media_item_id": iid,
        "countries": ["DE", "FR"],
        "translator_id": tid,
    })
    assert rsp.status_code == 200
    parent_id = rsp.get_json()["parent_task_id"]
    children = query_one(
        "SELECT COUNT(*) AS n FROM tasks WHERE parent_task_id=%s", (parent_id,)
    )
    assert children["n"] == 2

    # cleanup
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
    execute("DELETE FROM media_items WHERE product_id=%s", (pid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t",))
```

- [ ] **Step 2: Run, expect 404**

- [ ] **Step 3: Add to `web/routes/tasks.py`**

```python
@bp.route("/api/parent", methods=["POST"])
@login_required
@admin_required
def api_create_parent():
    payload = request.get_json(silent=True) or {}
    try:
        product_id = int(payload["media_product_id"])
        item_id = payload.get("media_item_id")
        item_id = int(item_id) if item_id is not None else None
        countries = payload.get("countries") or []
        translator_id = int(payload["translator_id"])
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({"error": f"参数错误: {e}"}), 400
    try:
        parent_id = tasks_svc.create_parent_task(
            media_product_id=product_id,
            media_item_id=item_id,
            countries=countries,
            translator_id=translator_id,
            created_by=int(current_user.id),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"parent_task_id": parent_id})
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```bash
git add web/routes/tasks.py tests/test_tasks_routes.py
git commit -m "feat(task-center): POST /tasks/api/parent create endpoint"
```

---

### Task 19: 父任务动作端点（claim / upload_done / approve / reject / cancel / bind_item）

**Files:**
- Modify: `web/routes/tasks.py`
- Modify: `tests/test_tasks_routes.py`

- [ ] **Step 1: Write the failing test (one combined test exercising all parent endpoints)**

Append:

```python
def test_parent_lifecycle_via_endpoints(logged_in_client):
    """走一遍 claim → upload_done → approve."""
    from appcore.db import execute, query_one
    from appcore.users import create_user, get_by_username
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t3",))
    create_user("_t_tc_t3", "x", role="user")
    tid = get_by_username("_t_tc_t3")["id"]
    execute("INSERT INTO media_products (user_id, name) VALUES (%s, %s)", (tid, "_t_tc_p3"))
    pid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    execute("INSERT INTO media_items (product_id, user_id, filename, object_key, lang) VALUES (%s,%s,%s,%s,%s)",
            (pid, tid, "x.mp4", "k/x.mp4", "en"))
    iid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]

    rsp = logged_in_client.post("/tasks/api/parent", json={
        "media_product_id": pid, "media_item_id": iid,
        "countries": ["DE"], "translator_id": tid,
    })
    parent_id = rsp.get_json()["parent_task_id"]

    rsp = logged_in_client.post(f"/tasks/api/parent/{parent_id}/claim")
    assert rsp.status_code == 200

    rsp = logged_in_client.post(f"/tasks/api/parent/{parent_id}/upload_done")
    assert rsp.status_code == 200

    rsp = logged_in_client.post(f"/tasks/api/parent/{parent_id}/approve")
    assert rsp.status_code == 200

    row = query_one("SELECT status FROM tasks WHERE id=%s", (parent_id,))
    assert row["status"] == "raw_done"

    # cleanup
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
    execute("DELETE FROM media_items WHERE product_id=%s", (pid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t3",))


def test_parent_reject_and_cancel_endpoints(logged_in_client):
    from appcore.db import execute, query_one
    from appcore.users import create_user, get_by_username
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t4",))
    create_user("_t_tc_t4", "x", role="user")
    tid = get_by_username("_t_tc_t4")["id"]
    execute("INSERT INTO media_products (user_id, name) VALUES (%s, %s)", (tid, "_t_tc_p4"))
    pid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    execute("INSERT INTO media_items (product_id, user_id, filename, object_key, lang) VALUES (%s,%s,%s,%s,%s)",
            (pid, tid, "x.mp4", "k/x.mp4", "en"))
    iid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]

    rsp = logged_in_client.post("/tasks/api/parent", json={
        "media_product_id": pid, "media_item_id": iid,
        "countries": ["DE"], "translator_id": tid,
    })
    parent_id = rsp.get_json()["parent_task_id"]
    logged_in_client.post(f"/tasks/api/parent/{parent_id}/claim")
    logged_in_client.post(f"/tasks/api/parent/{parent_id}/upload_done")

    rsp = logged_in_client.post(f"/tasks/api/parent/{parent_id}/reject",
                                 json={"reason": "字幕没去干净打回重做"})
    assert rsp.status_code == 200
    row = query_one("SELECT status FROM tasks WHERE id=%s", (parent_id,))
    assert row["status"] == "raw_in_progress"

    rsp = logged_in_client.post(f"/tasks/api/parent/{parent_id}/reject",
                                 json={"reason": "短"})
    assert rsp.status_code == 400  # reason 太短

    rsp = logged_in_client.post(f"/tasks/api/parent/{parent_id}/cancel",
                                 json={"reason": "商品下架，整体取消"})
    assert rsp.status_code == 200
    row = query_one("SELECT status FROM tasks WHERE id=%s", (parent_id,))
    assert row["status"] == "cancelled"

    # cleanup
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
    execute("DELETE FROM media_items WHERE product_id=%s", (pid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t4",))


def test_parent_bind_item_endpoint(logged_in_client):
    from appcore.db import execute, query_one
    from appcore.users import create_user, get_by_username
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t5",))
    create_user("_t_tc_t5", "x", role="user")
    tid = get_by_username("_t_tc_t5")["id"]
    execute("INSERT INTO media_products (user_id, name) VALUES (%s, %s)", (tid, "_t_tc_p5"))
    pid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]

    rsp = logged_in_client.post("/tasks/api/parent", json={
        "media_product_id": pid, "media_item_id": None,
        "countries": ["DE"], "translator_id": tid,
    })
    parent_id = rsp.get_json()["parent_task_id"]
    # 之后建一条 en item，用 bind_item 接口绑定
    execute("INSERT INTO media_items (product_id, user_id, filename, object_key, lang) VALUES (%s,%s,%s,%s,%s)",
            (pid, tid, "x.mp4", "k/x.mp4", "en"))
    iid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]

    rsp = logged_in_client.patch(f"/tasks/api/parent/{parent_id}/bind_item",
                                 json={"media_item_id": iid})
    assert rsp.status_code == 200
    row = query_one("SELECT media_item_id FROM tasks WHERE id=%s", (parent_id,))
    assert row["media_item_id"] == iid

    # cleanup
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
    execute("DELETE FROM media_items WHERE product_id=%s", (pid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t5",))
```

- [ ] **Step 2: Run, expect 4 failures (404)**

- [ ] **Step 3: Add to `web/routes/tasks.py`**

```python
@bp.route("/api/parent/<int:tid>/claim", methods=["POST"])
@login_required
@capability_required("can_process_raw_video")
def api_parent_claim(tid: int):
    try:
        tasks_svc.claim_parent(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.ConflictError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"ok": True})


@bp.route("/api/parent/<int:tid>/upload_done", methods=["POST"])
@login_required
def api_parent_upload_done(tid: int):
    try:
        tasks_svc.mark_uploaded(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.StateError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/api/parent/<int:tid>/approve", methods=["POST"])
@login_required
@admin_required
def api_parent_approve(tid: int):
    try:
        tasks_svc.approve_raw(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.StateError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/api/parent/<int:tid>/reject", methods=["POST"])
@login_required
@admin_required
def api_parent_reject(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.reject_raw(task_id=tid, actor_user_id=int(current_user.id),
                             reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/api/parent/<int:tid>/cancel", methods=["POST"])
@login_required
@admin_required
def api_parent_cancel(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.cancel_parent(task_id=tid, actor_user_id=int(current_user.id),
                                reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/api/parent/<int:tid>/bind_item", methods=["PATCH"])
@login_required
def api_parent_bind_item(tid: int):
    """父任务回填 media_item_id；上传后跳转回时调用。"""
    from appcore.db import query_one, execute
    payload = request.get_json(silent=True) or {}
    item_id = payload.get("media_item_id")
    if item_id is None:
        return jsonify({"error": "media_item_id required"}), 400
    row = query_one(
        "SELECT assignee_id, media_product_id FROM tasks "
        "WHERE id=%s AND parent_task_id IS NULL", (tid,)
    )
    if not row:
        return jsonify({"error": "task not found"}), 404
    if row["assignee_id"] != int(current_user.id) and not _is_admin():
        return jsonify({"error": "forbidden"}), 403
    item = query_one(
        "SELECT id FROM media_items WHERE id=%s AND product_id=%s",
        (int(item_id), row["media_product_id"])
    )
    if not item:
        return jsonify({"error": "media_item not found or not under this product"}), 400
    execute("UPDATE tasks SET media_item_id=%s, updated_at=NOW() WHERE id=%s",
            (int(item_id), tid))
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```bash
git add web/routes/tasks.py tests/test_tasks_routes.py
git commit -m "feat(task-center): parent action endpoints + bind_item PATCH"
```

---

### Task 20: 子任务动作端点（submit / approve / reject / cancel）

**Files:**
- Modify: `web/routes/tasks.py`
- Modify: `tests/test_tasks_routes.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_child_lifecycle_endpoints(logged_in_client, monkeypatch):
    from appcore.db import execute, query_one
    from appcore.users import create_user, get_by_username
    from appcore import tasks as tsvc
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t6",))
    create_user("_t_tc_t6", "x", role="user")
    tid = get_by_username("_t_tc_t6")["id"]
    execute("INSERT INTO media_products (user_id, name) VALUES (%s, %s)", (tid, "_t_tc_p6"))
    pid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    execute("INSERT INTO media_items (product_id, user_id, filename, object_key, lang) VALUES (%s,%s,%s,%s,%s)",
            (pid, tid, "x.mp4", "k/x.mp4", "en"))
    iid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]

    rsp = logged_in_client.post("/tasks/api/parent", json={
        "media_product_id": pid, "media_item_id": iid,
        "countries": ["DE"], "translator_id": tid,
    })
    parent_id = rsp.get_json()["parent_task_id"]
    logged_in_client.post(f"/tasks/api/parent/{parent_id}/claim")
    logged_in_client.post(f"/tasks/api/parent/{parent_id}/upload_done")
    logged_in_client.post(f"/tasks/api/parent/{parent_id}/approve")
    de_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,))["id"]

    # 给该子任务把 assignee 改成当前 logged_in_client 的 admin（实际场景翻译员是 tid，
    # 测试简化由 admin 自己 submit 用 monkeypatch readiness 通过）
    execute("UPDATE tasks SET assignee_id=(SELECT id FROM users WHERE username='_test_web_user_') "
            "WHERE id=%s", (de_id,))

    monkeypatch.setattr(tsvc, "_find_target_lang_item",
                        lambda product_id, lang: {"id": 1})
    monkeypatch.setattr("appcore.pushes.compute_readiness",
                        lambda i, p: {"ok": True})
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: True)

    rsp = logged_in_client.post(f"/tasks/api/child/{de_id}/submit")
    assert rsp.status_code == 200

    rsp = logged_in_client.post(f"/tasks/api/child/{de_id}/approve")
    assert rsp.status_code == 200
    row = query_one("SELECT status FROM tasks WHERE id=%s", (parent_id,))
    assert row["status"] == "all_done"

    # cleanup
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
    execute("DELETE FROM media_items WHERE product_id=%s", (pid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t6",))


def test_child_submit_returns_422_on_readiness_fail(logged_in_client, monkeypatch):
    from appcore.db import execute, query_one
    from appcore.users import create_user, get_by_username
    from appcore import tasks as tsvc
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t7",))
    create_user("_t_tc_t7", "x", role="user")
    tid = get_by_username("_t_tc_t7")["id"]
    execute("INSERT INTO media_products (user_id, name) VALUES (%s, %s)", (tid, "_t_tc_p7"))
    pid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    execute("INSERT INTO media_items (product_id, user_id, filename, object_key, lang) VALUES (%s,%s,%s,%s,%s)",
            (pid, tid, "x.mp4", "k/x.mp4", "en"))
    iid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    rsp = logged_in_client.post("/tasks/api/parent", json={
        "media_product_id": pid, "media_item_id": iid,
        "countries": ["DE"], "translator_id": tid,
    })
    parent_id = rsp.get_json()["parent_task_id"]
    logged_in_client.post(f"/tasks/api/parent/{parent_id}/claim")
    logged_in_client.post(f"/tasks/api/parent/{parent_id}/upload_done")
    logged_in_client.post(f"/tasks/api/parent/{parent_id}/approve")
    de_id = query_one("SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
                      (parent_id,))["id"]
    execute("UPDATE tasks SET assignee_id=(SELECT id FROM users WHERE username='_test_web_user_') WHERE id=%s",
            (de_id,))

    monkeypatch.setattr(tsvc, "_find_target_lang_item",
                        lambda product_id, lang: {"id": 1})
    monkeypatch.setattr("appcore.pushes.compute_readiness",
                        lambda i, p: {"has_cover": False, "has_video": True})
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: False)

    rsp = logged_in_client.post(f"/tasks/api/child/{de_id}/submit")
    assert rsp.status_code == 422
    body = rsp.get_json()
    assert "missing" in body and "has_cover" in body["missing"]

    # cleanup
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
    execute("DELETE FROM media_items WHERE product_id=%s", (pid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t7",))
```

- [ ] **Step 2: Run, expect failures**

- [ ] **Step 3: Add to `web/routes/tasks.py`**

```python
@bp.route("/api/child/<int:tid>/submit", methods=["POST"])
@login_required
def api_child_submit(tid: int):
    try:
        tasks_svc.submit_child(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.NotReadyError as e:
        return jsonify({"error": "readiness_failed", "missing": e.missing}), 422
    except tasks_svc.StateError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/api/child/<int:tid>/approve", methods=["POST"])
@login_required
@admin_required
def api_child_approve(tid: int):
    try:
        tasks_svc.approve_child(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.StateError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/api/child/<int:tid>/reject", methods=["POST"])
@login_required
@admin_required
def api_child_reject(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.reject_child(task_id=tid, actor_user_id=int(current_user.id),
                               reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/api/child/<int:tid>/cancel", methods=["POST"])
@login_required
@admin_required
def api_child_cancel(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.cancel_child(task_id=tid, actor_user_id=int(current_user.id),
                               reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```bash
git add web/routes/tasks.py tests/test_tasks_routes.py
git commit -m "feat(task-center): child action endpoints + readiness gate response"
```

---

### Task 21: GET `/tasks/api/<id>/events` — 审计流

**Files:**
- Modify: `web/routes/tasks.py`
- Modify: `tests/test_tasks_routes.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_api_events_returns_history(logged_in_client):
    from appcore.db import execute, query_one
    from appcore.users import create_user, get_by_username
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t8",))
    create_user("_t_tc_t8", "x", role="user")
    tid = get_by_username("_t_tc_t8")["id"]
    execute("INSERT INTO media_products (user_id, name) VALUES (%s, %s)", (tid, "_t_tc_p8"))
    pid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    execute("INSERT INTO media_items (product_id, user_id, filename, object_key, lang) VALUES (%s,%s,%s,%s,%s)",
            (pid, tid, "x.mp4", "k/x.mp4", "en"))
    iid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    rsp = logged_in_client.post("/tasks/api/parent", json={
        "media_product_id": pid, "media_item_id": iid,
        "countries": ["DE"], "translator_id": tid,
    })
    parent_id = rsp.get_json()["parent_task_id"]
    logged_in_client.post(f"/tasks/api/parent/{parent_id}/claim")

    rsp = logged_in_client.get(f"/tasks/api/{parent_id}/events")
    assert rsp.status_code == 200
    events = rsp.get_json()["events"]
    types = [e["event_type"] for e in events]
    assert "created" in types
    assert "claimed" in types

    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
    execute("DELETE FROM media_items WHERE product_id=%s", (pid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t8",))
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Add to `web/routes/tasks.py`**

```python
@bp.route("/api/<int:tid>/events", methods=["GET"])
@login_required
def api_events(tid: int):
    from appcore.db import query_all
    rows = query_all(
        "SELECT te.*, u.username AS actor_username "
        "FROM task_events te LEFT JOIN users u ON u.id=te.actor_user_id "
        "WHERE te.task_id=%s ORDER BY te.id ASC",
        (tid,),
    )
    events = [
        {
            "id": r["id"],
            "task_id": r["task_id"],
            "event_type": r["event_type"],
            "actor_user_id": r["actor_user_id"],
            "actor_username": r["actor_username"],
            "payload_json": r["payload_json"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]
    return jsonify({"events": events})
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```bash
git add web/routes/tasks.py tests/test_tasks_routes.py
git commit -m "feat(task-center): GET /tasks/api/<id>/events audit stream"
```

---

## Phase 4 — Frontend

### Task 22: layout.html 加菜单项

**Files:**
- Modify: `web/templates/layout.html`

- [ ] **Step 1: Find the existing 推送管理 nav block** (around line 433)

- [ ] **Step 2: Insert "任务中心" menu item right after "推送管理"**

```html
{% if has_permission('task_center') %}
<a href="/tasks/" target="_blank" rel="noopener noreferrer" {% if request.path.startswith('/tasks/') and not request.path.startswith('/tasks/api') %}class="active"{% endif %}>
  <span class="nav-icon">📋</span> 任务中心
</a>
{% endif %}
```

- [ ] **Step 3: Smoke check via existing route test**

Run: `python -m pytest tests/test_tasks_routes.py::test_index_renders_for_admin -q`
Expected: PASS (already passing; this just verifies layout still renders).

- [ ] **Step 4: Commit**

```bash
git add web/templates/layout.html
git commit -m "feat(task-center): add 任务中心 menu item to layout"
```

---

### Task 23: tasks_list.html 页面骨架 + Tab 切换 + 全局筛选

**Files:**
- Modify: `web/templates/tasks_list.html` (replace placeholder from Task 15)

- [ ] **Step 1: Replace `tasks_list.html` with full skeleton**

```html
{% extends "layout.html" %}
{% block title %}任务中心 - AutoVideoSrt{% endblock %}
{% block page_title %}任务中心{% endblock %}
{% block extra_style %}
:root {
  --tc-bg: oklch(99% 0.004 230);
  --tc-bg-subtle: oklch(97% 0.006 230);
  --tc-bg-muted: oklch(94% 0.010 230);
  --tc-border: oklch(91% 0.012 230);
  --tc-border-strong: oklch(84% 0.015 230);
  --tc-fg: oklch(22% 0.020 235);
  --tc-fg-muted: oklch(48% 0.018 230);
  --tc-accent: oklch(56% 0.16 230);
  --tc-accent-hover: oklch(50% 0.17 230);
  --tc-accent-subtle: oklch(94% 0.04 225);
  --tc-success-bg: oklch(95% 0.04 165);
  --tc-success-fg: oklch(38% 0.09 165);
  --tc-warning-bg: oklch(96% 0.05 85);
  --tc-warning-fg: oklch(42% 0.10 60);
  --tc-danger: oklch(58% 0.18 25);
  --tc-danger-bg: oklch(96% 0.04 25);
  --tc-danger-fg: oklch(42% 0.14 25);
  --tc-r: 6px; --tc-r-md: 8px; --tc-r-lg: 12px;
  --tc-sp-2: 8px; --tc-sp-3: 12px; --tc-sp-4: 16px;
  --tc-sp-5: 20px; --tc-sp-6: 24px;
}
.tc { font-family: "Inter Tight", "PingFang SC", "Microsoft YaHei", sans-serif; color: var(--tc-fg); }
.tc * { box-sizing: border-box; }
.tc-header { display:flex; align-items:center; justify-content:space-between; gap:var(--tc-sp-4); margin-bottom:var(--tc-sp-5); flex-wrap:wrap; }
.tc-tabs { display:flex; gap:var(--tc-sp-2); border-bottom:1px solid var(--tc-border); margin-bottom:var(--tc-sp-4); }
.tc-tab { background:transparent; border:none; padding:8px 14px; font-size:14px; color:var(--tc-fg-muted); cursor:pointer; border-bottom:2px solid transparent; }
.tc-tab.active { color:var(--tc-accent); border-bottom-color:var(--tc-accent); }
.tc-filters { display:flex; gap:var(--tc-sp-2); flex-wrap:wrap; align-items:center; margin-bottom:var(--tc-sp-4); }
.tc-input { height:32px; padding:0 10px; border:1px solid var(--tc-border-strong); border-radius:var(--tc-r); font-size:13px; }
.tc-input:focus { border-color:var(--tc-accent); outline:none; box-shadow:0 0 0 2px var(--tc-accent-subtle); }
.tc-btn { height:32px; padding:0 14px; border:1px solid var(--tc-border-strong); border-radius:var(--tc-r); background:var(--tc-bg); cursor:pointer; font-size:13px; }
.tc-btn:hover { background:var(--tc-bg-muted); }
.tc-btn--primary { background:var(--tc-accent); color:#fff; border:none; }
.tc-btn--primary:hover { background:var(--tc-accent-hover); }
.tc-btn--danger { background:var(--tc-danger); color:#fff; border:none; }
.tc-table { width:100%; border-collapse:collapse; font-size:13px; }
.tc-table th { text-align:left; padding:10px 12px; background:var(--tc-bg-subtle); border-bottom:2px solid var(--tc-border); font-weight:600; color:var(--tc-fg-muted); }
.tc-table td { padding:10px 12px; border-bottom:1px solid var(--tc-border); }
.tc-table tr:hover td { background:var(--tc-bg-subtle); }
.tc-badge { display:inline-flex; padding:2px 8px; border-radius:9999px; font-size:11px; font-weight:500; }
.tc-badge--in_progress { background:var(--tc-bg-muted); color:var(--tc-fg-muted); }
.tc-badge--completed { background:var(--tc-success-bg); color:var(--tc-success-fg); }
.tc-badge--terminated { background:var(--tc-danger-bg); color:var(--tc-danger-fg); }
.tc-empty { padding:60px 20px; text-align:center; color:var(--tc-fg-muted); }
{% endblock %}
{% block content %}
<div id="tcRoot" class="tc">
  <div class="tc-header">
    <div>
      <h1 style="margin:0; font-size:22px; font-weight:600;">任务中心</h1>
      <p style="margin:4px 0 0; font-size:13px; color:var(--tc-fg-muted);">管理素材原始视频处理与翻译任务</p>
    </div>
  </div>

  <div class="tc-tabs">
    <button class="tc-tab active" data-tab="mine" id="tcTabMine">我的任务</button>
    {% if is_admin %}
    <button class="tc-tab" data-tab="all" id="tcTabAll">全部任务</button>
    <button class="tc-tab" data-tab="dispatch" id="tcTabDispatch">待派单素材</button>
    {% endif %}
  </div>

  <div class="tc-filters" id="tcFilters">
    <input type="text" id="tcKeyword" class="tc-input" placeholder="搜索产品名" style="width:240px;">
    <select id="tcStatus" class="tc-input">
      <option value="">全部状态</option>
      <option value="in_progress">进行中</option>
      <option value="completed">已完成</option>
      <option value="terminated">终止</option>
    </select>
    <button class="tc-btn" id="tcRefresh">刷新</button>
  </div>

  <div id="tcTableWrap"></div>
</div>

<script>
const TC_IS_ADMIN = {{ 'true' if is_admin else 'false' }};
const TC_CAPS = {
  can_process_raw_video: {{ 'true' if capabilities.can_process_raw_video else 'false' }},
  can_translate: {{ 'true' if capabilities.can_translate else 'false' }},
};
let TC_CURRENT_TAB = 'mine';

function tcEsc(s) { const d = document.createElement('div'); d.textContent = s ?? ''; return d.innerHTML; }

async function tcFetchJson(url, opts) {
  const rsp = await fetch(url, Object.assign({headers:{'Content-Type':'application/json'}}, opts || {}));
  if (!rsp.ok) {
    const body = await rsp.json().catch(() => ({error: rsp.statusText}));
    throw new Error(body.error || rsp.statusText);
  }
  return rsp.json();
}

document.querySelectorAll('.tc-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tc-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    TC_CURRENT_TAB = btn.dataset.tab;
    tcRender();
  });
});
document.getElementById('tcRefresh').addEventListener('click', tcRender);
document.getElementById('tcKeyword').addEventListener('keyup', e => { if (e.key === 'Enter') tcRender(); });
document.getElementById('tcStatus').addEventListener('change', tcRender);

async function tcRender() {
  const wrap = document.getElementById('tcTableWrap');
  wrap.innerHTML = '<div class="tc-empty">加载中...</div>';
  try {
    if (TC_CURRENT_TAB === 'dispatch') {
      await tcRenderDispatchPool(wrap);
    } else {
      await tcRenderTaskList(wrap, TC_CURRENT_TAB);
    }
  } catch (e) {
    wrap.innerHTML = `<div class="tc-empty">加载失败：${tcEsc(e.message)}</div>`;
  }
}

async function tcRenderTaskList(wrap, tab) {
  const kw = document.getElementById('tcKeyword').value.trim();
  const status = document.getElementById('tcStatus').value;
  const params = new URLSearchParams({tab, keyword: kw, status});
  const data = await tcFetchJson('/tasks/api/list?' + params.toString());
  if (!data.items.length) { wrap.innerHTML = '<div class="tc-empty">暂无任务</div>'; return; }
  const rows = data.items.map(it => {
    const kind = it.parent_task_id ? '子任务' : '父任务';
    const country = it.country_code ? tcEsc(it.country_code) : '—';
    return `<tr data-id="${it.id}" data-parent="${it.parent_task_id ?? ''}">
      <td>${tcEsc(it.product_name)}</td>
      <td>${kind}</td>
      <td>${country}</td>
      <td><span class="tc-badge tc-badge--${tcEsc(it.high_level)}">${tcEsc(it.high_level)}</span></td>
      <td>${tcEsc(it.status)}</td>
      <td>${tcEsc(it.assignee_username || '—')}</td>
      <td><button class="tc-btn" onclick="tcOpenDetail(${it.id})">详情</button></td>
    </tr>`;
  }).join('');
  wrap.innerHTML = `<table class="tc-table"><thead><tr>
    <th>产品</th><th>类型</th><th>国家</th><th>高层</th><th>子状态</th><th>负责人</th><th>操作</th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}
async function tcRenderDispatchPool(wrap) { wrap.innerHTML = '<div class="tc-empty">待派单素材列表 — Task 25 实现</div>'; }
function tcOpenDetail(id) { alert('详情抽屉 — Task 27 实现 (id=' + id + ')'); }
tcRender();
</script>
{% endblock %}
```

- [ ] **Step 2: Smoke check rendering**

Run: `python -m pytest tests/test_tasks_routes.py::test_index_renders_for_admin -q`
Expected: PASS.

- [ ] **Step 3: Manual visual check (record outcome in commit message)**

Open `/tasks/` in browser; confirm 3 tabs appear for admin, "我的任务" loads (likely empty or with prior test data).

- [ ] **Step 4: Commit**

```bash
git add web/templates/tasks_list.html
git commit -m "feat(task-center): tasks_list page skeleton with tab + filter UI"
```

---

### Task 24: 表格渲染 — "我的任务" / "全部任务"

> 已在 Task 23 的 `tcRenderTaskList` 实现。本任务只补一个最小测试 + 视觉检查。

**Files:**
- Modify: `tests/test_tasks_routes.py`

- [ ] **Step 1: Write a smoke test**

Append:

```python
def test_index_html_contains_tab_buttons(logged_in_client):
    rsp = logged_in_client.get("/tasks/")
    body = rsp.data.decode("utf-8")
    assert 'data-tab="mine"' in body
    assert 'data-tab="all"' in body
    assert "tcRender" in body  # JS bootstrapped
```

- [ ] **Step 2: Run, expect pass**

Run: `python -m pytest tests/test_tasks_routes.py -q`

- [ ] **Step 3: Commit (no code change beyond test)**

```bash
git add tests/test_tasks_routes.py
git commit -m "test(task-center): smoke check tasks_list HTML contains tab buttons"
```

---

### Task 25: "待派单素材" Tab 表格 + 创建按钮

**Files:**
- Modify: `web/templates/tasks_list.html`

- [ ] **Step 1: Replace `tcRenderDispatchPool` function**

Find in `tasks_list.html` script:

```javascript
async function tcRenderDispatchPool(wrap) { wrap.innerHTML = '<div class="tc-empty">待派单素材列表 — Task 25 实现</div>'; }
```

Replace with:

```javascript
async function tcRenderDispatchPool(wrap) {
  const data = await tcFetchJson('/tasks/api/dispatch_pool');
  if (!data.items.length) { wrap.innerHTML = '<div class="tc-empty">没有待派单的素材（所有产品都已有活跃任务）</div>'; return; }
  const rows = data.items.map(it => `<tr>
    <td>${tcEsc(it.product_name)}</td>
    <td>${it.en_item_count}</td>
    <td><button class="tc-btn tc-btn--primary" onclick="tcOpenCreateModal(${it.product_id}, '${tcEsc(it.product_name)}', ${it.owner_id})">创建任务</button></td>
  </tr>`).join('');
  wrap.innerHTML = `<table class="tc-table"><thead><tr>
    <th>产品名</th><th>已有英文 item 数</th><th>操作</th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}
function tcOpenCreateModal(pid, pname, ownerId) { alert('创建任务弹窗 — Task 26 实现 (产品=' + pname + ')'); }
```

- [ ] **Step 2: Manual visual check**

Click "待派单素材" tab → 看到列表（如果 DB 有产品）。

- [ ] **Step 3: Commit**

```bash
git add web/templates/tasks_list.html
git commit -m "feat(task-center): dispatch pool tab with create-task buttons"
```

---

### Task 26: 创建任务 modal

**Files:**
- Modify: `web/templates/tasks_list.html`

- [ ] **Step 1: Add modal HTML in `tasks_list.html` content block**

Insert before the closing `</div>` of `#tcRoot`:

```html
<div id="tcCreateModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.4); z-index:1000;">
  <div style="position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); background:#fff; border-radius:12px; padding:24px; width:520px; max-width:90vw;">
    <h3 style="margin:0 0 16px;">创建任务</h3>
    <div id="tcCreateModalBody"></div>
    <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:20px;">
      <button class="tc-btn" onclick="tcCloseCreateModal()">取消</button>
      <button class="tc-btn tc-btn--primary" onclick="tcSubmitCreate()">创建并分配</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Replace `tcOpenCreateModal` and add helpers**

Find:

```javascript
function tcOpenCreateModal(pid, pname, ownerId) { alert('创建任务弹窗 — Task 26 实现 (产品=' + pname + ')'); }
```

Replace with:

```javascript
let TC_CREATE_CTX = null;
async function tcOpenCreateModal(pid, pname, ownerId) {
  TC_CREATE_CTX = {product_id: pid, product_name: pname, owner_id: ownerId};
  const body = document.getElementById('tcCreateModalBody');
  body.innerHTML = '加载中...';
  document.getElementById('tcCreateModal').style.display = 'block';
  try {
    // 拉该产品的 en items + 翻译员候选 + 国家清单
    const [itemsResp, transResp, langsResp] = await Promise.all([
      tcFetchJson('/medias/api/products/' + pid + '/items?lang=en'),
      tcFetchJson('/tasks/api/translators'),
      tcFetchJson('/tasks/api/languages'),
    ]);
    const items = itemsResp.items || [];
    const translators = transResp.translators || [];
    const langs = langsResp.languages || [];
    const itemOpts = items.map(i => `<option value="${i.id}">${tcEsc(i.filename)}</option>`).join('');
    const isOldProduct = ownerId && translators.some(t => t.id === ownerId);
    const transOpts = translators.map(t => `<option value="${t.id}" ${t.id === ownerId ? 'selected' : ''}>${tcEsc(t.username)}</option>`).join('');
    const langChecks = langs.map(l => `<label style="display:inline-block; padding:4px 8px;"><input type="checkbox" value="${l.code}"> ${tcEsc(l.code)}</label>`).join('');
    body.innerHTML = `
      <div style="margin-bottom:12px;"><strong>${tcEsc(pname)}</strong></div>
      <div style="margin-bottom:12px;">
        <label>原始素材（英文 item）：</label>
        <select id="tcCreateItem" class="tc-input" style="width:100%;">
          ${items.length ? itemOpts : '<option value="">该产品下还没有英文 item</option>'}
        </select>
        ${items.length ? '' : '<div style="color:var(--tc-danger-fg); font-size:12px; margin-top:4px;"><a href="/medias/" target="_blank">先去素材管理上传英文原片</a></div>'}
      </div>
      <div style="margin-bottom:12px;">
        <label>翻译员：${isOldProduct ? '<span style="color:var(--tc-fg-muted); font-size:12px;">(老品自动沿用产品负责人)</span>' : ''}</label>
        <select id="tcCreateTranslator" class="tc-input" style="width:100%;" ${isOldProduct ? 'disabled' : ''}>
          ${transOpts}
        </select>
      </div>
      <div style="margin-bottom:12px;">
        <label>目标国家（≥1）：</label>
        <div id="tcCreateLangs">${langChecks}</div>
      </div>
    `;
  } catch (e) {
    body.innerHTML = '<div style="color:var(--tc-danger-fg);">加载失败：' + tcEsc(e.message) + '</div>';
  }
}
function tcCloseCreateModal() { document.getElementById('tcCreateModal').style.display = 'none'; TC_CREATE_CTX = null; }
async function tcSubmitCreate() {
  if (!TC_CREATE_CTX) return;
  const itemEl = document.getElementById('tcCreateItem');
  const transEl = document.getElementById('tcCreateTranslator');
  const item_id = itemEl.value ? parseInt(itemEl.value) : null;
  const translator_id = transEl.value ? parseInt(transEl.value) : null;
  const countries = Array.from(document.querySelectorAll('#tcCreateLangs input:checked')).map(cb => cb.value);
  if (!item_id) { alert('请选英文素材'); return; }
  if (!translator_id) { alert('请选翻译员'); return; }
  if (!countries.length) { alert('至少勾一个国家'); return; }
  try {
    await tcFetchJson('/tasks/api/parent', {
      method: 'POST',
      body: JSON.stringify({media_product_id: TC_CREATE_CTX.product_id, media_item_id: item_id, countries, translator_id}),
    });
    tcCloseCreateModal();
    tcRender();
  } catch (e) { alert('创建失败：' + e.message); }
}
```

- [ ] **Step 3: Add 2 supporting GET endpoints to `web/routes/tasks.py`**

```python
@bp.route("/api/translators", methods=["GET"])
@login_required
def api_translators():
    from appcore.db import query_all
    rows = query_all(
        "SELECT id, username FROM users "
        "WHERE is_active=1 AND role <> 'superadmin' "
        "AND JSON_EXTRACT(COALESCE(permissions, '{}'), '$.can_translate') = TRUE "
        "ORDER BY username"
    )
    return jsonify({"translators": [{"id": r["id"], "username": r["username"]} for r in rows]})


@bp.route("/api/languages", methods=["GET"])
@login_required
def api_languages():
    from appcore.db import query_all
    rows = query_all(
        "SELECT lang FROM media_languages "
        "WHERE enabled=1 AND lang <> 'en' ORDER BY lang"
    )
    return jsonify({"languages": [{"code": r["lang"].upper()} for r in rows]})
```

- [ ] **Step 4: Add a helper endpoint** to fetch items by product+lang (or use existing if it exists). Search first:

```bash
grep -n 'def.*products.*items\|/api/products/.*items' g:/Code/AutoVideoSrtLocal/.worktrees/task-center/web/routes/medias.py | head
```

If an endpoint exists matching `/medias/api/products/<id>/items`, use it. Otherwise add to `web/routes/tasks.py`:

```python
@bp.route("/api/product/<int:pid>/en_items", methods=["GET"])
@login_required
def api_product_en_items(pid: int):
    from appcore.db import query_all
    rows = query_all(
        "SELECT id, filename, object_key FROM media_items "
        "WHERE product_id=%s AND lang='en' AND deleted_at IS NULL ORDER BY id DESC",
        (pid,),
    )
    return jsonify({"items": [{"id": r["id"], "filename": r["filename"]} for r in rows]})
```

Then update the modal JS to call `/tasks/api/product/<pid>/en_items` instead of the medias endpoint.

- [ ] **Step 5: Manual visual check**

Open dispatch tab → click 创建任务 → modal 显示，能选 item / 翻译员 / 国家 → 提交后跳转回 mine tab 看到新任务。

- [ ] **Step 6: Commit**

```bash
git add web/templates/tasks_list.html web/routes/tasks.py
git commit -m "feat(task-center): create-task modal + supporting GET endpoints"
```

---

### Task 27: 任务详情抽屉 + 操作按钮 + 审计流

**Files:**
- Modify: `web/templates/tasks_list.html`

- [ ] **Step 1: Add detail drawer HTML before closing `</div>` of `#tcRoot`**

```html
<div id="tcDetailDrawer" style="display:none; position:fixed; top:0; right:0; bottom:0; width:540px; max-width:90vw; background:#fff; border-left:1px solid var(--tc-border); box-shadow:-4px 0 12px rgba(0,0,0,0.08); z-index:999; overflow-y:auto; padding:24px;">
  <button class="tc-btn" style="position:absolute; top:12px; right:12px;" onclick="tcCloseDetail()">关闭</button>
  <div id="tcDetailBody"></div>
</div>
```

- [ ] **Step 2: Replace `tcOpenDetail` and add helpers**

Find:

```javascript
function tcOpenDetail(id) { alert('详情抽屉 — Task 27 实现 (id=' + id + ')'); }
```

Replace with:

```javascript
let TC_DETAIL_TASK_ID = null;
async function tcOpenDetail(id) {
  TC_DETAIL_TASK_ID = id;
  const drawer = document.getElementById('tcDetailDrawer');
  const body = document.getElementById('tcDetailBody');
  body.innerHTML = '加载中...';
  drawer.style.display = 'block';
  try {
    const [taskResp, eventsResp] = await Promise.all([
      tcFetchJson('/tasks/api/list?tab=' + (TC_IS_ADMIN ? 'all' : 'mine')),
      tcFetchJson('/tasks/api/' + id + '/events'),
    ]);
    const task = (taskResp.items || []).find(t => t.id === id);
    if (!task) { body.innerHTML = '任务未找到（可能不在当前可见范围）'; return; }
    body.innerHTML = tcRenderDetail(task, eventsResp.events || []);
  } catch (e) {
    body.innerHTML = '加载失败：' + tcEsc(e.message);
  }
}
function tcCloseDetail() { document.getElementById('tcDetailDrawer').style.display = 'none'; TC_DETAIL_TASK_ID = null; }

function tcRenderDetail(task, events) {
  const isParent = !task.parent_task_id;
  const buttons = tcDetailButtons(task, isParent);
  const evRows = events.map(e => `<div style="padding:6px 0; border-bottom:1px dashed var(--tc-border); font-size:12px;">
    <div><strong>${tcEsc(e.event_type)}</strong> by ${tcEsc(e.actor_username || '系统')}</div>
    <div style="color:var(--tc-fg-muted);">${tcEsc(e.created_at)}</div>
    ${e.payload_json ? `<pre style="margin:4px 0; font-size:11px; color:var(--tc-fg-muted);">${tcEsc(JSON.stringify(e.payload_json))}</pre>` : ''}
  </div>`).join('');
  return `
    <h3 style="margin:0 0 8px;">${tcEsc(task.product_name)}</h3>
    <div style="margin-bottom:8px;"><span class="tc-badge tc-badge--${tcEsc(task.high_level)}">${tcEsc(task.high_level)}</span> · ${tcEsc(task.status)}</div>
    <div style="font-size:13px; color:var(--tc-fg-muted); margin-bottom:16px;">
      ${isParent ? '父任务（原始视频段）' : '子任务（' + tcEsc(task.country_code) + ' 翻译段）'} ·
      负责人：${tcEsc(task.assignee_username || '—')}
    </div>
    ${task.last_reason ? `<div style="background:var(--tc-warning-bg); color:var(--tc-warning-fg); padding:10px; border-radius:8px; margin-bottom:12px; font-size:13px;">最近备注：${tcEsc(task.last_reason)}</div>` : ''}
    <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:20px;">${buttons}</div>
    <h4 style="margin:0 0 8px; font-size:13px;">审计流</h4>
    <div>${evRows || '<div style="color:var(--tc-fg-muted); font-size:12px;">暂无事件</div>'}</div>
  `;
}

function tcDetailButtons(task, isParent) {
  const id = task.id;
  const status = task.status;
  const btns = [];
  if (isParent) {
    if (status === 'pending' && TC_CAPS.can_process_raw_video) btns.push(`<button class="tc-btn tc-btn--primary" onclick="tcAction('parent', ${id}, 'claim')">认领</button>`);
    if (status === 'raw_in_progress') btns.push(`<button class="tc-btn tc-btn--primary" onclick="tcParentUploadDone(${id})">已上传</button>`);
    if (status === 'raw_review' && TC_IS_ADMIN) {
      btns.push(`<button class="tc-btn tc-btn--primary" onclick="tcAction('parent', ${id}, 'approve')">通过</button>`);
      btns.push(`<button class="tc-btn" onclick="tcOpenReason('parent', ${id}, 'reject')">打回</button>`);
    }
    if (TC_IS_ADMIN && ['pending','raw_in_progress','raw_review','raw_done'].includes(status)) {
      btns.push(`<button class="tc-btn tc-btn--danger" onclick="tcOpenReason('parent', ${id}, 'cancel')">取消</button>`);
    }
  } else {
    if (status === 'assigned') {
      btns.push(`<button class="tc-btn" onclick="tcChildJumpTranslate(${id}, '${tcEsc(task.country_code)}', ${task.media_product_id})">翻译</button>`);
      btns.push(`<button class="tc-btn" onclick="tcChildJumpHistory(${id}, '${tcEsc(task.country_code)}', ${task.media_product_id})">翻译任务记录</button>`);
      btns.push(`<button class="tc-btn tc-btn--primary" onclick="tcAction('child', ${id}, 'submit')">提交完成</button>`);
    }
    if (status === 'review' && TC_IS_ADMIN) {
      btns.push(`<button class="tc-btn tc-btn--primary" onclick="tcAction('child', ${id}, 'approve')">通过</button>`);
      btns.push(`<button class="tc-btn" onclick="tcOpenReason('child', ${id}, 'reject')">打回</button>`);
    }
    if (TC_IS_ADMIN && ['blocked','assigned','review'].includes(status)) {
      btns.push(`<button class="tc-btn tc-btn--danger" onclick="tcOpenReason('child', ${id}, 'cancel')">取消</button>`);
    }
  }
  return btns.join('');
}

async function tcAction(kind, id, action) {
  try {
    await tcFetchJson(`/tasks/api/${kind}/${id}/${action}`, {method:'POST', body:'{}'});
    tcRender();
    if (TC_DETAIL_TASK_ID === id) tcOpenDetail(id);
  } catch (e) {
    if (e.message === 'readiness_failed') {
      // try to read missing list
      alert('未通过产物齐全检查，请去素材管理补齐封面/视频/文案后再提交');
    } else {
      alert(action + ' 失败：' + e.message);
    }
  }
}

function tcChildJumpTranslate(taskId, country, productId) {
  const url = `/medias/?from_task=${taskId}&product=${productId}&lang=${country.toLowerCase()}&action=translate`;
  window.open(url, '_blank');
}
function tcChildJumpHistory(taskId, country, productId) {
  const url = `/medias/?from_task=${taskId}&product=${productId}&lang=${country.toLowerCase()}&action=history`;
  window.open(url, '_blank');
}
async function tcParentUploadDone(id) {
  // 简化版：直接调 upload_done；如果 media_item 缺失，后端会报错提示
  try {
    await tcFetchJson(`/tasks/api/parent/${id}/upload_done`, {method:'POST', body:'{}'});
    tcRender();
    if (TC_DETAIL_TASK_ID === id) tcOpenDetail(id);
  } catch (e) {
    if (e.message && e.message.includes('media_item')) {
      const goUpload = confirm('该任务还没有绑定原始素材。是否先去素材管理上传？');
      if (goUpload) {
        // 跳转去上传，回跳逻辑见 Task 29
        const productId = prompt('product id?'); // Task 29 时改为从 task 数据带过去
        window.open(`/medias/?from_task=${id}&product=${productId}&action=upload_en`, '_blank');
      }
    } else {
      alert(e.message);
    }
  }
}
```

- [ ] **Step 3: Manual visual check** —— 列表中点详情 → 抽屉打开 → 看到状态徽章 / 操作按钮 / 审计流。

- [ ] **Step 4: Commit**

```bash
git add web/templates/tasks_list.html
git commit -m "feat(task-center): detail drawer with action buttons + audit stream"
```

---

### Task 28: 打回 / 取消 modals

**Files:**
- Modify: `web/templates/tasks_list.html`

- [ ] **Step 1: Add reason modal HTML before closing `</div>` of `#tcRoot`**

```html
<div id="tcReasonModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.4); z-index:1100;">
  <div style="position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); background:#fff; border-radius:12px; padding:24px; width:480px; max-width:90vw;">
    <h3 id="tcReasonTitle" style="margin:0 0 12px;"></h3>
    <textarea id="tcReasonText" class="tc-input" style="width:100%; height:120px; padding:8px; resize:vertical;" placeholder="请输入原因（≥10 字符）"></textarea>
    <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:16px;">
      <button class="tc-btn" onclick="tcCloseReason()">取消</button>
      <button class="tc-btn tc-btn--danger" id="tcReasonSubmit">确认</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add JS helpers**

Append in script:

```javascript
let TC_REASON_CTX = null;
function tcOpenReason(kind, id, action) {
  TC_REASON_CTX = {kind, id, action};
  document.getElementById('tcReasonTitle').textContent =
    action === 'cancel' ? '取消任务（不可撤销）' : '打回任务';
  document.getElementById('tcReasonText').value = '';
  document.getElementById('tcReasonModal').style.display = 'block';
  document.getElementById('tcReasonSubmit').onclick = tcSubmitReason;
}
function tcCloseReason() { document.getElementById('tcReasonModal').style.display = 'none'; TC_REASON_CTX = null; }
async function tcSubmitReason() {
  if (!TC_REASON_CTX) return;
  const reason = document.getElementById('tcReasonText').value.trim();
  if (reason.length < 10) { alert('原因至少 10 字符'); return; }
  if (TC_REASON_CTX.action === 'cancel' && !confirm('取消后非已完成的子任务也会一起终止，不可撤销，确认？')) return;
  try {
    await tcFetchJson(`/tasks/api/${TC_REASON_CTX.kind}/${TC_REASON_CTX.id}/${TC_REASON_CTX.action}`, {
      method: 'POST',
      body: JSON.stringify({reason}),
    });
    const id = TC_REASON_CTX.id;
    tcCloseReason();
    tcRender();
    if (TC_DETAIL_TASK_ID === id) tcOpenDetail(id);
  } catch (e) { alert('失败：' + e.message); }
}
```

- [ ] **Step 3: Manual visual check** — 在详情抽屉点"打回" → modal → 输入 < 10 字符提示 → 输入足够 → 成功翻转状态。

- [ ] **Step 4: Commit**

```bash
git add web/templates/tasks_list.html
git commit -m "feat(task-center): reason modal for reject/cancel actions"
```

---

## Phase 5 — Cross-Page Integration (best effort)

### Task 29: 父任务"已上传"跳转流（best effort）

**Files:**
- Modify: `web/templates/tasks_list.html`
- Possibly modify: `web/templates/_medias_edit_modal.html` or `_medias_edit_detail_modal.html`

> **Risk acknowledgment**: 这一步可能改不动素材管理的上传 callback。如果跑不通，**退化方案**：
> 任务中心父任务详情页的 [ 已上传 ] 按钮直接弹一个表单："请输入您刚上传的 media_item 的 id"，然后调 PATCH `/tasks/api/parent/<id>/bind_item` 绑定。这个退化方案不需要改素材管理，符合 spec 5.3 的退化设计。

- [ ] **Step 1: Search for upload success callback in medias modal**

```bash
grep -n 'upload.*success\|onUploadComplete\|after.*upload\|then.*upload' g:/Code/AutoVideoSrtLocal/.worktrees/task-center/web/templates/_medias_edit_modal.html
grep -n 'from_task' g:/Code/AutoVideoSrtLocal/.worktrees/task-center/web/templates/medias_list.html | head
```

- [ ] **Step 2: If a clean hook point is found** (e.g., a JS function with item_id available after upload), add at the end:

```javascript
// task-center hook
const params = new URLSearchParams(window.location.search);
if (params.get('from_task') && newItemId) {
  window.location.href = `/tasks/?focus=${params.get('from_task')}&new_item=${newItemId}`;
}
```

And add to `tasks_list.html` at script start:

```javascript
const focusParams = new URLSearchParams(window.location.search);
const focusTaskId = focusParams.get('focus');
const focusItemId = focusParams.get('new_item');
if (focusTaskId && focusItemId) {
  // PATCH bind_item then 弹 confirm 进 raw_review
  (async () => {
    try {
      await tcFetchJson(`/tasks/api/parent/${focusTaskId}/bind_item`, {
        method: 'PATCH',
        body: JSON.stringify({media_item_id: parseInt(focusItemId)}),
      });
      if (confirm('原始视频已上传完成。确认进入审核？')) {
        await tcFetchJson(`/tasks/api/parent/${focusTaskId}/upload_done`, {method: 'POST', body: '{}'});
      }
      tcOpenDetail(parseInt(focusTaskId));
    } catch (e) { alert('回填失败：' + e.message); }
  })();
}
```

- [ ] **Step 3: If hook point NOT clean, apply退化方案** —— 把 `tcParentUploadDone` 替换为：

```javascript
async function tcParentUploadDone(id) {
  const itemIdStr = prompt('请输入您在素材管理刚上传的英文 item ID（可在素材管理页 URL 或行 ID 看到）：');
  if (!itemIdStr) return;
  const itemId = parseInt(itemIdStr);
  if (!itemId) { alert('无效 ID'); return; }
  try {
    await tcFetchJson(`/tasks/api/parent/${id}/bind_item`, {
      method: 'PATCH',
      body: JSON.stringify({media_item_id: itemId}),
    });
    await tcFetchJson(`/tasks/api/parent/${id}/upload_done`, {method:'POST', body:'{}'});
    tcRender();
    if (TC_DETAIL_TASK_ID === id) tcOpenDetail(id);
  } catch (e) { alert('失败：' + e.message); }
}
```

- [ ] **Step 4: Document the chosen path in commit message**

- [ ] **Step 5: Commit**

```bash
git add web/templates/tasks_list.html web/templates/_medias_edit_modal.html
git commit -m "feat(task-center): upload-done flow (degraded prompt fallback if needed)"
```

---

## Phase 6 — Final Verification

### Task 30: 全测试 + 手动验收清单走查

**Files:** N/A (verification only)

- [ ] **Step 1: Run all related tests**

```bash
cd g:/Code/AutoVideoSrtLocal/.worktrees/task-center
python -m pytest tests/test_db_migration_tasks_tables.py tests/test_appcore_permissions_task_capabilities.py tests/test_appcore_tasks.py tests/test_tasks_routes.py -q 2>&1 | tail -30
```
Expected: All green.

- [ ] **Step 2: Run full project test suite (regression check)**

```bash
python -m pytest tests/ -q --ignore=tests/e2e 2>&1 | tail -30
```
Expected: No new failures (any pre-existing failures should be unchanged).

- [ ] **Step 3: Manual acceptance checklist** — open the app and verify:

- [ ] 任务中心菜单出现，所有用户能进
- [ ] "待派单素材" Tab 列出"无活跃父任务"的产品
- [ ] 创建任务弹窗：老品自动回填翻译员且禁用编辑
- [ ] 创建任务弹窗：新品翻译员下拉只显示有 `can_translate` 的非 admin
- [ ] 处理人在"我的任务"看到 pending 池 + 自己已认领的
- [ ] 翻译员在"我的任务"只看到 product owner = 自己且 status ≠ blocked 的子任务
- [ ] 父任务"已上传"按钮工作（跳转或退化版 prompt）
- [ ] 子任务"翻译"按钮跳素材管理（带 query string）
- [ ] readiness gate：故意不传封面就 submit，前端弹"差封面"
- [ ] 打回后状态回退正确，reason 显示在审计流
- [ ] 取消父任务后已 done 子任务保留 done，其他变 cancelled
- [ ] 在素材管理改产品负责人 → 任务中心未完成子 assignee 跟变；已 done 不变
- [ ] 同时拥有两个能力位的用户在"我的任务"看到混合（父 + 子）

- [ ] **Step 4: Document any deviations** in `docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md` 末尾增加一节 "Implementation Notes" 记录 Task 29 实际走的是 hook 还是退化方案，以及任何与 spec 不符的地方。

- [ ] **Step 5: Final commit if any docs changed**

```bash
git add docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md
git commit -m "docs(task-center): record implementation notes for any spec deviations"
```

---

## Self-Review Notes (filled in after writing)

- **Spec coverage**: 12 决定 + 1 修订 + 1 增量 → 全部映射到任务：
  - 决定 1 (双层模型) → Task 1, 4
  - 决定 2 (两道审核) → Task 7, 8, 11, 12
  - 决定 3 (创建时一并物化 blocked) → Task 4
  - 决定 4 (国家+翻译员强制必填) → Task 4 (test rejects empty)
  - 决定 5 (一人一品 + 老品沿用) → Task 14 (cascade), Task 26 (创建弹窗 ownerId 自动选)
  - 决定 6 (打回原路返回) → Task 8, 12
  - 决定 7 (无换人按钮) → 在 Task 27 按钮表里没出现"换人"
  - 决定 8 (owner cascade 未完成跟换) → Task 14
  - 决定 9 (capability 位) → Task 2
  - 决定 10 (子任务半集成跳现有按钮) → Task 27 (tcChildJumpTranslate)
  - 决定 11-修订 (raw video op 进认领池) → Task 5 + Task 27 按钮逻辑
  - 决定 12 (待派单素材 Tab + 一键创建) → Task 17, 25, 26
  - 决定 §3-增量 (cancelled + 级联) → Task 9, 13
  - readiness gate → Task 10
- **Placeholders**: scanned — no TBD/TODO; all code blocks complete
- **Type consistency**: `task_id` 整型；`actor_user_id` 整型；`reason` 字符串 ≥10 字符 — 在 service 和路由两层都强制；`country_code` 大写 — 在 create_parent_task 里 normalize；状态枚举常量在所有任务中复用
