# Task Center Translation Product Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add direct inspection buttons for every small-language translation output shown in the task-center child-task drawer.

**Architecture:** `appcore.tasks` owns URL/action construction and enriches readiness checks plus artifact rows with stable `actions`. Task center JavaScript renders those actions without rebuilding business URLs. Existing media-management pages keep handling the real product/video UI; the bridge query parameters open the right product, language, panel, or product-link modal.

**Tech Stack:** Python 3.12, Flask blueprints, Jinja templates, vanilla JavaScript, pytest.

---

## File Structure

- Modify `appcore/tasks.py`: add action helpers, detail-image/copywriting task lookup helpers, enrich child readiness checks, and enrich task artifact rows.
- Modify `tests/test_appcore_tasks_supporting_data.py`: cover readiness actions and artifact actions without touching a real DB.
- Modify `web/templates/tasks_list.html`: render readiness/action buttons and artifact action buttons, using safe internal/external links.
- Modify `web/templates/medias_list.html`: extend task-center bridge query handling for `video`, `cover`, `copywriting`, `detail_images`, and `product_links`.
- Modify `web/routes/copywriting_translate.py`: add read-only page route and API route for `copywriting_translate` projects.
- Create `web/templates/copywriting_translate_detail.html`: read-only status/source/output page.
- Modify `tests/test_copywriting_translate_routes.py`: cover the new API/page permissions and payload shape.
- Modify `appcore/bulk_translate_projection.py`: return `/copywriting-translate/<id>` for `copywriting_translate` children.
- Modify `web/static/bulk_translate_detail.js`: return the same route in client-side fallback.
- Modify `tests/test_bulk_translate_projection.py`: assert the new detail URL.

## Adopted Anchors

- `AGENTS.md`
- `docs/superpowers/specs/2026-05-21-task-center-translation-product-actions-design.md`
- `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
- `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`
- `docs/superpowers/specs/2026-05-20-task-center-step-review-assets-design.md`
- `docs/superpowers/specs/2026-04-18-bulk-translate-design.md`
- `docs/superpowers/specs/2026-04-19-medias-localized-detail-image-translation-design.md`
- `docs/superpowers/specs/2026-05-09-product-link-management-modal.md`

## Task 1: Backend Readiness Actions

**Files:**
- Modify: `tests/test_appcore_tasks_supporting_data.py`
- Modify: `appcore/tasks.py`

- [ ] **Step 1: Write failing tests**

Add assertions to `test_get_child_readiness_returns_missing_when_lang_item_absent` that `localized_media_item.actions` contains:

```python
[
    {
        "label": "去生成/绑定素材",
        "url": "/medias/?q=robot-kit-rjc&from_task=44&product=9&lang=de&action=translate",
        "kind": "locate",
        "primary": True,
    }
]
```

Update `test_get_child_readiness_computes_payload` so the fake target item includes:

```python
{
    "id": 5,
    "product_id": product_id,
    "lang": lang,
    "object_key": "media/de/result.mp4",
    "cover_object_key": "media/de/cover.jpg",
}
```

Add assertions that:

```python
by_key = {check["key"]: check for check in payload["checks"]}
assert by_key["translated_video"]["actions"][0] == {
    "label": "预览视频",
    "url": "/medias/object?object_key=media%2Fde%2Fresult.mp4",
    "kind": "preview",
    "primary": True,
}
assert by_key["translated_cover"]["actions"][0] == {
    "label": "查看封面",
    "url": "/medias/item-cover/5",
    "kind": "preview",
    "primary": True,
}
assert by_key["translated_copywriting"]["actions"][0]["url"].endswith("action=copywriting")
assert by_key["detail_images"]["actions"][0]["url"].endswith("action=detail_images")
assert by_key["shopify_images"]["actions"][0]["url"].endswith("action=product_links&focus=shopify_images")
assert by_key["product_links"]["actions"][0]["url"].endswith("action=product_links&focus=product_links")
assert by_key["product_links"]["actions"][1] == {
    "label": "打开 newjoyloo.com",
    "url": "https://newjoyloo.com/de/products/robot-kit-rjc",
    "kind": "external",
}
```

Monkeypatch optional lookup helpers in this test:

```python
monkeypatch.setattr(tasks, "_recent_copywriting_translate_task_id", lambda *a, **k: "copy-1")
monkeypatch.setattr(tasks, "_recent_detail_image_translate_task_id", lambda *a, **k: "img-1")
monkeypatch.setattr(tasks, "_detail_image_preview_rows", lambda *a, **k: [{"id": 31}, {"id": 32}, {"id": 33}, {"id": 34}])
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
pytest tests/test_appcore_tasks_supporting_data.py::test_get_child_readiness_returns_missing_when_lang_item_absent tests/test_appcore_tasks_supporting_data.py::test_get_child_readiness_computes_payload -q
```

Expected: FAIL because `actions` and helper functions are not implemented.

- [ ] **Step 3: Implement minimal backend actions**

In `appcore/tasks.py`, add helpers:

```python
def _action(label: str, url: str, kind: str, *, primary: bool = False, disabled_reason: str = "") -> dict:
    payload = {"label": label, "url": url, "kind": kind}
    if primary:
        payload["primary"] = True
    if disabled_reason:
        payload["disabled_reason"] = disabled_reason
    return payload

