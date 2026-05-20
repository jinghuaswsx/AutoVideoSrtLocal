# Mingkong Card Material Ad Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show cached product/material advertising status icons and a material-library search button on Mingkong material cards.

**Architecture:** Add a small materialized status cache maintained by an APScheduler task. The Mingkong card APIs enrich rows by reading cache rows only, and the existing `renderMkVideoMaterialCard()` renders icons and the `/medias/?q=` button from the enriched payload.

**Tech Stack:** Python 3.12, Flask, APScheduler, MySQL/SQLite test doubles, Jinja template JavaScript, pytest.

---

### Task 1: Cache Schema And Task Registration

**Files:**
- Create: `db/migrations/2026_05_20_mingkong_material_ad_status_cache.sql`
- Modify: `appcore/scheduled_tasks.py`
- Modify: `appcore/scheduler.py`
- Test: `tests/test_mingkong_materials_schema.py`
- Test: `tests/test_mingkong_materials_scheduler.py`

- [ ] Add a migration for `mingkong_material_ad_status_cache` with scope/hash lookup keys and status fields.
- [ ] Add `mingkong_material_ad_status_refresh` to `TASK_DEFINITIONS`.
- [ ] Register `appcore.mingkong_material_ad_status_scheduler.register()` from `appcore/scheduler.py`.
- [ ] Add tests that assert schema text and scheduled task metadata.

### Task 2: Status Cache Service

**Files:**
- Modify: `appcore/mingkong_materials.py`
- Create: `appcore/mingkong_material_ad_status_scheduler.py`
- Test: `tests/test_mingkong_materials.py`

- [ ] Add helpers for `media_search_code`, status lookup hash, and cache serialization.
- [ ] Add `refresh_ad_status_cache()` to compute product and material statuses into the cache table.
- [ ] Add cache enrichment helpers used by `list_material_library()` and `list_yesterday_top100()`.
- [ ] Add tests for product-code normalization, material binding status, product spend status, and cached enrichment.

### Task 3: Card UI

**Files:**
- Modify: `web/templates/mk_selection.html`
- Test: `tests/test_mk_selection_routes.py`

- [ ] Add CSS and SVG/HTML hooks for a top-right status icon cluster.
- [ ] Render product and material icons only when the corresponding cached boolean is true.
- [ ] Add a search icon link next to the product-code line, using `media_search_url`.
- [ ] Add template tests for the icon hooks and `/medias/?q=` search link.

### Task 4: Verification

**Files:**
- No new files.

- [ ] Run focused tests:

```bash
pytest tests/test_mingkong_materials.py tests/test_mingkong_materials_schema.py tests/test_mingkong_materials_scheduler.py tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
```

- [ ] Verify unauthenticated route behavior:

```bash
python -m web.app
curl -I http://127.0.0.1:<port>/xuanpin/mk
```

Expected unauthenticated status is `302`.
