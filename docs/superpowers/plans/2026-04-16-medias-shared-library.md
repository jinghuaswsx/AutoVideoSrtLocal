# Medias Shared Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `素材管理` module into a shared read/write library so any logged-in user who can access `/medias` can view, edit, upload, and delete the same product/material dataset.

**Architecture:** Keep the DAO contract stable and move the behavior change into the medias route layer: list APIs should always query the full dataset, and product/item access checks should stop enforcing creator ownership. Then remove the admin-only “查看全部” UI and the frontend `scope=all` branch so the page matches the new shared-library semantics.

**Tech Stack:** Flask + Jinja2 + vanilla JavaScript / MySQL-backed `appcore.medias` DAO / pytest with monkeypatched route dependencies and no-DB authenticated clients

**Spec:** `docs/superpowers/specs/2026-04-16-medias-shared-library-design.md`

---

## File Structure

### Create

None.

### Modify

| File | Change |
| --- | --- |
| `tests/conftest.py` | Add a no-DB authenticated normal-user client fixture so route/page tests can exercise non-admin behavior. |
| `tests/test_web_routes.py` | Add failing tests for shared list access, cross-user read/write/delete access, and removal of the admin-only scope toggle. |
| `web/routes/medias.py` | Stop filtering list results by `current_user.id`, simplify `_can_access_product(...)`, and remove admin-only scope logic from the page render. |
| `web/templates/medias_list.html` | Remove the admin-only “查看全部” chip and the `MEDIAS_IS_ADMIN` bootstrap flag. |
| `web/static/medias.js` | Remove `scopeAll` / `scope=all` branching and stop syncing the deleted chip. |

---

## Task 1: Add failing tests for shared list and cross-user material access

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing no-DB normal-user fixture and route tests**

```python
# tests/conftest.py
@pytest.fixture
def authed_user_client_no_db(monkeypatch):
    from web.app import create_app

    fake_user = {
        "id": 2,
        "username": "test-user",
        "role": "user",
        "is_active": 1,
    }

    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 2 else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "2"
        session["_fresh"] = True

    return client
```

```python
# tests/test_web_routes.py
def test_medias_list_is_shared_for_normal_users(authed_user_client_no_db, monkeypatch):
    captured = {}

    def fake_list_products(user_id, keyword="", archived=False, offset=0, limit=20):
        captured["user_id"] = user_id
        return ([{
            "id": 9,
            "user_id": 99,
            "name": "共享产品",
            "product_code": "shared-product",
            "color_people": None,
            "source": None,
            "archived": 0,
            "created_at": None,
            "updated_at": None,
        }], 1)

    monkeypatch.setattr("web.routes.medias.medias.list_products", fake_list_products)
    monkeypatch.setattr("web.routes.medias.medias.count_items_by_product", lambda ids: {9: 1})
    monkeypatch.setattr("web.routes.medias.medias.first_thumb_item_by_product", lambda ids: {})
    monkeypatch.setattr("web.routes.medias.medias.list_item_filenames_by_product", lambda ids, limit_per=5: {9: ["demo.mp4"]})
    monkeypatch.setattr("web.routes.medias.medias.lang_coverage_by_product", lambda ids: {9: {}})
    monkeypatch.setattr("web.routes.medias.medias.get_product_covers_batch", lambda ids: {9: {}})

    response = authed_user_client_no_db.get("/medias/api/products")

    assert response.status_code == 200
    assert captured["user_id"] is None
    payload = response.get_json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == 9
    assert payload["items"][0]["product_code"] == "shared-product"


def test_medias_normal_user_can_read_update_and_delete_other_users_product(authed_user_client_no_db, monkeypatch):
    product = {
        "id": 7,
        "user_id": 88,
        "name": "他人产品",
        "product_code": "owner-product",
        "color_people": None,
        "source": None,
        "archived": 0,
        "created_at": None,
        "updated_at": None,
    }
    updated = {}
    deleted = {}

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product if pid == 7 else None)
    monkeypatch.setattr("web.routes.medias.medias.get_product_covers", lambda pid: {"en": "covers/en.jpg"})
    monkeypatch.setattr("web.routes.medias.medias.list_copywritings", lambda pid: [])
    monkeypatch.setattr("web.routes.medias.medias.list_items", lambda pid: [])
    monkeypatch.setattr("web.routes.medias.medias.get_product_by_code", lambda code: None)
    monkeypatch.setattr("web.routes.medias.medias.has_english_cover", lambda pid: True)
    monkeypatch.setattr("web.routes.medias.medias.update_product", lambda pid, **fields: updated.update({"pid": pid, **fields}))
    monkeypatch.setattr("web.routes.medias.medias.replace_copywritings", lambda pid, items, lang="en": None)
    monkeypatch.setattr("web.routes.medias.medias.soft_delete_product", lambda pid: deleted.update({"pid": pid}))

    get_response = authed_user_client_no_db.get("/medias/api/products/7")
    put_response = authed_user_client_no_db.put(
        "/medias/api/products/7",
        json={
            "name": "共享改名",
            "product_code": "shared-edited",
            "copywritings": {"en": [{"title": "T", "body": "B"}]},
        },
    )
    delete_response = authed_user_client_no_db.delete("/medias/api/products/7")

    assert get_response.status_code == 200
    assert get_response.get_json()["product"]["id"] == 7
    assert put_response.status_code == 200
    assert updated == {"pid": 7, "name": "共享改名", "product_code": "shared-edited"}
    assert delete_response.status_code == 200
    assert deleted == {"pid": 7}
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest tests/test_web_routes.py -q -k "medias_list_is_shared_for_normal_users or medias_normal_user_can_read_update_and_delete_other_users_product"`

