# Mobile Header Selected Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the mobile global header show the active sidebar module icon and label instead of the fixed site brand.

**Architecture:** Keep `layout.html` as the single shell source. The sidebar `active` menu item remains the current-module source of truth; a small client-side sync script copies that item into the mobile header and falls back to `AutoVideoSrt` when no active item exists.

**Tech Stack:** Flask/Jinja template, inline shell CSS/JS, pytest static/template assertions.

---

### Task 1: Add Regression Test

**Files:**
- Create: `tests/test_mobile_header_selected_module.py`
- Modify: none
- Test: `tests/test_mobile_header_selected_module.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _layout_source() -> str:
    return (ROOT / "web" / "templates" / "layout.html").read_text(encoding="utf-8")


def test_mobile_header_has_selected_module_sync_contract():
    source = _layout_source()

    assert 'data-mobile-brand-icon' in source
    assert 'data-mobile-brand-text' in source
    assert 'syncMobileModuleBrand' in source
    assert "document.querySelector('.sidebar-nav a.active')" in source
    assert "textEl.textContent = label" in source
    assert "iconEl.textContent = icon" in source
    assert "brand.setAttribute('href', href)" in source


def test_order_analytics_sidebar_entry_provides_mobile_header_source():
    source = _layout_source()

    assert '<a href="/order-analytics" target="_blank" rel="noopener noreferrer" {% if request.path.startswith' in source
    assert '<span class="nav-icon">📊</span> 数据分析' in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mobile_header_selected_module.py -q`

Expected: the static contract test fails because `data-mobile-brand-icon` and `syncMobileModuleBrand` do not exist yet.

### Task 2: Implement Mobile Header Sync

**Files:**
- Modify: `web/templates/layout.html`
- Test: `tests/test_mobile_header_selected_module.py`

- [ ] **Step 1: Update mobile brand markup and CSS**

Change the mobile brand icon/text spans to include stable data attributes and add a text span class that can ellipsize.

- [ ] **Step 2: Add the sync script**

Add a small IIFE named `syncMobileModuleBrand` after the mobile sidebar drawer script. It should read `.sidebar-nav a.active`, extract icon/label, update the mobile brand, and leave the fallback unchanged when no active item exists.

- [ ] **Step 3: Run target tests**

Run: `pytest tests/test_mobile_header_selected_module.py -q`

Expected: all tests pass.

### Task 3: Related Regression

**Files:**
- Existing layout/menu tests only

- [ ] **Step 1: Run focused menu and layout tests**

Run: `pytest tests/test_mobile_header_selected_module.py tests/test_tools_routes.py tests/test_av_sync_menu_routes.py tests/test_medias_admin_query.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Inspect diff**

Run: `git diff -- docs/superpowers/specs/2026-05-10-mobile-header-selected-module.md docs/superpowers/plans/2026-05-10-mobile-header-selected-module.md tests/test_mobile_header_selected_module.py web/templates/layout.html`

Expected: only the spec, plan, test, and layout template changed.
