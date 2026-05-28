# Material Evaluation Progress UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix material AI evaluation video preview and show live per-country progress for async manual evaluation.

**Architecture:** Add a material evaluation run repository backed by `material_evaluation_runs`, wire POST evaluate to async run creation, instrument per-country evaluation with progress callbacks, and poll that status from both existing AI evaluation modals.

**Tech Stack:** Python 3.12, Flask, MySQL migrations, vanilla JavaScript static assets, pytest.

---

### Task 1: Video Preview Route

**Files:**
- Modify: `web/routes/medias/evaluation.py`
- Test: `tests/test_medias_routes.py`

- [x] Write failing test for relative eval clip paths and Range response.
- [x] Resolve clip path to an absolute path before returning it.
- [x] Return through `send_file_with_range()` so browser video tags get 206 for Range.
- [x] Run `pytest tests/test_medias_routes.py::test_manual_ai_evaluate_clip_resolves_relative_eval_clip_path -q`.

### Task 2: Async Run API

**Files:**
- Create: `appcore/material_evaluation_runs.py`
- Create: `db/migrations/2026_05_28_material_evaluation_runs.sql`
- Modify: `web/services/media_evaluation.py`
- Modify: `web/routes/medias/evaluation.py`
- Modify: `web/routes/medias/__init__.py`
- Test: `tests/test_media_evaluation_service.py`, `tests/test_medias_routes.py`

- [x] Write failing tests for async start and status response.
- [x] Add run create/update/get helpers and background runner.
- [x] Make POST evaluate async by default, while preserving `?sync=1`.
- [x] Add status route with product access checks.
- [x] Run the service and route tests.

### Task 3: Per-Country Progress

**Files:**
- Modify: `appcore/material_evaluation.py`
- Test: `tests/test_material_evaluation.py`

- [x] Write failing test for one failed country continuing to later countries.
- [x] Add progress snapshot helpers and optional `progress_callback`.
- [x] Mark countries queued/running/completed/failed.
- [x] Continue after individual country exceptions and make final result require review when any country fails.
- [x] Run `pytest tests/test_material_evaluation.py::test_evaluate_countries_records_failed_country_and_continues -q`.

### Task 4: Modal UI

**Files:**
- Modify: `web/static/medias.js`
- Modify: `web/static/pushes.js`
- Test: `tests/test_medias_ai_evaluation_modal_assets.py`, `tests/test_pushes_ui_assets.py`

- [x] Write failing static tests for status polling and country cards.
- [x] Add status endpoint helpers and polling loop.
- [x] Render country progress cards in the AI evaluation modal.
- [x] On completion, refresh product and render final result.
- [x] Run static asset tests.

### Task 5: Verification

**Files:**
- All touched files.

- [x] Run targeted pytest set.
- [x] Run Python compile checks for touched Python modules.
- [x] Inspect `git diff --check`.
- [ ] Commit the completed branch.
