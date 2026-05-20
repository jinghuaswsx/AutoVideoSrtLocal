# User Work Scope Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `翻译工作` user work scope and make “加入素材库” / “做小语种” assignment lists show only users in that scope.

**Architecture:** Reuse `users.permissions` JSON for the first work-scope flag, keeping the existing permission modal as the management surface. Add app-layer helpers in `appcore.users`, route-level validation before all translator assignment writes, and switch the Mingkong selection page to a translation-work user API.

**Tech Stack:** Python 3.12, Flask blueprints, MySQL JSON functions in migrations, Jinja template JavaScript, pytest.

---

## File Map

- Modify `appcore/permissions.py`: register `work_scope_translation` in the capability group and set role defaults.
- Modify `appcore/users.py`: add JSON permission parsing helpers, `list_translation_work_users()`, and `ensure_translation_work_user()`.
- Modify `web/routes/tasks.py`: add `/tasks/api/translation-work-users`, validate `translator_id` in parent creation and import-and-create.
- Modify `web/routes/mk_import.py`: validate `translator_id` before calling `mk_import_svc.import_mk_video`.
- Modify `web/services/mk_import.py`: add a typed invalid-translator response helper.
- Modify `appcore/new_product_review.py`: require `work_scope_translation` in `_resolve_translator()`.
- Modify `appcore/today_recommendations.py`: validate recommendation adoption translator IDs through `ensure_translation_work_user()`.
- Modify `web/templates/mk_selection.html`: replace active-user API with translation-work user API and update empty-list messages.
- Create `db/migrations/2026_05_20_user_translation_work_scope.sql`: seed the five named users with `can_translate` and `work_scope_translation`.
- Modify tests:
  - `tests/test_permissions.py`
  - `tests/test_user_translators.py`
  - `tests/test_tasks_routes.py`
  - `tests/test_mk_import_routes.py`
  - `tests/test_appcore_new_product_review.py`
  - `tests/test_new_product_review_routes.py`
  - `tests/test_today_recommendations.py`
  - `tests/test_xuanpin_routes.py`
  - `tests/test_db_migration_user_translation_work_scope.py`

## Docs Anchor

Use [docs/superpowers/specs/2026-05-20-user-work-scope-translation-design.md](../specs/2026-05-20-user-work-scope-translation-design.md) as the implementation anchor.

### Task 1: Permission Flag

**Files:**
- Modify: `tests/test_permissions.py`
- Modify: `appcore/permissions.py`

- [ ] **Step 1: Write failing permission tests**

Append to `tests/test_permissions.py`:

```python
def test_work_scope_translation_in_codes():
    assert "work_scope_translation" in PERMISSION_CODES
    assert PERMISSION_META["work_scope_translation"]["label"] == "翻译工作"
    assert PERMISSION_META["work_scope_translation"]["group"] == "capability"


def test_work_scope_translation_defaults_require_explicit_assignment():
    assert default_permissions_for_role(ROLE_ADMIN)["work_scope_translation"] is False
    assert default_permissions_for_role(ROLE_USER)["work_scope_translation"] is False
    assert default_permissions_for_role(ROLE_TRANSLATOR)["work_scope_translation"] is False
    assert default_permissions_for_role(ROLE_ANALYST)["work_scope_translation"] is False
    assert default_permissions_for_role(ROLE_SUPERADMIN)["work_scope_translation"] is True
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
pytest tests/test_permissions.py::test_work_scope_translation_in_codes tests/test_permissions.py::test_work_scope_translation_defaults_require_explicit_assignment -q
```

Expected: first test fails because `work_scope_translation` is not registered.

- [ ] **Step 3: Implement permission flag**

In `appcore/permissions.py`, add the capability entry immediately after `can_translate`:

```python
("work_scope_translation", GROUP_CAPABILITY, "翻译工作", False, False),
```

Do not add it to `_TRANSLATOR_TRUE_PERMISSIONS`; this scope must be explicitly assigned.

- [ ] **Step 4: Run permission tests**

Run:

```bash
pytest tests/test_permissions.py -q
```

