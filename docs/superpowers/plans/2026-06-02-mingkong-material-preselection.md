# Mingkong Material Preselection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a permission-managed Mingkong material preselection workflow for operations users and admin follow-up import/translation actions.

**Architecture:** Add a focused preselection service backed by a new MySQL table keyed by the existing Mingkong `material_key`. Route gates split full `mk_selection` admins from users with the narrower `mk_material_preselection` permission. The existing Mingkong card renderer is extended to show preselected languages/notes and to pass preselection defaults into the existing import and small-language task flows.

**Tech Stack:** Python 3.12, Flask, MySQL migrations, Jinja template JavaScript, pytest.

---

## File Structure

- Modify `appcore/permissions.py`: register `mk_material_preselection`.
- Create `db/migrations/2026_06_02_mingkong_material_preselections.sql`: table plus idempotent `guqian` permission grant.
- Modify `tests/test_permissions.py`: permission defaults and user overlay coverage.
- Modify `tests/test_mingkong_materials_schema.py`: migration smoke coverage for the new table and grant SQL.
- Create `appcore/mingkong_material_preselections.py`: normalize countries, upsert/list/mark preselection rows, enrich cards.
- Create `tests/test_mingkong_material_preselections.py`: service behavior tests with monkeypatched database calls.
- Modify `appcore/mingkong_materials.py`: attach preselection info to archived card-list API rows.
- Modify `web/routes/xuanpin.py`: add route gates and preselection JSON endpoints.
- Modify `tests/test_xuanpin_routes.py`: route/template behavior for restricted users and admins.
- Modify `web/templates/mk_selection.html`: add tab, modal, filters, note/language rendering, default-language wiring.

## Task 1: Permission Registration And Initial Grant

**Files:**
- Modify: `appcore/permissions.py`
- Modify: `tests/test_permissions.py`
- Modify: `tests/test_mingkong_materials_schema.py`
- Create: `db/migrations/2026_06_02_mingkong_material_preselections.sql`

- [ ] **Step 1: Write failing permission tests**

Add tests proving the new permission is a managed permission, admin-default true,
user-default false, and grantable through stored JSON:

```python
def test_mk_material_preselection_permission_defaults():
    assert "mk_material_preselection" in PERMISSION_CODES
    assert default_permissions_for_role(ROLE_ADMIN)["mk_material_preselection"] is True
    assert default_permissions_for_role(ROLE_USER)["mk_material_preselection"] is False


def test_user_can_be_granted_mk_material_preselection():
    u = User(_make_row(ROLE_USER, permissions=json.dumps({"mk_material_preselection": True})))
    assert u.has_permission("mk_material_preselection") is True
    assert u.has_permission("mk_selection") is False
```

- [ ] **Step 2: Run permission tests and verify RED**

Run:

```powershell
pytest tests/test_permissions.py::test_mk_material_preselection_permission_defaults tests/test_permissions.py::test_user_can_be_granted_mk_material_preselection -q
```

Expected: FAIL because `mk_material_preselection` is not registered.

- [ ] **Step 3: Register the permission**

Add this management permission after `mk_selection` in `PERMISSIONS`:

```python
("mk_material_preselection", GROUP_MANAGEMENT, "明空素材预选", True, False),
```

- [ ] **Step 4: Run permission tests and verify GREEN**

Run the same pytest command. Expected: PASS.

- [ ] **Step 5: Write migration/schema tests**

Extend the schema test to assert:

```python
assert "CREATE TABLE IF NOT EXISTS mingkong_material_preselections" in migration_sql
assert "UNIQUE KEY uk_mk_material_preselections_material_key" in migration_sql
assert "$.mk_material_preselection" in migration_sql
assert "WHERE username = 'guqian'" in migration_sql
```

- [ ] **Step 6: Run schema test and verify RED**

Run:

```powershell
pytest tests/test_mingkong_materials_schema.py -q
```

Expected: FAIL because the migration file does not exist yet.

- [ ] **Step 7: Add the migration**

Create a migration that:

- Adds `mingkong_material_preselections`.
- Adds the required unique key and indexes.
- Updates `guqian` permissions with `JSON_SET`.
- Uses `CASE WHEN JSON_VALID(...)` so `NULL` or invalid permissions becomes `{}`.

- [ ] **Step 8: Run schema test and verify GREEN**

Run:

```powershell
pytest tests/test_mingkong_materials_schema.py tests/test_permissions.py -q
```

Expected: PASS.

## Task 2: Preselection Service

**Files:**
- Create: `appcore/mingkong_material_preselections.py`
- Create: `tests/test_mingkong_material_preselections.py`

- [ ] **Step 1: Write failing normalization/upsert tests**

Cover:

- Empty selected countries raises `ValueError`.
- Duplicate/mixed-case country codes normalize to uppercase unique list.
- Notes are trimmed.
- Upsert calls database with `material_key` and JSON countries.

Representative test:

```python
def test_upsert_requires_at_least_one_country(monkeypatch):
    from appcore import mingkong_material_preselections as svc

    payload = {"material_key": "abc", "selected_countries": []}

    with pytest.raises(ValueError, match="至少选择一个语言"):
        svc.upsert_preselection(payload, user_id=7)
```

- [ ] **Step 2: Run new service tests and verify RED**

Run:

```powershell
pytest tests/test_mingkong_material_preselections.py -q
```

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement service helpers**

Create functions:

```python
def normalize_countries(values: Iterable[str]) -> list[str]: ...
def upsert_preselection(payload: Mapping[str, Any], *, user_id: int) -> dict: ...
def list_preselections(filters: Mapping[str, Any]) -> dict: ...
def mark_processed(material_key: str, *, parent_task_id: int | None, user_id: int) -> dict: ...
def enrich_items_with_preselection(items: list[dict]) -> list[dict]: ...
```

Use existing `appcore.db.query`, `query_one`, and `execute` patterns.

- [ ] **Step 4: Run service tests and verify GREEN**

Run:

```powershell
pytest tests/test_mingkong_material_preselections.py -q
```

Expected: PASS.

- [ ] **Step 5: Add list/filter/processed tests**

Cover imported/unimported, processed/unprocessed, and that only `mark_processed`
sets `processed_at`.

- [ ] **Step 6: Implement list/filter/processed behavior**

Keep the query bounded and parameterized. Imported status uses enriched
`has_local_material_in_library`/`media_item_id`; processed status uses `processed_at`.

- [ ] **Step 7: Run service tests and verify GREEN**

Run:

```powershell
pytest tests/test_mingkong_material_preselections.py -q
```

Expected: PASS.

## Task 3: Route Gates And JSON APIs

**Files:**
- Modify: `web/routes/xuanpin.py`
- Modify: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Write failing route permission tests**

Add a client helper for a normal user with:

```python
permissions={"mk_material_preselection": True, "mk_selection": False}
```

Assert:

- `GET /xuanpin/mk` returns 200.
- `GET /xuanpin/api/mk-material-library` returns 200.
- `POST /mk-import/video` remains forbidden for that user.
- `POST /xuanpin/api/mk-material-preselections` delegates to service.

- [ ] **Step 2: Run route tests and verify RED**

Run:

```powershell
pytest tests/test_xuanpin_routes.py -q
```

Expected: FAIL on new permission behavior/endpoints.

- [ ] **Step 3: Implement route helpers**

Add helpers:

```python
def _has_mk_selection_permission() -> bool:
    return bool(current_user.is_authenticated and current_user.has_permission("mk_selection"))


def _has_mk_material_preselection_permission() -> bool:
    return bool(
        current_user.is_authenticated
        and (
            current_user.has_permission("mk_selection")
            or current_user.has_permission("mk_material_preselection")
        )
    )
```

Use the full helper for admin-only operations and the narrower helper for read/save
preselection operations.

- [ ] **Step 4: Add preselection endpoints**

Implement:

- `GET /xuanpin/api/mk-material-preselections`
- `POST /xuanpin/api/mk-material-preselections`
- `POST /xuanpin/api/mk-material-preselections/<material_key>/processed`

Only the processed endpoint requires `mk_selection`.

