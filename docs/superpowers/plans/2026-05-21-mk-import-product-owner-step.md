# Mingkong Product Owner Step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Mingkong “加入素材库” owner choice into the import progress modal, rename the old translator step to product owner, and clearly separate product ownership from per-language translation assignment.

**Architecture:** Keep the existing `/mk-import/video` endpoint and import service, adding `product_owner_id` as the new preferred request field while retaining `translator_id` as a compatibility alias. Frontend state in `web/templates/mk_selection.html` will use product-owner naming for import ownership and translation-owner naming for small-language task assignment.

**Tech Stack:** Python 3.12, Flask routes, appcore service functions, Jinja template with inline JavaScript, pytest.

---

### Task 1: Frontend Contract Tests

**Files:**
- Modify: `tests/test_mk_selection_routes.py`
- Modify: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Add failing tests for the new import progress step**

Add assertions that the template contains a `productOwner` progress step before `domains`, uses `product_owner_id`, and no longer renders `mkiTranslatorModal`.

```python
def test_mk_import_progress_uses_product_owner_step_before_domains():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "{key: 'productOwner', title: '选择产品负责人'" in template
    assert template.index("{key: 'productOwner'") < template.index("{key: 'domains'")
    assert "product_owner_id" in template
    assert 'id="mkiTranslatorModal"' not in template
    assert "mkiOpenTranslatorModal" not in template
```

- [ ] **Step 2: Add failing tests for task assignment copy**

Add assertions that the small-language modal labels the task assignee as a language translation owner and includes the semantic distinction text.

```python
def test_mk_small_language_modal_distinguishes_product_owner_from_translation_owner():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "小语种翻译负责人" in template
    assert "产品负责人用于素材归属" in template
    assert "可与产品负责人不同" in template
    assert "翻译员：" not in template
```

- [ ] **Step 3: Run tests and confirm red**

Run: `pytest tests/test_mk_selection_routes.py::test_mk_import_progress_uses_product_owner_step_before_domains tests/test_mk_selection_routes.py::test_mk_small_language_modal_distinguishes_product_owner_from_translation_owner -q`

Expected: FAIL because the template still has the old translator modal and labels.

### Task 2: Backend Request Compatibility

**Files:**
- Modify: `web/routes/mk_import.py`
- Modify: `tests/test_mk_import_routes.py`

- [ ] **Step 1: Add failing route tests**

Add tests for `product_owner_id` priority and old `translator_id` compatibility.

```python
def test_mk_import_video_accepts_product_owner_id(authed_client_no_db, monkeypatch):
    from web.routes import mk_import as route

    captured = {}
    monkeypatch.setattr(route, "ensure_translation_work_user", lambda user_id: {"id": user_id})
    monkeypatch.setattr(route.mk_import_svc, "import_mk_video", lambda **kwargs: captured.update(kwargs) or {"ok": True})

    resp = authed_client_no_db.post(
        "/mk-import/video",
        json={"mk_video_metadata": {"filename": "x.mp4"}, "product_owner_id": 8},
    )

    assert resp.status_code == 200
    assert captured["translator_id"] == 8
```

- [ ] **Step 2: Run route test and confirm red**

Run: `pytest tests/test_mk_import_routes.py::test_mk_import_video_accepts_product_owner_id -q`

Expected: FAIL because the route currently requires integer `translator_id`.

- [ ] **Step 3: Implement minimal parsing**

In `web/routes/mk_import.py`, parse:

```python
product_owner_id = payload.get("product_owner_id", payload.get("translator_id"))
```

Validate it as the old `translator_id` value for service compatibility.

- [ ] **Step 4: Run route tests and confirm green**

Run: `pytest tests/test_mk_import_routes.py -q`

Expected: PASS.

### Task 3: Import Progress Modal Owner Step

**Files:**
- Modify: `web/templates/mk_selection.html`
- Modify: `tests/test_mk_selection_routes.py`
- Modify: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Replace old translator modal with progress owner state**

Remove `mkiOpenTranslatorModal`, `mkiTranslatorOK`, `mkiTranslatorCancel`, and the `mkiTranslatorModal` HTML block.

- [ ] **Step 2: Add product owner state and UI panel**

Add state variables equivalent to:

```javascript
let mkiImportProgressProductOwnerId = null;
let mkiProductOwnersCache = null;
```

Add a progress panel with select and confirm/retry controls for “选择产品负责人”.

- [ ] **Step 3: Change import flow ordering**

Change `mkiHandleClick(btn)` so it opens the progress modal immediately, loads product-owner choices, waits for confirmation, then calls `/mk-import/video` with `product_owner_id`.

- [ ] **Step 4: Preserve domain gate**

Ensure `mkiImportProgressComplete(data, btn)` marks product-owner as done before loading domains, and `mkiImportProgressShowNextActions()` still only runs after domain save.

- [ ] **Step 5: Run frontend static tests**

Run: `pytest tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q`

Expected: PASS.

### Task 4: Small-Language Task Copy

**Files:**
- Modify: `web/templates/mk_selection.html`
- Modify: `tests/test_mk_selection_routes.py`
- Modify: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Rename task assignment labels**

Change small-language modal text from generic “翻译员” to “小语种翻译负责人” or “语言翻译负责人”.

- [ ] **Step 2: Add semantic distinction copy**

Add visible helper text:

```text
产品负责人用于素材归属；小语种翻译负责人用于当前语言任务，可与产品负责人不同。
```

- [ ] **Step 3: Keep API compatibility**

Continue sending `translator_id` and `language_assignments` to task-center endpoints, because that is the current task API contract.

- [ ] **Step 4: Run static tests**

Run: `pytest tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q`

Expected: PASS.

### Task 5: Verification

**Files:**
- Verify only; no planned edits.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
pytest tests/test_mk_import_routes.py tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
```

Expected: PASS.

- [ ] **Step 2: Compile Python**

Run:

```bash
python3 -m compileall appcore web tests -q
```

Expected: exit code 0.

- [ ] **Step 3: Check diff whitespace**

Run:

```bash
git diff --check
```

Expected: exit code 0.