Expected: all permission tests pass.

### Task 2: User Helper Layer

**Files:**
- Modify: `tests/test_user_translators.py`
- Modify: `appcore/users.py`

- [ ] **Step 1: Write failing helper tests**

Append to `tests/test_user_translators.py`:

```python
def test_list_translation_work_users_requires_translate_and_work_scope(monkeypatch):
    from appcore import users

    def fake_query(sql, args=()):
        return [
            {
                "id": 1,
                "username": "zhou",
                "display_name": "周干琴",
                "role": "user",
                "permissions": '{"can_translate": true, "work_scope_translation": true}',
            },
            {
                "id": 2,
                "username": "translate-only",
                "display_name": "翻译但非范围",
                "role": "user",
                "permissions": '{"can_translate": true, "work_scope_translation": false}',
            },
            {
                "id": 3,
                "username": "scope-only",
                "display_name": "范围但非翻译",
                "role": "user",
                "permissions": '{"can_translate": false, "work_scope_translation": true}',
            },
        ]

    monkeypatch.setattr(users, "_user_display_name_expr", lambda: "username")
    monkeypatch.setattr(users, "query", fake_query)

    assert users.list_translation_work_users() == [
        {"id": 1, "username": "zhou", "display_name": "周干琴"},
    ]


def test_ensure_translation_work_user_accepts_valid_user(monkeypatch):
    from appcore import users

    monkeypatch.setattr(
        users,
        "query_one",
        lambda sql, args: {
            "id": 5,
            "username": "worker",
            "display_name": "顾倩",
            "role": "user",
            "permissions": '{"can_translate": true, "work_scope_translation": true}',
            "is_active": 1,
        },
    )

    assert users.ensure_translation_work_user(5)["username"] == "worker"


def test_ensure_translation_work_user_rejects_missing_scope(monkeypatch):
    from appcore import users

    monkeypatch.setattr(
        users,
        "query_one",
        lambda sql, args: {
            "id": 5,
            "username": "worker",
            "display_name": "顾倩",
            "role": "user",
            "permissions": '{"can_translate": true, "work_scope_translation": false}',
            "is_active": 1,
        },
    )

    try:
        users.ensure_translation_work_user(5)
    except ValueError as exc:
        assert "翻译工作范围" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run failing helper tests**

Run:

```bash
pytest tests/test_user_translators.py::test_list_translation_work_users_requires_translate_and_work_scope tests/test_user_translators.py::test_ensure_translation_work_user_accepts_valid_user tests/test_user_translators.py::test_ensure_translation_work_user_rejects_missing_scope -q
```

Expected: fails because helper functions are missing.

- [ ] **Step 3: Implement helpers**

In `appcore/users.py`, add reusable helpers near `list_translators()`:

```python
def _coerce_permissions(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _has_bool_permission(row: dict, code: str) -> bool:
    return bool(_coerce_permissions(row.get("permissions")).get(code))


def _user_display_name_expr() -> str:
    row = query_one(
        "SELECT 1 AS ok FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' "
        "AND COLUMN_NAME = 'xingming'"
    )
    if row:
        return "COALESCE(NULLIF(TRIM(xingming), ''), username)"
    return "username"


def list_translation_work_users() -> list[dict]:
    expr = _user_display_name_expr()
    rows = query(
        f"SELECT id, username, {expr} AS display_name, role, permissions "
        "FROM users WHERE is_active=1 AND role <> %s ORDER BY display_name ASC, id ASC",
        (ROLE_SUPERADMIN,),
    )
    out = []
    for row in rows:
        if row.get("role") == ROLE_SUPERADMIN:
            continue
        if _has_bool_permission(row, "can_translate") and _has_bool_permission(row, "work_scope_translation"):
            out.append({
                "id": int(row["id"]),
                "username": row["username"],
                "display_name": row.get("display_name") or row["username"],
            })
    return out


def ensure_translation_work_user(user_id: int) -> dict:
    expr = _user_display_name_expr()
    row = query_one(
        f"SELECT id, username, {expr} AS display_name, role, permissions, is_active "
        "FROM users WHERE id=%s",
        (int(user_id),),
    )
    if not row:
        raise ValueError("翻译员不存在")
    if not row.get("is_active"):
        raise ValueError("翻译员已停用")
    if not _has_bool_permission(row, "can_translate"):
        raise ValueError("该用户没有翻译能力")
    if not _has_bool_permission(row, "work_scope_translation"):
        raise ValueError("该用户不在翻译工作范围")
    return row
```

Then update `list_translators()` to use `_coerce_permissions()` instead of duplicating JSON parsing.

- [ ] **Step 4: Run user helper tests**

Run:

```bash
pytest tests/test_user_translators.py -q
```

Expected: all user translator tests pass.

### Task 3: Tasks API and Validation

**Files:**
- Modify: `tests/test_tasks_routes.py`
- Modify: `web/routes/tasks.py`

- [ ] **Step 1: Write failing route tests**

Append to `tests/test_tasks_routes.py`:

```python
def test_translation_work_users_route_returns_users(authed_client_no_db, monkeypatch):
    expected = [{"id": 10, "username": "gq", "display_name": "顾倩"}]
    monkeypatch.setattr("web.routes.tasks.list_translation_work_users", lambda: expected)

    resp = authed_client_no_db.get("/tasks/api/translation-work-users")

    assert resp.status_code == 200
    assert resp.get_json() == {"users": expected}


def test_create_parent_rejects_non_translation_work_user(authed_client_no_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "web.routes.tasks.ensure_translation_work_user",
        lambda user_id: (_ for _ in ()).throw(ValueError("该用户不在翻译工作范围")),
    )
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.create_parent_task",
        lambda **kwargs: calls.append(kwargs) or 123,
    )

    resp = authed_client_no_db.post(
        "/tasks/api/parent",
        json={"media_product_id": 1, "media_item_id": 2, "countries": ["DE"], "translator_id": 9},
    )

    assert resp.status_code == 400
    assert "翻译工作范围" in resp.get_json()["error"]
    assert calls == []