- [ ] **Step 5: Run route tests and verify GREEN**

Run:

```powershell
pytest tests/test_xuanpin_routes.py -q
```

Expected: PASS or only unrelated existing failures. Do not run tests that touch local
MySQL.

## Task 4: Card API Enrichment

**Files:**
- Modify: `appcore/mingkong_materials.py`
- Modify: `tests/test_mingkong_material_preselections.py`

- [ ] **Step 1: Write failing enrichment test**

Given material rows with a known `material_key`, assert list-card output includes:

```python
{
    "preselection": {
        "selected_countries": ["DE", "FR"],
        "operator_note": "优先做德法",
        "processed_at": None,
    },
    "is_preselected": True,
}
```

- [ ] **Step 2: Run enrichment test and verify RED**

Run:

```powershell
pytest tests/test_mingkong_material_preselections.py::test_enrich_items_with_preselection -q
```

Expected: FAIL because enrichment is not wired.

- [ ] **Step 3: Wire enrichment into material list services**

After `_enrich_cached_ad_statuses(items)`, call
`enrich_items_with_preselection(items)` for both archived material-library and
yesterday-spend list responses.

- [ ] **Step 4: Run enrichment tests and verify GREEN**

Run:

```powershell
pytest tests/test_mingkong_material_preselections.py -q
```

Expected: PASS.

## Task 5: Frontend Tab, Modal, Notes, And Defaults

**Files:**
- Modify: `web/templates/mk_selection.html`
- Modify: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Write failing template tests**

Assert the template contains:

- `素材预选`
- `mkMaterialPreselectionPanel`
- `/xuanpin/api/mk-material-preselections`
- `mkiOpenPreselectionModal`
- `mkiRenderPreselectionNote`
- `defaultCountries`
- `preselectionNote`

- [ ] **Step 2: Run template tests and verify RED**

Run:

```powershell
pytest tests/test_xuanpin_routes.py::test_mk_selection_template_contains_material_preselection_ui -q
```

Expected: FAIL because the UI is absent.

- [ ] **Step 3: Add tab and panel**

Update tab normalization and state management to include `preselection`.

Add filters:

- Library status: all/imported/not_imported.
- Processed status: all/processed/unprocessed.

- [ ] **Step 4: Add preselection modal**

Render product info, language capsules, AI hint area, and note textarea. Save via
`POST /xuanpin/api/mk-material-preselections`.

- [ ] **Step 5: Extend card renderer**

Render language pills and note snippet for cards with `preselection`. Add the
`素材预选` button beside yesterday spend.

- [ ] **Step 6: Wire admin flows**

When a card has preselection data:

- Import/progress flow shows `preselection.operator_note` at top.
- Small-language modal receives `defaultCountries`.
- Small-language modal shows `preselectionNote`.
- Successful task creation calls the processed endpoint.

- [ ] **Step 7: Run template/route tests and verify GREEN**

Run:

```powershell
pytest tests/test_xuanpin_routes.py tests/test_mingkong_material_preselections.py -q
```

Expected: PASS or documented unrelated failures.

## Task 6: Focused Verification

**Files:**
- No new source files.

- [ ] **Step 1: Run permission/service/schema tests**

Run:

```powershell
pytest tests/test_permissions.py tests/test_mingkong_materials_schema.py tests/test_mingkong_material_preselections.py -q
```

Expected: PASS.

- [ ] **Step 2: Run route/template tests**

Run:

```powershell
pytest tests/test_xuanpin_routes.py -q
```

Expected: PASS or document unrelated existing failures.

- [ ] **Step 3: Static check for local MySQL violations**

Confirm no verification command connected to Windows `127.0.0.1:3306`. If any test
attempts that connection, stop that test and report the project rule.

## Self Review

- Spec coverage: permissions, guqian grant, preselection data, note display, admin
  defaults, processed semantics, and filters all have tasks.
- Placeholder scan: no `TBD` or unspecified implementation steps remain.
- Type consistency: `selected_countries`, `operator_note`, `processed_at`,
  `material_key`, `defaultCountries`, and `preselectionNote` names are consistent
  across service, route, and frontend tasks.
