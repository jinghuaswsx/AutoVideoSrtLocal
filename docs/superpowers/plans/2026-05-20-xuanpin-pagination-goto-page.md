# Xuanpin Pagination Goto Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a compact `去 [page] 页` input to every existing product-center pagination component so pressing Enter jumps to the requested page.

**Architecture:** Keep pagination behavior local to the existing template render functions. Add tiny page-normalization helpers in each template so inputs clamp to `1..totalPages` before invoking the current loader function.

**Tech Stack:** Flask/Jinja templates, inline JavaScript, pytest template/route assertions.

---

Docs anchor: `docs/superpowers/specs/2026-05-20-xuanpin-pagination-goto-page-design.md`

### Task 1: RED Tests

**Files:**
- Modify: `tests/test_mk_selection_routes.py`
- Modify: `tests/test_meta_hot_posts_routes.py`
- Modify: `tests/test_tabcut_selection_routes.py`
- Modify: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Add failing template assertions**

Add assertions that require:

```python
assert "function normalizeMkGotoPage(raw, totalPages)" in template
assert "function handleMkGotoPage(event, loaderName, totalPages)" in template
assert 'class="oc-pager-goto"' in template
assert "onkeydown=\"handleMkGotoPage(event, '${loaderName}', ${totalPages})\"" in template
assert "onkeydown=\"handleMkGotoPage(event, 'loadData', ${totalPages})\"" in template
assert "function normalizeMetaHotGotoPage(raw, totalPages)" in body
assert "function handleMetaHotGotoPage(event, loaderName, totalPages)" in body
assert 'class="mh-pager-goto"' in body
assert "onkeydown=\"handleMetaHotGotoPage(event, '${loaderName}', ${totalPages})\"" in body
assert "function normalizeTabcutGotoPage(raw, totalPages)" in body
assert "function handleTabcutGotoPage(event, totalPages)" in body
assert 'class="tabcut-pager-goto"' in body
assert 'onkeydown="handleTabcutGotoPage(event, ${totalPages})"' in body
```

- [ ] **Step 2: Run RED tests**

Run:

```bash
pytest tests/test_mk_selection_routes.py::test_mk_selection_material_archive_tabs_have_top_pagers_and_page100 tests/test_meta_hot_posts_routes.py::test_meta_hot_posts_page_renders_tabs_and_api tests/test_tabcut_selection_routes.py::test_tabcut_selection_page_renders_tabs tests/test_xuanpin_routes.py::test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api -q
```

Expected: fail because the goto helpers and markup are not present yet.

### Task 2: Implement Template Helpers

**Files:**
- Modify: `web/templates/mk_selection.html`
- Modify: `web/templates/meta_hot_posts.html`
- Modify: `web/templates/tabcut_selection.html`

- [ ] **Step 1: Add pager input styles**

Add local compact styles under each existing pager style:

```css
.oc-pager-goto, .mh-pager-goto, .tabcut-pager-goto {
  display:inline-flex;
  align-items:center;
  gap:6px;
}
```

Use page-specific selectors and existing colors instead of one shared selector.

- [ ] **Step 2: Add page normalization and keydown helpers**

In each template script, add a helper equivalent to:

```javascript
function normalizeMkGotoPage(raw, totalPages) {
  const last = Math.max(1, Number(totalPages || 1));
  const page = Number.parseInt(String(raw || '').trim(), 10);
  if (!Number.isFinite(page) || page < 1) return 1;
  return Math.min(last, page);
}
function handleMkGotoPage(event, loaderName, totalPages) {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  const page = normalizeMkGotoPage(event.currentTarget.value, totalPages);
  const loader = window[loaderName];
  if (typeof loader === 'function') loader(page);
}
```

Adapt names for Meta Hot and TABCUT.

- [ ] **Step 3: Render goto controls**

Append `去 <input ...> 页` to each existing pager HTML:

```javascript
`<label class="oc-pager-goto">去 <input type="number" min="1" max="${totalPages}" value="${pageNum}" onkeydown="handleMkGotoPage(event, '${loaderName}', ${totalPages})"> 页</label>`
```

Use the current page variable and current total pages variable for each template.

### Task 3: GREEN Verification

**Files:**
- Test: `tests/test_mk_selection_routes.py`
- Test: `tests/test_meta_hot_posts_routes.py`
- Test: `tests/test_tabcut_selection_routes.py`
- Test: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_mk_selection_routes.py::test_mk_selection_material_archive_tabs_have_top_pagers_and_page100 tests/test_meta_hot_posts_routes.py::test_meta_hot_posts_page_renders_tabs_and_api tests/test_tabcut_selection_routes.py::test_tabcut_selection_page_renders_tabs tests/test_xuanpin_routes.py::test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full related suite**

Run:

```bash
pytest tests/test_mk_selection_routes.py tests/test_meta_hot_posts_routes.py tests/test_tabcut_selection_routes.py tests/test_xuanpin_routes.py -q
```

Expected: all related tests pass without DB access to Windows local MySQL.