def test_import_and_create_rejects_non_translation_work_user(authed_client_no_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "web.routes.tasks.ensure_translation_work_user",
        lambda user_id: (_ for _ in ()).throw(ValueError("该用户不在翻译工作范围")),
    )
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.import_and_create_task",
        lambda **kwargs: calls.append(kwargs) or {"parent_task_id": 1},
    )

    resp = authed_client_no_db.post(
        "/tasks/api/import-and-create",
        json={"mk_video_metadata": {"filename": "x.mp4"}, "countries": ["DE"], "translator_id": 9},
    )

    assert resp.status_code == 400
    assert "翻译工作范围" in resp.get_json()["error"]
    assert calls == []
```

- [ ] **Step 2: Run failing route tests**

Run:

```bash
pytest tests/test_tasks_routes.py::test_translation_work_users_route_returns_users tests/test_tasks_routes.py::test_create_parent_rejects_non_translation_work_user tests/test_tasks_routes.py::test_import_and_create_rejects_non_translation_work_user -q
```

Expected: import or route failures because the new API and validation are not implemented.

- [ ] **Step 3: Implement route API and validation**

In `web/routes/tasks.py`, update imports:

```python
from appcore.users import (
    ensure_translation_work_user,
    list_translation_work_users,
    list_translators,
)
```

Before `tasks_svc.create_parent_task(...)` and `tasks_svc.import_and_create_task(...)`, add:

```python
    try:
        ensure_translation_work_user(translator_id)
    except ValueError as e:
        return _json_response({"error": str(e)}, 400)
```

Add route next to `/api/translators`:

```python
@bp.route("/api/translation-work-users", methods=["GET"])
@login_required
def api_translation_work_users():
    return _json_response({"users": list_translation_work_users()})
