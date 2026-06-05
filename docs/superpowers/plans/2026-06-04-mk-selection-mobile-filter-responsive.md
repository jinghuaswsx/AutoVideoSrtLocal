# 明空选品移动端筛选区适配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/xuanpin/mk` filter controls fully visible on mobile without changing search/filter behavior.

**Architecture:** Keep the existing Jinja template and JavaScript behavior. Add a focused mobile CSS override in `mk_selection.html`, plus a static template test that locks the responsive layout contract.

**Tech Stack:** Flask/Jinja template, inline page CSS, pytest static template assertions.

---

### Task 1: Add Responsive Layout Contract Test

**Files:**
- Modify: `tests/test_mk_selection_routes.py`

- [ ] **Step 1: Write the failing test**

Add this test near the other `mk_selection.html` static template tests:

```python
def test_mk_selection_filter_toolbar_has_mobile_grid_layout():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "docs/superpowers/specs/2026-06-04-mk-selection-mobile-filter-responsive-design.md" in template
    assert "@media (max-width: 768px)" in template
    assert ".oc-header--actions { width:100%; }" in template
    assert "grid-template-columns:repeat(2, minmax(0, 1fr));" in template
    assert ".oc-search #searchInput { grid-column:1 / -1; width:100% !important; }" in template
    assert ".oc-search > .oc-btn { width:100%; justify-content:center; }" in template
    assert ".mk-video-library-head { flex-wrap:wrap; align-items:flex-start; }" in template
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/test_mk_selection_routes.py::test_mk_selection_filter_toolbar_has_mobile_grid_layout -q
```

Expected: FAIL because the responsive contract strings are not yet present in `mk_selection.html`.

### Task 2: Implement Mobile CSS Override

**Files:**
- Modify: `web/templates/mk_selection.html`

- [ ] **Step 1: Add the mobile CSS**

Inside the existing `@media (max-width: 768px)` block near the end of `mk_selection.html`, add the focused layout override:

```css
  /* Docs-anchor: docs/superpowers/specs/2026-06-04-mk-selection-mobile-filter-responsive-design.md */
  .oc-header--actions { width:100%; }
  .oc-search {
    display:grid;
    grid-template-columns:repeat(2, minmax(0, 1fr));
    width:100%;
    gap:8px;
    align-items:stretch;
    flex-wrap:wrap;
  }
  .oc-search > .oc-input,
  .oc-search > .oc-select,
  .oc-search > .oc-btn {
    width:100%;
    min-width:0;
  }
  .oc-search #searchInput { grid-column:1 / -1; width:100% !important; }
  .oc-search > .oc-btn { justify-content:center; }
  .mk-video-library-head { flex-wrap:wrap; align-items:flex-start; }
  .mk-video-library-head > * { min-width:0; }
```

Keep the existing `.mk-library-tabs` horizontal scrolling rules unchanged.

- [ ] **Step 2: Add the extra narrow adjustment**

Add a `max-width: 420px` block after the `max-width: 768px` block:

```css
@media (max-width: 420px) {
  .oc-header { margin-bottom:10px; }
  .oc-search { gap:6px; }
  .oc-search > .oc-input,
  .oc-search > .oc-btn {
    padding-left:8px;
    padding-right:8px;
  }
}
```

- [ ] **Step 3: Run the focused test**

Run:

```bash
pytest tests/test_mk_selection_routes.py::test_mk_selection_filter_toolbar_has_mobile_grid_layout -q
```

Expected: PASS.

### Task 3: Run Focused Verification

**Files:**
- Read-only verification only.

- [ ] **Step 1: Run route/template tests**

Run:

```bash
pytest tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run compile check**

Run:

```bash
python -m compileall appcore web tests -q
```

Expected: exit code 0.

- [ ] **Step 3: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected: exit code 0 with no whitespace errors.
