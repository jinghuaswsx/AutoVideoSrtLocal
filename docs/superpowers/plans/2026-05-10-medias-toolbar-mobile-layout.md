# Medias Toolbar Mobile Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compact the `/medias` top controls so mobile users see two action buttons on one row and search/filter controls on one three-column row.

**Architecture:** Keep this as a template and event-binding cleanup. The Jinja template owns control grouping and responsive CSS; `web/static/medias.js` continues to own live search behavior and must not depend on the optional search button.

**Tech Stack:** Flask/Jinja2, inline page CSS in `web/templates/medias_list.html`, vanilla JavaScript in `web/static/medias.js`, pytest source-structure tests.

---

### Task 1: Add Failing Coverage

**Files:**
- Modify: `tests/test_medias_list_filters.py`

- [ ] Add a source-structure test asserting that:
  - `web/templates/medias_list.html` has `.oc-header-action-buttons` containing both `id="createBtn"` and `.oc-tool-download-btn`.
  - `.oc-toolbar-filter-row` uses `grid-template-columns:repeat(3, minmax(0, 1fr));`.
  - `id="searchBtn"` is absent from the template.
  - `web/static/medias.js` binds `kwInput` listeners without requiring `searchBtn`.

- [ ] Run:

```bash
pytest tests/test_medias_list_filters.py::test_medias_toolbar_compacts_actions_and_filters -q
```

Expected before implementation: the test fails because the template still renders `searchBtn`, lacks `.oc-header-action-buttons`, and the JS uses `if (searchBtn && kwInput)`.

### Task 2: Update Template Layout

**Files:**
- Modify: `web/templates/medias_list.html`

- [ ] Replace the loose header action layout with:
  - `.oc-header-actions`
  - `.oc-header-action-buttons`
  - download button and create button in the same button row.

- [ ] Replace the toolbar children with `.oc-toolbar-filter-row` containing only:
  - `.oc-search`
  - `#filterXmycMatch`
  - `#filterRoasStatus`

- [ ] Remove the visible `#searchBtn` button.

### Task 3: Decouple Search Event Binding

**Files:**
- Modify: `web/static/medias.js`

- [ ] Change DOMContentLoaded binding so `kwInput` always receives `input` and `keydown` listeners when it exists.
- [ ] Keep `searchBtn` click binding conditional, so older cached templates with a button still work.

### Task 4: Verify

**Files:**
- Test: `tests/test_medias_list_filters.py`
- Test: `tests/test_medias_pages_routes.py`
- Test: `tests/test_shopify_image_localizer_release_web.py`

- [ ] Run:

```bash
pytest tests/test_medias_list_filters.py tests/test_medias_pages_routes.py tests/test_shopify_image_localizer_release_web.py -q
```

- [ ] Start a dev server on a free local port:

```bash
python -m web.app
```

- [ ] Check `/medias` when logged out returns 302 rather than 500.