```

- [ ] **Step 4: Run task route tests**

Run:

```bash
pytest tests/test_tasks_routes.py -q
```

Expected: task route tests pass.

### Task 4: mk-import Validation

**Files:**
- Modify: `tests/test_mk_import_routes.py`
- Modify: `web/routes/mk_import.py`

- [ ] **Step 1: Write failing mk-import test**

Append to `tests/test_mk_import_routes.py`:

```python
def test_mk_import_video_rejects_non_translation_work_user(authed_client_no_db, monkeypatch):
    from web.routes import mk_import as route

    calls = []
    monkeypatch.setattr(
        route,
        "ensure_translation_work_user",
        lambda user_id: (_ for _ in ()).throw(ValueError("该用户不在翻译工作范围")),
    )
    monkeypatch.setattr(route.mk_import_svc, "import_mk_video", lambda **kwargs: calls.append(kwargs))

    resp = authed_client_no_db.post(
        "/mk-import/video",
        json={"mk_video_metadata": {"filename": "x.mp4"}, "translator_id": 7},
    )

    assert resp.status_code == 400
    assert "翻译工作范围" in resp.get_json()["detail"]
    assert calls == []
```

- [ ] **Step 2: Run failing mk-import test**

Run:

```bash
pytest tests/test_mk_import_routes.py::test_mk_import_video_rejects_non_translation_work_user -q
```

Expected: fails because validation import is absent.

- [ ] **Step 3: Implement mk-import validation**

In `web/routes/mk_import.py`, import:

```python
from appcore.users import ensure_translation_work_user
```

In the POST handler after payload parse and before service call:

```python
    try:
        ensure_translation_work_user(translator_id)
    except ValueError as exc:
        return jsonify({"error": "invalid_translator", "detail": str(exc)}), 400
```

- [ ] **Step 4: Run mk-import tests**

Run:

```bash
pytest tests/test_mk_import_routes.py -q
```

Expected: mk-import route tests pass.

### Task 5: New Product Review Validation

**Files:**
- Modify: `tests/test_appcore_new_product_review.py`
- Modify: `tests/test_new_product_review_routes.py`
- Modify: `appcore/new_product_review.py`

- [ ] **Step 1: Update service tests for missing work scope**

In `tests/test_appcore_new_product_review.py`, add:

```python
def test_resolve_translator_rejects_no_translation_work_scope(monkeypatch):
    from appcore import new_product_review as npr

    monkeypatch.setattr(
        npr,
        "query_one",
        lambda sql, args: {
            "id": 5,
            "username": "u",
            "role": "user",
            "permissions": '{"can_translate": true, "work_scope_translation": false}',
            "is_active": 1,
        },
    )
    with pytest.raises(npr.TranslatorInvalidError):
        npr._resolve_translator(5)
```

- [ ] **Step 2: Run failing service test**

Run:

```bash
pytest tests/test_appcore_new_product_review.py::test_resolve_translator_rejects_no_translation_work_scope -q
```

Expected: fails because `_resolve_translator()` currently only checks `can_translate`.

- [ ] **Step 3: Implement service validation**

In `appcore/new_product_review.py`, update `_resolve_translator()` after the `can_translate` check:

```python
    if not perms.get("work_scope_translation"):
        raise TranslatorInvalidError(
            f"user {translator_id} lacks translation work scope"
        )
```

- [ ] **Step 4: Run new product review tests**

Run:

```bash
pytest tests/test_appcore_new_product_review.py tests/test_new_product_review_routes.py -q
```

Expected: tests pass.

### Task 6: Today Recommendations Adoption Validation

**Files:**
- Modify: `appcore/today_recommendations.py`
- Modify: `tests/test_today_recommendations.py`

- [ ] **Step 1: Locate adoption service**

Run:

```bash
rg -n "def adopt_recommendations|translator_id" appcore/today_recommendations.py tests/test_xuanpin_routes.py
```

Expected: find `adopt_recommendations(...)` and the route test area for `/xuanpin/api/today-recommendations/adopt`.

- [ ] **Step 2: Add service validation test**

In `tests/test_today_recommendations.py`, add:

```python
def test_adopt_recommendations_rejects_non_translation_work_user(monkeypatch):
    from appcore import today_recommendations as svc

    monkeypatch.setattr(
        svc,
        "ensure_translation_work_user",
        lambda user_id: (_ for _ in ()).throw(ValueError("该用户不在翻译工作范围")),
        raising=False,
    )

    with pytest.raises(ValueError, match="翻译工作范围"):
        svc.adopt_recommendations(
            recommendation_ids=[1],
            translator_id=99,
            actor_user_id=1,
        )
