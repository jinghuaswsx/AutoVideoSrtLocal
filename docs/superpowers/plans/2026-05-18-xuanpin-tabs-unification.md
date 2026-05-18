# Xuanpin Tabs Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify the five selection-center top tabs to the Meta hot posts visual style and page-navigation loading behavior.

**Architecture:** Add reusable Jinja partials for selection-center top-tab markup and style, then include them from all five xuanpin templates with an `active` value. Keep each page's existing data loader and permissions untouched.

**Tech Stack:** Python 3.12, Flask/Jinja templates, pytest route/template assertions.

---

### Task 1: Lock Expected Template Behavior

**Files:**
- Modify: `tests/test_mk_selection_routes.py`
- Modify: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Write failing assertions**

Add assertions that the rendered pages include `xuanpin-tabs` / `xuanpin-tab`, active aria state, and no longer include legacy page-specific top-tab classes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q`

Expected: failures mention missing `xuanpin-tabs` or still-present legacy classes.

### Task 2: Add Shared Tab Partial

**Files:**
- Create: `web/templates/_xuanpin_tabs.html`
- Create: `web/templates/_xuanpin_tabs_style.html`

- [ ] **Step 1: Implement reusable markup and CSS**

Create a Jinja partial that renders five `/xuanpin/*` links. The partial reads `active` and sets `active` class and `aria-selected`.

Create a style partial that defines `xuanpin-tabs` / `xuanpin-tab` styling based on the Meta hot posts top-tab appearance.

- [ ] **Step 2: Keep the partial self-contained**

Do not add JavaScript. Navigation remains normal links, so each target page keeps its own existing load function.

### Task 3: Replace Per-Page Top Tabs

**Files:**
- Modify: `web/templates/mk_selection.html`
- Modify: `web/templates/meta_hot_posts.html`
- Modify: `web/templates/tabcut_selection.html`
- Modify: `web/templates/today_recommendations.html`
- Modify: `web/templates/new_product_review_list.html`

- [ ] **Step 1: Include the shared partial**

Set `active` before each include, for example:

```jinja
{% set active = "mk" %}
{% include "_xuanpin_tabs.html" %}
```

Include the shared CSS from each page's `extra_style` block:

```jinja
{% include "_xuanpin_tabs_style.html" %}
```

- [ ] **Step 2: Remove legacy top-tab CSS and markup**

Remove only the top-level selection-center tab definitions and nav blocks. Leave Meta sub-tabs, TABCUT view tabs, page filters, loaders, tables, and cards unchanged.

### Task 4: Verify

**Files:**
- Test: `tests/test_mk_selection_routes.py`
- Test: `tests/test_xuanpin_routes.py`
- Test: `tests/test_meta_hot_posts_routes.py`
- Test: `tests/test_tabcut_selection_routes.py`

- [ ] **Step 1: Run targeted route/template tests**

Run: `pytest tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py tests/test_meta_hot_posts_routes.py tests/test_tabcut_selection_routes.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Inspect final diff**

Run: `git diff -- web/templates tests docs/superpowers/specs/2026-05-18-xuanpin-tabs-unification-design.md docs/superpowers/plans/2026-05-18-xuanpin-tabs-unification.md`

Expected: diff only touches the shared tab partial, five xuanpin templates, related tests, and the new docs.