def _media_object_url(object_key: str) -> str:
    return "/medias/object?" + urlencode({"object_key": object_key})

def _medias_search_url(
    *,
    product_code: str,
    task_id: int,
    product_id: int,
    lang: str,
    action: str = "translate",
    extra: dict[str, object] | None = None,
) -> str:
    params = {
        "q": product_code or "",
        "from_task": str(task_id),
        "product": str(product_id),
        "lang": (lang or "").lower(),
        "action": action,
    }
    for key, value in (extra or {}).items():
        if value not in (None, ""):
            params[str(key)] = str(value)
    return "/medias/?" + urlencode(params)
```

Extend `_child_acceptance_payload()` to attach actions for every `_acceptance_check()` call, using the rules in the spec.

- [ ] **Step 4: Verify green**

Run the same two pytest nodes. Expected: PASS.

## Task 2: Artifact Actions

**Files:**
- Modify: `tests/test_appcore_tasks_supporting_data.py`
- Modify: `appcore/tasks.py`

- [ ] **Step 1: Write failing test**

Add `test_list_task_artifacts_includes_direct_actions()` that monkeypatches `tasks.query_all` to return one `media_items` row with `id=5`, `task_id=44`, `product_id=9`, `product_code="robot-kit-rjc"`, `lang="de"`, `object_key="media/de/result.mp4"`, and `cover_object_key="media/de/cover.jpg"`. Assert the returned row contains `actions` for preview video, view cover, locate material, and translation task record.

- [ ] **Step 2: Run test and verify red**

Run:

```bash
pytest tests/test_appcore_tasks_supporting_data.py::test_list_task_artifacts_includes_direct_actions -q
```

Expected: FAIL because artifact rows are returned raw.

- [ ] **Step 3: Implement artifact action enrichment**

In `appcore/tasks.py`, add `_artifact_actions(row)` and return:

```python
item = dict(row)
item["actions"] = _artifact_actions(item)
```

from both parent and child branches of `list_task_artifacts()`.

- [ ] **Step 4: Verify green**

Run the new test. Expected: PASS.

## Task 3: Copywriting Translate Read-Only Detail

**Files:**
- Modify: `tests/test_copywriting_translate_routes.py`
- Modify: `web/routes/copywriting_translate.py`
- Create: `web/templates/copywriting_translate_detail.html`
- Modify: `web/app.py`

- [ ] **Step 1: Write failing route tests**

Add tests that monkeypatch `web.routes.copywriting_translate.query_one`:

```python
def fake_query_one(sql, args=()):
    if "FROM projects" in sql:
        return {
            "id": "copy-1",
            "user_id": 1,
            "status": "done",
            "state_json": json.dumps({
                "source_copy_id": 101,
                "source_lang": "en",
                "target_lang": "de",
                "parent_task_id": "bulk-1",
                "target_copy_id": 202,
                "tokens_used": 88,
            }),
            "created_at": "2026-05-21 10:00:00",
            "updated_at": "2026-05-21 10:03:00",
        }
    if "id=%s" in sql and args == (101,):
        return {"id": 101, "product_id": 9, "lang": "en", "title": "Source", "body": "Source body"}
    if "id=%s" in sql and args == (202,):
        return {"id": 202, "product_id": 9, "lang": "de", "title": "Ziel", "body": "Ziel body"}
