# Meta Hot Posts Europe Fit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 10-minute Meta hot-post Europe-fit evaluator and expose the best 50 materials in the Meta hot posts UI.

**Architecture:** Add one focused assessment module under `appcore/meta_hot_posts`, extend `store.py` for queue/result persistence, and register a new scheduler task with takeover semantics. The UI reuses the existing card renderer with an inner sub tab and a Top50 API.

**Tech Stack:** Python 3.12, Flask, pytest, MySQL migrations, existing `appcore.llm_client`, existing `appcore.llm_media_optimizer`.

---

### Task 1: Registry And Schema

**Files:**
- Modify: `appcore/llm_use_cases.py`
- Create: `db/migrations/2026_05_14_meta_hot_posts_europe_fit.sql`
- Test: `tests/test_llm_use_cases_registry.py`
- Test: `tests/test_db_migration_meta_hot_posts_marked.py`

- [ ] Add failing tests asserting `meta_hot_posts.europe_fit` is registered as `openrouter / google/gemini-3-flash-preview / openrouter`, and the migration creates `meta_hot_post_europe_assessments`.
- [ ] Run `pytest tests/test_llm_use_cases_registry.py::test_meta_hot_posts_europe_fit_use_case_is_registered_for_billing tests/test_db_migration_meta_hot_posts_marked.py::test_meta_hot_posts_europe_fit_migration_creates_assessment_table -q` and confirm failure.
- [ ] Add the use case and migration.
- [ ] Re-run the same tests and confirm pass.

### Task 2: Store Queue And Results

**Files:**
- Modify: `appcore/meta_hot_posts/store.py`
- Test: `tests/test_meta_hot_posts_store.py`

- [ ] Add failing tests for selecting pending Europe-fit rows, marking running, saving success/failure, resetting running rows for takeover, and listing Top50.
- [ ] Run the new store tests and confirm failure.
- [ ] Implement SQL helpers: `next_pending_europe_fit_materials`, `mark_europe_fit_running`, `finish_europe_fit_assessment`, `reset_running_europe_fit_assessments`, and `list_top_europe_fit_materials`.
- [ ] Re-run store tests and confirm pass.

### Task 3: LLM Assessment Module

**Files:**
- Create: `appcore/meta_hot_posts/europe_fit.py`
- Test: `tests/test_meta_hot_posts_europe_fit.py`

- [ ] Add failing tests for prompt content, JSON normalization, video optimizer usage, missing local video failure, and successful result shape.
- [ ] Run `pytest tests/test_meta_hot_posts_europe_fit.py -q` and confirm failure.
- [ ] Implement assessment prompt/schema, response normalization, and `assess_material`.
- [ ] Re-run Europe-fit tests and confirm pass.

### Task 4: Scheduler And Task Registration

**Files:**
- Modify: `appcore/meta_hot_posts/scheduler.py`
- Modify: `appcore/scheduled_tasks.py`
- Test: `tests/test_meta_hot_posts_scheduler.py`
- Test: `tests/test_appcore_scheduled_tasks.py`

- [ ] Add failing tests for 10-minute scheduler registration, default batch limit 30, takeover of previous running run, and scheduled task metadata.
- [ ] Run focused scheduler/task tests and confirm failure.
- [ ] Implement `europe_fit_tick_once`, cooperative run-current checks, and `register()` wiring.
- [ ] Re-run focused tests and confirm pass.

### Task 5: Service, Routes, And UI

**Files:**
- Modify: `appcore/meta_hot_posts/service.py`
- Modify: `web/routes/xuanpin.py`
- Modify: `web/templates/meta_hot_posts.html`
- Test: `tests/test_meta_hot_posts_service.py`
- Test: `tests/test_meta_hot_posts_routes.py`

- [ ] Add failing tests for Top50 response hydration, manual Europe-fit trigger, route delegation, and template sub-tab/API wiring.
- [ ] Run focused route/service tests and confirm failure.
- [ ] Implement response builders, Flask routes, sub-tab controls, Top50 loading, and manual trigger button.
- [ ] Re-run focused tests and confirm pass.

### Task 6: Final Verification

**Files:**
- All touched files.

- [ ] Run `pytest tests/test_meta_hot_posts_europe_fit.py tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_scheduler.py tests/test_meta_hot_posts_service.py tests/test_meta_hot_posts_routes.py tests/test_appcore_scheduled_tasks.py tests/test_llm_use_cases_registry.py tests/test_db_migration_meta_hot_posts_marked.py -q`.
- [ ] Run `python -m compileall appcore/meta_hot_posts appcore/llm_use_cases.py web/routes/xuanpin.py`.
- [ ] Do not run any command that connects to Windows local MySQL `127.0.0.1:3306`.
