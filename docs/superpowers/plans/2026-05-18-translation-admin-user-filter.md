# Translation Admin User Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a superadmin-only creator filter to the multi-translate and omni-translate list pages.

**Architecture:** Keep project listing and creator-option SQL inside `appcore.translation_route_store`. Route modules parse and validate `user_id`, pass filter context to templates, and templates render a native select that preserves the current language filter.

**Tech Stack:** Python 3.12, Flask, Jinja, pytest.

---

### Task 1: Route-Store Query Contract

**Files:**
- Modify: `appcore/translation_route_store.py`
- Test: `tests/test_translation_route_store.py`

- [ ] Add failing tests for `filter_user_id` admin scoping, non-admin ignoring crafted filters, and `list_project_creators()`.
- [ ] Run `pytest tests/test_translation_route_store.py -q`; expected failure references the missing argument/function.
- [ ] Add `filter_user_id` to `list_projects_with_creator()` and create `list_project_creators()`.
- [ ] Re-run `pytest tests/test_translation_route_store.py -q`; expected pass.

### Task 2: Multi-Translate Route And Template

**Files:**
- Modify: `web/routes/multi_translate.py`
- Modify: `web/templates/multi_translate_list.html`
- Test: `tests/test_multi_translate_routes.py`

- [ ] Add failing tests for `/multi-translate?user_id=237`, selector rendering, and normal user ignoring `user_id`.
- [ ] Run focused multi tests; expected failure references missing SQL args or missing selector HTML.
- [ ] Parse `user_id` only for superadmin, load `user_filter_options`, pass `show_user_filter`, `current_user_filter`, and options to the template.
- [ ] Render a compact select and preserve `lang` in URLs.
- [ ] Re-run focused multi tests; expected pass.

### Task 3: Omni-Translate Route And Template

**Files:**
- Modify: `web/routes/omni_translate.py`
- Modify: `web/templates/omni_translate_list.html`
- Test: `tests/test_omni_translate_routes.py`

- [ ] Add failing tests for `/omni-translate?user_id=237`, selector rendering, and normal user ignoring `user_id`.
- [ ] Run focused omni tests; expected failure references missing SQL args or missing selector HTML.
- [ ] Mirror the multi route/template behavior for omni.
- [ ] Re-run focused omni tests; expected pass.

### Task 4: Verification

**Files:**
- No new files.

- [ ] Run `pytest tests/test_translation_route_store.py tests/test_multi_translate_routes.py tests/test_omni_translate_routes.py -q`.
- [ ] Check unauthenticated list routes with Flask client return 302.
- [ ] Check authenticated `/multi-translate` and `/omni-translate` return 200.