```

Assert:

```python
assert client_patched.get("/api/copywriting-translate/copy-1").status_code == 200
assert client_patched.get("/copywriting-translate/copy-1").status_code == 200
```

Add a normal-user fixture case where project `user_id` differs from `current_user.id`, and assert 404 for both endpoints.

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
pytest tests/test_copywriting_translate_routes.py::test_detail_api_returns_readonly_payload tests/test_copywriting_translate_routes.py::test_detail_page_renders_readonly_payload tests/test_copywriting_translate_routes.py::test_detail_rejects_non_owner_non_admin -q
```

Expected: FAIL because the routes do not exist.

- [ ] **Step 3: Implement routes and template**

In `web/routes/copywriting_translate.py`:

```python
from flask import abort, render_template
from appcore.db import query_one

pages_bp = Blueprint("copywriting_translate_pages", __name__)

@bp.get("/<task_id>")
@login_required
def api_detail(task_id: str):
    return _copywriting_translate_payload(task_id)

@pages_bp.get("/copywriting-translate/<task_id>")
@login_required
def detail_page(task_id: str):
    payload = _load_copywriting_translate_detail(task_id)
    return render_template("copywriting_translate_detail.html", detail=payload)
```

Register `pages_bp` in `web/app.py`.

- [ ] **Step 4: Verify green**

Run the three new route tests. Expected: PASS.

## Task 4: Bulk Translate Detail URL Fix

**Files:**
- Modify: `tests/test_bulk_translate_projection.py`
- Modify: `appcore/bulk_translate_projection.py`
- Modify: `web/static/bulk_translate_detail.js`

- [ ] **Step 1: Write failing projection test**

Add or update a test asserting:

```python
assert mod._child_detail_url("copywriting_translate", "copy-1") == "/copywriting-translate/copy-1"
```

- [ ] **Step 2: Run test and verify red**

Run:

```bash
pytest tests/test_bulk_translate_projection.py -q
```

Expected: FAIL on the new copywriting detail URL assertion.

- [ ] **Step 3: Implement URL fix**

Change both Python and JavaScript mappings from `/copywriting/<id>` or `None` to `/copywriting-translate/<id>`.

- [ ] **Step 4: Verify green**

Run `pytest tests/test_bulk_translate_projection.py -q`. Expected: PASS.

## Task 5: Task Center Frontend Rendering

**Files:**
- Modify: `web/templates/tasks_list.html`
- Modify: `tests/test_tasks_routes.py` or add a template-source assertion in an existing UI asset test.

- [ ] **Step 1: Write failing source assertion**

Add a lightweight test that reads `web/templates/tasks_list.html` and asserts it contains:

```python
"function tcRenderActionLinks"
"tc-action-link--primary"
"check.actions"
"it.actions"
```

- [ ] **Step 2: Run test and verify red**

Run the new test. Expected: FAIL because the helper does not exist.

- [ ] **Step 3: Implement rendering helper**

Add JavaScript helpers:

