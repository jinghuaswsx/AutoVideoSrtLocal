# Admin Unified API Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make API configuration admin-only and globally shared from the single username `admin`.

**Architecture:** Centralize config ownership in `appcore.api_keys`, then protect `/settings` and sidebar visibility at the web layer. Keep task ownership and billing user IDs unchanged.

**Tech Stack:** Flask, Flask-Login, MySQL-backed DAO helpers, pytest.

---

### Task 1: Centralize API Config Owner

**Files:**
- Modify: `appcore/api_keys.py`
- Test: `tests/test_appcore_api_keys.py`

- [ ] Write tests proving normal users read admin-owned config and cannot write API config.
- [ ] Add helpers to resolve username `admin` to a config owner id.
- [ ] Make `get_key`, `resolve_key`, `resolve_extra`, `get_all`, and `resolve_jianying_project_root` read admin-owned rows.
- [ ] Make `set_key` reject writes unless the passed `user_id` belongs to username `admin`.

### Task 2: Restrict Settings Entry

**Files:**
- Modify: `web/routes/settings.py`
- Modify: `web/templates/layout.html`
- Test: `tests/test_settings_routes_new.py`

- [ ] Write tests for `/settings` 403 for normal users and role-admin users whose username is not `admin`.
- [ ] Add a username-based API config permission helper/decorator.
- [ ] Apply it to `/settings` GET/POST.
- [ ] Hide the sidebar API 配置 link unless `current_user.username == "admin"`.

### Task 3: Verify Focused Behavior

**Files:**
- Test: `tests/test_appcore_api_keys.py`
- Test: `tests/test_settings_routes_new.py`
- Test: `tests/test_web_routes.py`

- [ ] Run focused pytest files.
- [ ] Fix regressions caused by tests that assumed any admin role could manage API config.
- [ ] Run syntax compile for touched Python files.
