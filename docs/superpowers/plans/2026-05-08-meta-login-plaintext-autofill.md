# Meta Login Plaintext Autofill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store `DXM01-Meta/facebook` login credentials in plaintext DB fields and use them to autofill Facebook login when Meta Ads Manager sessions expire.

**Architecture:** Add a small credential DAO backed by `browser_login_credentials`, a superadmin settings tab, and a Playwright CDP autofill helper. Existing Meta export flows call the helper only after detecting `FAILED_AUTH`, then retry the export once without logging credentials.

**Tech Stack:** Flask/Jinja settings page, PyMySQL DAO helpers, Playwright sync CDP, pytest monkeypatch tests.

---

## File Structure

- Create `db/migrations/2026_05_08_browser_login_credentials.sql`: plaintext credential table.
- Create `appcore/browser_login_credentials.py`: DAO, username masking, status updates.
- Create `appcore/meta_login_autofill.py`: login-page detection and CDP autofill helper.
- Modify `web/routes/settings.py`: add `browser_credentials` tab data and POST handling.
- Modify `web/templates/settings.html`: add superadmin tab and form.
- Modify `tools/roi_hourly_sync.py`: after `FAILED_AUTH`, autofill and retry one export.
- Modify `tools/meta_daily_final_sync.py`: after `FAILED_AUTH`, autofill and retry one export.
- Add tests in `tests/test_browser_login_credentials.py`, `tests/test_meta_login_autofill.py`, `tests/test_settings_browser_credentials.py`, `tests/test_meta_login_retry.py`.

## Task 1: DAO And Migration

- [x] Write failing tests for DAO upsert/read/mask/status.
- [x] Run `pytest tests/test_browser_login_credentials.py -q` and confirm failures from missing module/table SQL behavior.
- [x] Add migration and DAO.
- [x] Re-run `pytest tests/test_browser_login_credentials.py -q`.

## Task 2: Settings Tab

- [x] Write failing route/template tests for `browser_credentials` tab visibility, password blank preserving old value, and no password echo.
- [x] Run `pytest tests/test_settings_browser_credentials.py -q` and confirm failures.
- [x] Add route view helpers and Jinja form.
- [x] Re-run `pytest tests/test_settings_browser_credentials.py -q`.

## Task 3: Autofill Helper

- [x] Write failing tests for login-page detection, human-check detection, missing credential, and successful fake page autofill.
- [x] Run `pytest tests/test_meta_login_autofill.py -q` and confirm failures.
- [x] Implement `appcore/meta_login_autofill.py`.
- [x] Re-run `pytest tests/test_meta_login_autofill.py -q`.

## Task 4: Meta Export Retry

- [x] Write failing tests that `roi_hourly_sync` and `meta_daily_final_sync` call autofill after `FAILED_AUTH` and retry once.
- [x] Run `pytest tests/test_meta_login_retry.py -q` and confirm failures.
- [x] Wire helper into both export paths with sanitized errors.
- [x] Re-run `pytest tests/test_meta_login_retry.py -q`.

## Task 5: Regression And Storage

- [x] Run focused tests: `pytest tests/test_browser_login_credentials.py tests/test_meta_login_autofill.py tests/test_settings_browser_credentials.py tests/test_meta_login_retry.py tests/test_roi_hourly_sync_meta_multi_account.py tests/test_meta_server_sync_tools.py tests/test_settings_routes_new.py -q`.
- [x] Run `python -m py_compile appcore/browser_login_credentials.py appcore/meta_login_autofill.py web/routes/settings.py tools/roi_hourly_sync.py tools/meta_daily_final_sync.py`.
- [x] Run `git diff --check`.
- [ ] After code is deployed/applied, insert the provided `DXM01-Meta/facebook` credential through DAO or settings UI without putting it in command history or logs.

## Docs-anchor

- `docs/superpowers/specs/2026-05-08-meta-login-plaintext-autofill-design.md`
