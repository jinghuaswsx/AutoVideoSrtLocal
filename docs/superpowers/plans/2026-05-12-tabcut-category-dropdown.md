# Tabcut Category Dropdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the free-text TABCUT Category filter with a dropdown of available TikTok level-1 categories.

**Architecture:** Reuse the existing `category_l1` query parameter and exact-match filtering already implemented in `appcore.tabcut_selection.store`. Add a small category-options query/service/API adapter, then hydrate the page select from `/xuanpin/api/tabcut/categories`.

**Tech Stack:** Python 3.12, Flask, pytest, Jinja template with inline JavaScript.

---

### Task 1: Category Options Query and API

**Files:**
- Modify: `appcore/tabcut_selection/store.py`
- Modify: `appcore/tabcut_selection/service.py`
- Modify: `web/routes/medias/tabcut_selection.py`
- Modify: `web/routes/xuanpin.py`
- Test: `tests/test_tabcut_selection_store.py`
- Test: `tests/test_tabcut_selection_routes.py`
- Test: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Write the failing store test**

Add a test that calls `store.list_category_options(query_fn=fake_query)` and expects a sorted list of distinct non-empty level-1 category names with counts.

```python
def test_list_category_options_returns_distinct_l1_names():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [
            {"value": "Beauty", "label": "Beauty", "video_count": 12, "goods_count": 7},
            {"value": "Food", "label": "Food", "video_count": 3, "goods_count": 9},
        ]

    result = store.list_category_options(query_fn=fake_query)

    sql, params = calls[0]
    assert result == [
        {"value": "Beauty", "label": "Beauty", "video_count": 12, "goods_count": 7},
        {"value": "Food", "label": "Food", "video_count": 3, "goods_count": 9},
    ]
    assert "tabcut_video_candidates" in sql
    assert "tabcut_goods" in sql
    assert "category_l1_name" in sql
    assert params == ["US", "US"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tabcut_selection_store.py::test_list_category_options_returns_distinct_l1_names -q`

Expected: FAIL with `AttributeError: module 'appcore.tabcut_selection.store' has no attribute 'list_category_options'`.

- [ ] **Step 3: Implement the query and service wrapper**

Add `list_category_options(args=None, query_fn=query)` in `store.py`; it should merge distinct level-1 category names from candidates and goods, ignore blank names, count appearances, and sort by label. Add `build_category_options_response(args)` in `service.py` returning `{"items": ...}`.

- [ ] **Step 4: Add route tests and route adapters**

Add tests proving `/medias/api/tabcut-selection/categories` and `/xuanpin/api/tabcut/categories` delegate to the service and return `{"items": [...]}` for admins. Then add the two Flask routes by following the existing videos/goods route pattern.

- [ ] **Step 5: Run API tests**

Run: `pytest tests/test_tabcut_selection_store.py tests/test_tabcut_selection_routes.py tests/test_xuanpin_routes.py -q`

Expected: PASS.

### Task 2: Page Dropdown

**Files:**
- Modify: `web/templates/tabcut_selection.html`
- Test: `tests/test_tabcut_selection_routes.py`
- Test: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Write the failing page tests**

Assert the page contains `<select class="tabcut-select" id="categoryL1">`, an `All` option, and the category endpoint string `/xuanpin/api/tabcut/categories`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tabcut_selection_routes.py::test_tabcut_selection_page_renders_tabs tests/test_xuanpin_routes.py::test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api -q`

Expected: FAIL because the current page still renders an `<input>` and does not fetch category options.

- [ ] **Step 3: Implement the dropdown**

Replace the Category input with a select:

```html
<select class="tabcut-select" id="categoryL1">
  <option value="">All</option>
</select>
```

Add `loadCategoryOptions()` in the page script. It should fetch `/xuanpin/api/tabcut/categories`, append escaped option labels, preserve the current selected value when possible, and run once before `loadTabcut(1)`.

- [ ] **Step 4: Run page tests**

Run: `pytest tests/test_tabcut_selection_routes.py tests/test_xuanpin_routes.py -q`

Expected: PASS.

### Task 3: Verification and Release

**Files:**
- Verify only.

- [ ] **Step 1: Run targeted tests**

Run: `pytest tests/test_tabcut_selection_store.py tests/test_tabcut_selection_routes.py tests/test_xuanpin_routes.py tests/test_tabcut_crawler.py -q`

Expected: PASS.

- [ ] **Step 2: Start a local dev server on an unused port**

Run: `python -m web.app` with a non-production port such as `5055` if available.

Expected: server starts without a traceback.

- [ ] **Step 3: Verify route smoke**

Use HTTP requests against the local dev server to confirm unauthenticated `/xuanpin/tabcut` returns 302 rather than 500.

- [ ] **Step 4: Merge and deploy**

Merge the branch to `master`, push `origin master`, then run the documented SSH release flow for `/opt/autovideosrt-test` and `/opt/autovideosrt`.
