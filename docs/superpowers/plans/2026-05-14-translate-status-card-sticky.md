# Translate Status Card Sticky Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the existing multi / omni translate status card visible below the shared topbar while users scroll long task detail pages.

**Architecture:** Reuse the single `#taskStatusCard` rendered by `_translate_detail_shell.html`. Add sticky CSS in the shared shell and lock it with a static template test.

**Tech Stack:** Jinja templates, CSS, pytest static template tests.

---

### Task 1: Lock Sticky Status Card Contract

**Files:**
- Modify: `tests/test_translate_detail_shell_templates.py`
- Modify: `web/templates/_translate_detail_shell.html`

- [ ] **Step 1: Write the failing test**

Add `test_translate_status_card_is_sticky_below_topbar()` to `tests/test_translate_detail_shell_templates.py`. It must read `_translate_detail_shell.html` and assert that `.task-status-card` uses `position: sticky`, desktop `top: 68px`, `z-index`, and a mobile override with `top: 60px`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_translate_status_card_is_sticky_below_topbar -q
```

Expected: failure because the current status card CSS has no sticky positioning.

- [ ] **Step 3: Implement minimal sticky CSS**

Update `.task-status-card` in `web/templates/_translate_detail_shell.html` to add `position: sticky`, `top: 68px`, `z-index`, and a subtle shadow / backdrop. Add a mobile media query with `top: 60px`.

- [ ] **Step 4: Run targeted and related tests**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py tests/test_multi_translate_routes.py tests/test_omni_translate_routes.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Smoke check route guard**

Start a local server on an unused port and verify an unauthenticated detail route returns `302`, not `500`.