Expected: FAIL because the current route code passes `current_user.id` into `medias.list_products(...)` and returns `404` for cross-user access.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/conftest.py tests/test_web_routes.py
git commit -m "test: cover shared medias access rules"
```

---

## Task 2: Make medias routes shared for all logged-in users

**Files:**
- Modify: `web/routes/medias.py`

- [ ] **Step 1: Implement the minimal route changes to remove creator-only access**

```python
# web/routes/medias.py
def _can_access_product(product: dict | None, write: bool = False) -> bool:
    return bool(product)


@bp.route("/")
@login_required
def index():
    return render_template(
        "medias_list.html",
        tos_ready=tos_clients.is_media_bucket_configured(),
    )


@bp.route("/api/products", methods=["GET"])
@login_required
def api_list_products():
    keyword = (request.args.get("keyword") or "").strip()
    archived = request.args.get("archived") in ("1", "true", "yes")
    page = max(1, int(request.args.get("page") or 1))
    limit = 20
    offset = (page - 1) * limit

    rows, total = medias.list_products(
        None,
        keyword=keyword,
        archived=archived,
        offset=offset,
        limit=limit,
    )
    pids = [r["id"] for r in rows]
    counts = medias.count_items_by_product(pids)
    thumb_covers = medias.first_thumb_item_by_product(pids)
    filenames = medias.list_item_filenames_by_product(pids, limit_per=5)
    coverage = medias.lang_coverage_by_product(pids)
    covers_map = medias.get_product_covers_batch(pids)
    data = [
        _serialize_product(
            r,
            counts.get(r["id"], 0),
            thumb_covers.get(r["id"]),
            items_filenames=filenames.get(r["id"], []),
            lang_coverage=coverage.get(r["id"], {}),
            covers=covers_map.get(r["id"], {}),
        )
        for r in rows
    ]
    return jsonify({"items": data, "total": total, "page": page, "page_size": limit})
