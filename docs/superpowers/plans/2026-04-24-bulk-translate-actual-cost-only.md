# Bulk Translate Actual-Cost-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove bulk translate pre-run estimate logic and show only actual cost after work completes.

**Architecture:** Stop computing `estimate` during task creation, keep `cost_tracking.actual` as the only live pricing structure for new tasks, and update all task creation/detail UIs to stop reading or displaying estimated price. Preserve backward compatibility by tolerating old tasks that still have `estimate`.

**Tech Stack:** Flask, Python runtime orchestration, vanilla JS frontends, pytest.

---

### Task 1: Lock runtime behavior with tests

**Files:**
- Modify: `tests/test_bulk_translate_runtime.py`
- Modify: `tests/test_bulk_translate_routes.py`
- Modify: `tests/test_bulk_translate_detail_assets.py`

- [ ] Add failing tests that assert new bulk translate tasks do not persist `cost_tracking.estimate` or `estimated_cost_cny` audit detail.
- [ ] Add failing tests that assert `/api/bulk-translate/estimate` no longer delegates to estimator pricing logic.
- [ ] Add failing tests that assert detail rendering no longer shows estimate copy.
- [ ] Run the focused failing tests and confirm they fail for the expected reasons.

### Task 2: Remove runtime estimate dependency

**Files:**
- Modify: `appcore/bulk_translate_runtime.py`
- Modify: `web/routes/bulk_translate.py`

- [ ] Update task creation to stop calling `do_estimate`.
- [ ] Initialize new task state with `cost_tracking.actual` only.
- [ ] Remove `estimated_cost_cny` from create audit details.
- [ ] Change `/api/bulk-translate/estimate` to return an explicit disabled payload without estimator work.
- [ ] Re-run the runtime/route tests and make them pass.

### Task 3: Remove estimate from projections and UI

**Files:**
- Modify: `appcore/bulk_translate_projection.py`
- Modify: `web/static/bulk_translate_detail.js`
- Modify: `web/static/bulk_translate_ui.js`
- Modify: `web/static/medias_translate_modal.js`

- [ ] Update projection/list/detail serializers so they tolerate missing `estimate` and expose actual cost cleanly.
- [ ] Remove estimate polling and estimate-based confirmation copy from the legacy bulk translate UI.
- [ ] Update medias translation modal copy so creation is direct and pricing is described as post-completion actual cost.
- [ ] Update detail summary cards to show actual cost only, with incomplete-task fallback wording.
- [ ] Re-run the UI-facing tests and any affected route tests.

### Task 4: Final regression verification

**Files:**
- Modify: `tests/test_bulk_translate_runtime.py`
- Modify: `tests/test_bulk_translate_routes.py`
- Modify: `tests/test_bulk_translate_detail_assets.py`
- Modify: any newly touched focused tests

- [ ] Run `pytest tests/test_medias_raw_sources_translate.py tests/test_bulk_translate_routes.py tests/test_bulk_translate_runtime.py tests/test_bulk_translate_detail_assets.py -q`.
- [ ] Run `pytest tests/test_bulk_translate_estimator.py -q` only if estimator code is still imported or kept alive by compatibility paths.
- [ ] Review the changed files for any remaining estimate-specific UI text or state assumptions.
- [ ] Summarize behavior changes, verification results, and residual compatibility notes.
