# Niuma Subtitle Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Niuma as a selectable subtitle-removal backend without duplicating the existing third-party subtitle-removal API implementation.

**Architecture:** Reuse `appcore/subtitle_removal_provider.py` and `appcore/subtitle_removal_runtime.py`. Add `niuma_main` to infrastructure credentials, then branch inside the existing route/runtime/provider surfaces by `subtitle_backend`.

**Tech Stack:** Python 3.12, Flask, pytest, requests, existing TOS source storage helpers.

---

### Task 1: Failing Tests

**Files:**
- Modify: `tests/test_infra_credentials.py`
- Modify: `tests/test_subtitle_removal_provider.py`
- Modify: `tests/test_subtitle_removal_runtime.py`
- Modify: `tests/test_subtitle_removal_routes.py`
- Modify: `tests/test_web_routes.py`
- Modify: `tests/test_runner_lifecycle.py` only if dispatch changes require it

- [ ] Add infra tests for `niuma_main` schema and runtime sync into `config.NIUMA_ERASE_*`.
- [ ] Add provider tests proving `credential_code="niuma_main"` reads Niuma config and preserves the existing default behavior.
- [ ] Add runtime test proving Niuma uses the live-compatible `videoName={task_id}_0_0_{x1}_{y1}_{x2}_{y2}` shape and passes `credential_code="niuma_main"` to existing submit/query functions.
- [ ] Add route tests for accepting `subtitle_backend=niuma`, treating upload as TOS-backed, list filtering, labels, and hiding erase type from non-Volc backends.
- [ ] Add UI tests for visible "牛马" upload radio and list filter pill.
- [ ] Run targeted tests and confirm the new assertions fail before implementation.

### Task 2: Config and Credentials

**Files:**
- Modify: `config.py`
- Modify: `appcore/infra_credentials.py`
- Modify: `.env.example`
- Create: `db/migrations/20250515_add_niuma_credentials.sql`

- [ ] Add `NIUMA_ERASE_API_KEY` and `NIUMA_ERASE_BASE_URL`.
- [ ] Add `niuma_main` schema/display metadata and `external_api` group.
- [ ] Seed `niuma_main` in migration with default base URL and empty API key.
- [ ] Keep API key out of tracked files.

### Task 3: Provider and Runtime Reuse

**Files:**
- Modify: `appcore/subtitle_removal_provider.py`
- Modify: `appcore/subtitle_removal_runtime.py`
- Modify: `web/services/subtitle_removal_runner.py` only if necessary

- [ ] Parameterize provider config lookup so existing default calls are unchanged.
- [ ] In runtime, detect `subtitle_backend=niuma`, use Niuma credential code, and build the Niuma-specific video name.
- [ ] Keep existing Volc/goodline and VOD behavior unchanged.

### Task 4: Routes and UI

**Files:**
- Modify: `web/routes/subtitle_removal.py`
- Modify: `web/templates/subtitle_removal_list.html`
- Modify: `web/templates/_subtitle_removal_upload_panel.html`
- Modify: `web/templates/_subtitle_removal_scripts.html`

- [ ] Add `niuma` backend normalization and label.
- [ ] Treat Niuma as non-local/TOS-backed in bootstrap and complete.
- [ ] Store `erase_text_type` only for Volc; return empty erase type for Niuma/local VSR.
- [ ] Add visible Niuma controls and frontend label handling.

### Task 5: Verification and Deploy

**Files:**
- All touched files.

- [ ] Run targeted pytest commands without any local MySQL access.
- [ ] Start dev server on an unused local port if route rendering needs browser/manual verification.
- [ ] Commit, push, deploy through the documented server flow, apply migration, configure `niuma_main.api_key` on the server, restart as part of deployment, and verify server HTTP status plus Niuma flow where practical.
