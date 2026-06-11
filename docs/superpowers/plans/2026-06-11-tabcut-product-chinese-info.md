# Tabcut Product Chinese Info Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Chinese product information to Tabcut video cards and immersive overlays, with scheduled and manual Gemini 3.1 Flash Lite translation.

**Architecture:** Store Chinese fields on `tabcut_goods`, hydrate them through existing Tabcut list APIs, and keep translation orchestration in a small `appcore.tabcut_selection.goods_translation` module. Reuse existing APScheduler registration and Tabcut route aliases for manual translation.

**Tech Stack:** Python 3.12, Flask, MySQL migrations, APScheduler, existing `appcore.llm_client`, Jinja template JavaScript, pytest.

---

### Task 1: Docs And Migration Tests

**Files:**
- Modify: `docs/superpowers/specs/2026-06-11-tabcut-product-chinese-info-design.md`
- Modify: `docs/superpowers/plans/2026-06-11-tabcut-product-chinese-info.md`
- Test: `tests/test_tabcut_selection_schema.py`

- [x] Add a test that reads `db/migrations/2026_06_11_tabcut_goods_chinese_info.sql` and asserts all Chinese fields plus `tabcut.translate_goods_info` binding exist.
- [x] Create the migration with idempotent `information_schema.COLUMNS` guard and OpenRouter Gemini 3.1 Flash Lite binding.
- [x] Run `pytest tests/test_tabcut_selection_schema.py -q` and commit docs + migration.

### Task 2: Store And Translation Service

**Files:**
- Create: `appcore/tabcut_selection/goods_translation.py`
- Modify: `appcore/tabcut_selection/store.py`
- Modify: `appcore/tabcut_selection/service.py`
- Test: `tests/test_tabcut_goods_translation.py`
- Test: `tests/test_tabcut_selection_store.py`

- [x] Write failing tests for `translate_goods_info`, pending-row selection, running/done/failed updates, and hydrated response fields.
- [x] Implement JSON prompt parsing and store helpers.
- [x] Run `pytest tests/test_tabcut_goods_translation.py tests/test_tabcut_selection_store.py -q` and commit.

### Task 3: Routes And Scheduler

**Files:**
- Modify: `web/routes/medias/tabcut_selection.py`
- Modify: `web/routes/xuanpin.py`
- Modify: `appcore/tabcut_selection/scheduler.py`
- Modify: `appcore/scheduled_tasks.py`
- Test: `tests/test_tabcut_selection_routes.py`
- Test: `tests/test_xuanpin_routes.py`
- Test: `tests/test_appcore_scheduled_tasks.py`

- [x] Write failing tests for manual translate API aliases and scheduler registration.
- [x] Implement manual API response and scheduled tick.
- [x] Run route/scheduler focused tests and commit.

### Task 4: Template UI

**Files:**
- Modify: `web/templates/tabcut_selection.html`
- Test: `tests/test_tabcut_selection_routes.py`
- Test: `tests/test_xuanpin_routes.py`

- [x] Write failing template assertions for card Chinese info, translate button, non-mobile default expanded overlay, and overlay Chinese detail fields.
- [x] Implement product title/category helpers, manual translate fetch, card refresh, and overlay refresh.
- [x] Run template route tests and commit.

### Task 5: Verification And Release

**Files:**
- All changed files

- [x] Run focused tests listed in the spec.
- [x] Run `python3 scripts/pytest_related.py --base origin/master --run`.
- [x] Run `python3 -m compileall appcore/tabcut_selection web/routes tests -q && git diff --check`.
- [ ] Merge latest `origin/master`, push to `master`, deploy test and production, and smoke `/xuanpin/tabcut` plus manual translation API.