```javascript
function tcSafeHref(url) {
  const raw = String(url || '').trim();
  if (!raw) return '';
  if (raw.startsWith('/') && !raw.startsWith('//')) return raw;
  if (/^https?:\/\//i.test(raw)) return raw;
  return '';
}
function tcRenderActionLinks(actions) {
  const list = Array.isArray(actions) ? actions : [];
  if (!list.length) return '';
  return '<div class="tc-action-links">' + list.map(function(action) {
    const href = tcSafeHref(action.url);
    const label = tcEsc(action.label || '查看');
    if (!href) {
      return '<span class="tc-btn tc-btn--disabled">' + label + '</span>';
    }
    const cls = action.primary ? 'tc-btn tc-action-link tc-action-link--primary' : 'tc-btn tc-action-link';
    return '<a class="' + cls + '" href="' + tcEsc(href) + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
  }).join('') + '</div>';
}
```

Use `tcRenderActionLinks(check.actions || [])` in readiness rows and `tcRenderActionLinks(it.actions || [])` in artifact rows.

- [ ] **Step 4: Verify green**

Run the new frontend-source test. Expected: PASS.

## Task 6: Medias Bridge Actions

**Files:**
- Modify: `web/templates/medias_list.html`
- Add or modify a source assertion test.

- [ ] **Step 1: Write failing source assertion**

Add assertions that `web/templates/medias_list.html` contains the bridge cases for:

```python
"action === 'product_links'"
"action === 'copywriting'"
"action === 'detail_images'"
"action === 'video' || action === 'cover'"
"mbridgeFocusTarget"
```

- [ ] **Step 2: Run test and verify red**

Run the new source assertion. Expected: FAIL.

- [ ] **Step 3: Implement bridge behavior**

After the bridge clicks the language tab, wait for the edit modal body and:

- `product_links`: click `#edProductLinksOpenBtn`.
- `copywriting`: scroll and highlight `#edCwSection`.
- `detail_images`: scroll and highlight `#edDetailImagesSection`.
- `video` or `cover`: scroll and highlight `.oc-vitem[data-item="<item>"]` when present, otherwise `#edItemsSection`.

- [ ] **Step 4: Verify green**

Run the source assertion. Expected: PASS.

## Task 7: Verification and Commit

**Files:**
- Modify: `CHANGELOG.md` only if the repo already requires it for this branch; otherwise rely on the spec and plan docs as commit docs.

- [ ] **Step 1: Run targeted tests**

Run:

```bash
pytest tests/test_appcore_tasks_supporting_data.py tests/test_tasks_routes.py tests/test_copywriting_translate_routes.py tests/test_bulk_translate_projection.py -q
```

Expected: PASS.

- [ ] **Step 2: Run compile check**

Run:

```bash
python3 -m compileall appcore/tasks.py appcore/bulk_translate_projection.py web/routes/tasks.py web/routes/copywriting_translate.py
```

Expected: all four files compile successfully.

- [ ] **Step 3: Check worktree**

Run:

```bash
git status --short
```

Expected: only intentional docs, Python, template, JS, and test files changed.

- [ ] **Step 4: Commit**

Commit with:

```bash
git add docs/superpowers/plans/2026-05-21-task-center-translation-product-actions-implementation.md appcore/tasks.py appcore/bulk_translate_projection.py web/routes/copywriting_translate.py web/app.py web/templates/tasks_list.html web/templates/medias_list.html web/templates/copywriting_translate_detail.html web/static/bulk_translate_detail.js tests/test_appcore_tasks_supporting_data.py tests/test_copywriting_translate_routes.py tests/test_bulk_translate_projection.py tests/test_tasks_routes.py
git commit -m "feat(task-center): add translation product action links" -m "Docs-anchor: docs/superpowers/specs/2026-05-21-task-center-translation-product-actions-design.md"
```

Expected: commit succeeds and includes the docs anchor.

## Self-Review

- Spec coverage: readiness actions, artifact actions, product/video split, copywriting detail route, bulk detail URL, task-center rendering, and medias bridge actions each have a task above.
- Placeholder scan: no unresolved placeholder language remains.
- Type consistency: action objects use `label`, `url`, `kind`, `primary`, and optional `disabled_reason`, matching the spec.