```

- [ ] **Step 3: Run failing today recommendations test**

Run:

```bash
pytest tests/test_today_recommendations.py::test_adopt_recommendations_rejects_non_translation_work_user -q
```

Expected: fails because `adopt_recommendations()` has not validated the translation work scope before DB work.

- [ ] **Step 4: Add validation in service**

In `appcore/today_recommendations.py`, import:

```python
from appcore.users import ensure_translation_work_user
```

At the start of `adopt_recommendations(...)`, after parsing `translator_id`, add:

```python
    ensure_translation_work_user(int(translator_id))
```

This lets the existing xuanpin route map `ValueError` to 400.

- [ ] **Step 5: Run today recommendations tests**

Run:

```bash
pytest tests/test_today_recommendations.py -q
```

Expected: today recommendations tests pass.

### Task 7: Mingkong Selection Frontend

**Files:**
- Modify: `tests/test_xuanpin_routes.py`
- Modify: `web/templates/mk_selection.html`

- [ ] **Step 1: Write frontend static test**

Append to `tests/test_xuanpin_routes.py`:

```python
def test_xuanpin_mk_uses_translation_work_user_api(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "const MKI_ACTIVE_USERS_API = '/tasks/api/translation-work-users';" in body
    assert "/medias/api/users/active" not in body
    assert "没有可用翻译工作用户" in body
```

- [ ] **Step 2: Run failing frontend test**

Run:

```bash
pytest tests/test_xuanpin_routes.py::test_xuanpin_mk_uses_translation_work_user_api -q
```

Expected: fails because template still references `/medias/api/users/active`.

- [ ] **Step 3: Update template**

In `web/templates/mk_selection.html`, change:

```javascript
const MKI_ACTIVE_USERS_API = '/tasks/api/translation-work-users';
```

Update both empty-list alert messages:

```javascript
alert('没有可用翻译工作用户');
```

Do not change `mkiUserLabel()` except to keep `display_name || username` fallback.

- [ ] **Step 4: Run frontend route tests**

Run:

```bash
pytest tests/test_xuanpin_routes.py -q
```

Expected: xuanpin tests pass.

### Task 8: Migration

**Files:**
- Create: `db/migrations/2026_05_20_user_translation_work_scope.sql`
- Create: `tests/test_db_migration_user_translation_work_scope.py`

- [ ] **Step 1: Add migration file**

Create `db/migrations/2026_05_20_user_translation_work_scope.sql`:

```sql
-- Docs-anchor: docs/superpowers/specs/2026-05-20-user-work-scope-translation-design.md

SET @target_names = JSON_ARRAY('周干琴', '顾倩', '王舒溦', '王健', '蔡靖华');

UPDATE users
SET permissions = JSON_SET(
  CASE
    WHEN JSON_VALID(COALESCE(CAST(permissions AS CHAR), '{}'))
    THEN COALESCE(permissions, JSON_OBJECT())
    ELSE JSON_OBJECT()
  END,
  '$.can_translate', JSON_EXTRACT('true', '$'),
  '$.work_scope_translation', JSON_EXTRACT('true', '$')
)
WHERE JSON_CONTAINS(@target_names, JSON_QUOTE(username));

SET @has_xingming = (
  SELECT COUNT(*)
  FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'users'
    AND COLUMN_NAME = 'xingming'
);

SET @sql = IF(
  @has_xingming > 0,
  'UPDATE users SET permissions = JSON_SET(CASE WHEN JSON_VALID(COALESCE(CAST(permissions AS CHAR), ''{}'')) THEN COALESCE(permissions, JSON_OBJECT()) ELSE JSON_OBJECT() END, ''$.can_translate'', JSON_EXTRACT(''true'', ''$''), ''$.work_scope_translation'', JSON_EXTRACT(''true'', ''$'')) WHERE JSON_CONTAINS(@target_names, JSON_QUOTE(xingming))',
  'SELECT 1'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
```

- [ ] **Step 2: Add migration static test**

Create `tests/test_db_migration_user_translation_work_scope.py`:

```python
from pathlib import Path


def test_user_translation_work_scope_migration_targets_named_users():
    body = Path("db/migrations/2026_05_20_user_translation_work_scope.sql").read_text(encoding="utf-8")

    for name in ["周干琴", "顾倩", "王舒溦", "王健", "蔡靖华"]:
        assert name in body
    assert "$.can_translate" in body
    assert "$.work_scope_translation" in body
    assert "INFORMATION_SCHEMA.COLUMNS" in body
    assert "xingming" in body
```

- [ ] **Step 3: Run migration static test**

Run:

```bash
pytest tests/test_db_migration_user_translation_work_scope.py -q
```

Expected: migration static test passes.

### Task 9: Full Verification

**Files:**
- All touched files.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
pytest tests/test_permissions.py tests/test_user_translators.py tests/test_tasks_routes.py tests/test_mk_import_routes.py tests/test_appcore_new_product_review.py tests/test_new_product_review_routes.py tests/test_today_recommendations.py tests/test_xuanpin_routes.py tests/test_db_migration_user_translation_work_scope.py -q
```

Expected: all focused tests pass.

- [ ] **Step 2: Run route smoke without DB mutation**

Run a Flask test-client smoke with startup recovery and DB helpers patched:

```bash
AUTOVIDEOSRT_DISABLE_DOTENV=1 FLASK_SECRET_KEY=test-secret WTF_CSRF_ENABLED=0 DISABLE_STARTUP_RECOVERY=1 AUTOVIDEOSRT_DISABLE_BACKGROUND_THREADS=1 python3 - <<'PY'
from unittest.mock import patch
with patch('web.app._seed_default_prompts', lambda: None), \
     patch('web.app.recover_all_interrupted_tasks', lambda: None), \
     patch('web.app.mark_interrupted_bulk_translate_tasks', lambda: None), \
     patch('appcore.db.execute', lambda *a, **k: None), \
     patch('appcore.db.query', lambda *a, **k: []), \
     patch('appcore.db.query_one', lambda *a, **k: None), \
     patch('appcore.scheduled_tasks.query', lambda *a, **k: []):
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    for path in ('/xuanpin/mk', '/tasks/'):
        resp = client.get(path, follow_redirects=False)
        print(path, resp.status_code, resp.headers.get('Location'))
PY
```

Expected: unauthenticated routes return 302 to login, not 500.

- [ ] **Step 3: Inspect git diff**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intended docs, code, migration, and tests changed.

- [ ] **Step 4: Commit implementation**

Run:

```bash
git add appcore/permissions.py appcore/users.py appcore/new_product_review.py appcore/today_recommendations.py web/routes/tasks.py web/routes/mk_import.py web/templates/mk_selection.html db/migrations/2026_05_20_user_translation_work_scope.sql tests/test_permissions.py tests/test_user_translators.py tests/test_tasks_routes.py tests/test_mk_import_routes.py tests/test_appcore_new_product_review.py tests/test_new_product_review_routes.py tests/test_xuanpin_routes.py tests/test_db_migration_user_translation_work_scope.py docs/superpowers/plans/2026-05-20-user-work-scope-translation.md
git commit -m "feat: restrict translation assignment by work scope" -m "Docs-anchor: docs/superpowers/specs/2026-05-20-user-work-scope-translation-design.md#0-一句话目标"
```

Expected: commit succeeds with docs anchor.

## Self-Review

- Spec coverage: plan covers the permission bit, five-user migration, filtered frontend lists, server-side validation, and test coverage.
- Placeholder scan: no placeholder-only steps are present.
- Type consistency: helper names are `list_translation_work_users()` and `ensure_translation_work_user()` throughout.