```

- [ ] **Step 2: Run the focused tests and verify they pass**

Run: `pytest tests/test_web_routes.py -q -k "medias_list_is_shared_for_normal_users or medias_normal_user_can_read_update_and_delete_other_users_product"`

Expected: PASS.

- [ ] **Step 3: Run the existing medias route regressions**

Run: `pytest tests/test_web_routes.py -q -k "medias_list_is_shared_for_normal_users or medias_normal_user_can_read_update_and_delete_other_users_product or medias_page_contains_aligned_create_modal_layout"`

Expected: PASS, proving the shared-access change still coexists with the current medias page shell.

- [ ] **Step 4: Commit the shared-route behavior**

```bash
git add web/routes/medias.py tests/test_web_routes.py tests/conftest.py
git commit -m "feat: share medias library across users"
```

---

## Task 3: Add failing frontend tests for removing the admin-only scope toggle

**Files:**
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing page and script assertions**

```python
def test_medias_page_removes_admin_only_scope_toggle(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="scopeAll"' not in body
    assert 'id="chipScope"' not in body
    assert "window.MEDIAS_IS_ADMIN" not in body


def test_medias_scripts_do_not_use_admin_scope_switch():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "scopeAll" not in medias_js
    assert "scope=all" not in medias_js
    assert "MEDIAS_IS_ADMIN" not in medias_js
    assert "syncChip('chipScope', 'scopeAll');" not in medias_js
```

- [ ] **Step 2: Run the focused frontend tests and verify they fail**

Run: `pytest tests/test_web_routes.py -q -k "medias_page_removes_admin_only_scope_toggle or medias_scripts_do_not_use_admin_scope_switch"`

Expected: FAIL because the template still renders the admin-only chip and the script still builds the `scope=all` query.

- [ ] **Step 3: Commit the failing frontend tests**

```bash
git add tests/test_web_routes.py
git commit -m "test: lock medias shared-library frontend"
```

---

## Task 4: Remove the admin-only scope UI and frontend branch

**Files:**
- Modify: `web/templates/medias_list.html`
- Modify: `web/static/medias.js`

- [ ] **Step 1: Implement the minimal template and script cleanup**

```html
<!-- web/templates/medias_list.html -->
<div class="oc-toolbar">
  <div class="oc-search">
    <span class="leading"><svg width="14" height="14"><use href="#ic-search"/></svg></span>
    <input id="kw" placeholder="搜索产品名称或产品 ID">
  </div>
  <label class="oc-chip" id="chipArchived">
    <svg class="check" width="12" height="12"><use href="#ic-check"/></svg>
    <input type="checkbox" id="archived">
    <span>已归档</span>
  </label>
  <span class="oc-spacer"></span>
  <button class="oc-btn ghost" id="searchBtn">
    <svg width="14" height="14"><use href="#ic-search"/></svg>
    <span>搜索</span>
  </button>
</div>

<script>
  window.MEDIAS_TOS_READY = {{ 'true' if tos_ready else 'false' }};
</script>
<script src="{{ url_for('static', filename='medias.js') }}"></script>
```

```javascript
// web/static/medias.js
async function loadList() {
  const kw = $('kw').value.trim();
  const archived = $('archived').checked;
  const params = new URLSearchParams({ page: state.page });
  if (kw) params.set('keyword', kw);
  if (archived) params.set('archived', '1');
  renderSkeleton();
  try {
    await ensureLanguages();
    const data = await fetchJSON('/medias/api/products?' + params);
    renderGrid(data.items);
    renderPager(data.total, data.page, data.page_size);
    const pill = $('totalPill');
    if (pill) pill.textContent = `共 ${data.total} 个产品`;
  } catch (e) {
    $('grid').innerHTML = `
      <div class="oc-state">
        <div class="icon">${icon('alert', 28)}</div>
        <p class="title">加载失败</p>
        <p class="desc">${escapeHtml(e.message || '请稍后重试')}</p>
        <button class="oc-btn ghost" onclick="location.reload()">刷新页面</button>
      </div>`;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  $('searchBtn').addEventListener('click', () => { state.page = 1; loadList(); });
  $('kw').addEventListener('keydown', (e) => { if (e.key === 'Enter') { state.page = 1; loadList(); } });

  const syncChip = (chipId, inputId) => {
    const chip = $(chipId), inp = $(inputId);
    if (!chip || !inp) return;
    const sync = () => chip.classList.toggle('on', inp.checked);
    inp.addEventListener('change', () => { sync(); state.page = 1; loadList(); });
    sync();
  };
  syncChip('chipArchived', 'archived');
});
```

- [ ] **Step 2: Run the focused frontend tests and verify they pass**

Run: `pytest tests/test_web_routes.py -q -k "medias_page_removes_admin_only_scope_toggle or medias_scripts_do_not_use_admin_scope_switch"`

Expected: PASS.

- [ ] **Step 3: Run the existing medias page regressions**

Run: `pytest tests/test_web_routes.py -q -k "aligned_create_modal_layout or aligned_edit_modal_layout or marks_copy_as_required_in_add_modal or wraps_video_titles_in_edit_modal or centers_new_item_submit_button_in_edit_modal or shrinks_edit_modal_video_cards_to_eighty_percent"`

Expected: PASS, confirming the shared-library cleanup did not disturb the current medias UI layout.

- [ ] **Step 4: Commit the frontend cleanup**

```bash
git add web/templates/medias_list.html web/static/medias.js tests/test_web_routes.py
git commit -m "refactor: remove medias admin-only scope toggle"
```

---

## Task 5: Final verification and handoff

**Files:**
- Modify: `tests/test_web_routes.py` (only if a regression fix is needed during verification)

- [ ] **Step 1: Run the combined medias test slice**

Run: `pytest tests/test_web_routes.py -q -k "medias_list_is_shared_for_normal_users or medias_normal_user_can_read_update_and_delete_other_users_product or medias_page_removes_admin_only_scope_toggle or medias_scripts_do_not_use_admin_scope_switch or medias_page_contains_aligned_create_modal_layout or medias_page_contains_aligned_edit_modal_layout or medias_page_marks_copy_as_required_in_add_modal or medias_page_wraps_video_titles_in_edit_modal or medias_page_centers_new_item_submit_button_in_edit_modal or medias_page_shrinks_edit_modal_video_cards_to_eighty_percent"`

Expected: PASS for the full no-DB medias slice covering shared access and the current page layout rules.

- [ ] **Step 2: Check the working tree and summarize intentional diffs**

Run: `git status --short`

Expected: Only the planned files for shared medias behavior should remain modified. If unrelated files appear, leave them untouched and call them out in the handoff.

- [ ] **Step 3: Commit any final regression fix if needed**

```bash
git add tests/test_web_routes.py web/routes/medias.py web/templates/medias_list.html web/static/medias.js tests/conftest.py
git commit -m "test: finalize medias shared-library regressions"
```

- [ ] **Step 4: Prepare the execution handoff summary**

```text
Implemented the shared-library medias behavior:
- all logged-in users now query the same products list
- product/item access no longer depends on creator ownership
- the admin-only “查看全部” UI and scope query branch are removed
- route and page regressions cover normal-user read/write access
```
