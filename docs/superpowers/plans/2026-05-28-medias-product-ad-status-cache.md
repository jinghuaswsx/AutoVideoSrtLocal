# 素材管理产品投放汇总缓存 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fast cached product and language ad metrics to the素材管理 product list, including overall ROAS, per-language push/ad ROAS rows, delivery status, and delivery-status filtering.

**Architecture:** Add two MySQL cache tables maintained hourly by APScheduler. Product list requests read cache rows in bulk for the current page and filter by cached product status. The frontend renders the cached payload without extra per-row network calls.

**Tech Stack:** Python 3.12, Flask service layer, MySQL-compatible SQL migrations, APScheduler, vanilla JS/CSS, pytest.

---

### Task 1: Cache Schema And Service Tests

**Files:**
- Create: `db/migrations/2026_05_28_media_product_ad_status_cache.sql`
- Create: `appcore/media_product_ad_status_cache.py`
- Test: `tests/test_media_product_ad_status_cache.py`

- [ ] **Step 1: Write failing tests**

Create tests for:

- `_roas(300, 100) == 3.0`
- `_delivery_status(100, 10) == "active"`
- `_delivery_status(100, 0) == "stopped"`
- `_delivery_status(0, 0) == "never"`
- `get_product_ad_summary_cache([1, 2])` returns a dict keyed by product id
- `get_product_lang_ad_summary_cache([1, 2])` returns nested product/language rows
- `refresh_all()` calls product and language refresh SQL in one transaction without connecting to a real DB when `get_conn` is monkeypatched

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest tests/test_media_product_ad_status_cache.py -q
```

Expected: import or attribute failures because the module does not exist yet.

- [ ] **Step 3: Implement schema and module**

Add SQL migration with `media_product_ad_summary_cache` and `media_product_lang_ad_summary_cache`.

Implement:

- constants `STATUS_ALL`, `STATUS_ACTIVE`, `STATUS_STOPPED`, `STATUS_NEVER`
- `_roas(numerator, denominator)`
- `_delivery_status(total_spend, active_spend)`
- `get_product_ad_summary_cache(product_ids)`
- `get_product_lang_ad_summary_cache(product_ids)`
- `refresh_all()`

- [ ] **Step 4: Verify tests pass**

Run:

```bash
pytest tests/test_media_product_ad_status_cache.py -q
```

Expected: all tests pass.

### Task 2: Product List API Integration

**Files:**
- Modify: `appcore/medias.py`
- Modify: `web/services/media_products_listing.py`
- Modify: `web/routes/medias/_serializers.py`
- Test: `tests/test_medias_list_filters.py`
- Test: `tests/test_media_products_listing_service.py`

- [ ] **Step 1: Write failing tests**

Add tests that:

- `medias.list_products(..., delivery_status="active")` joins or filters against `media_product_ad_summary_cache`
- invalid `delivery_status` falls back to `all`
- `/medias/api/products?delivery_status=stopped` passes `stopped` to `list_products`
- `build_products_list_response()` loads product and language ad caches for current page IDs
- serializer returns `ad_summary` and `lang_ad_summary`

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest tests/test_medias_list_filters.py tests/test_media_products_listing_service.py -q
```

Expected: failures for missing parameter/cache fields.

- [ ] **Step 3: Implement backend integration**

Update filter constants, list-products SQL, product list service dependencies, and serializer parameters.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
pytest tests/test_medias_list_filters.py tests/test_media_products_listing_service.py -q
```

Expected: all tests pass.

### Task 3: Scheduler Registration

**Files:**
- Create: `appcore/media_product_ad_status_cache_scheduler.py`
- Modify: `appcore/scheduler.py`
- Modify: `appcore/scheduled_tasks.py`
- Test: `tests/test_media_product_ad_status_cache_scheduler.py`
- Test: `tests/test_appcore_scheduled_tasks.py`

- [ ] **Step 1: Write failing tests**

Add tests for:

- scheduler registers `media_product_ad_status_cache_refresh` with interval hours=1
- `tick_once()` wraps refresh in `scheduled_tasks.start_run` / `finish_run`
- `scheduled_tasks.task_definitions()` includes the new task and spec path

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest tests/test_media_product_ad_status_cache_scheduler.py tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_media_product_ad_status_cache_refresh -q
```

Expected: missing module / missing task definition.

- [ ] **Step 3: Implement scheduler**

Create scheduler module mirroring `push_status_cache_scheduler.py`, register it in `appcore/scheduler.py`, and add the task definition.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
pytest tests/test_media_product_ad_status_cache_scheduler.py tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_media_product_ad_status_cache_refresh -q
```

Expected: all tests pass.

### Task 4: Frontend Rendering And Filter

**Files:**
- Modify: `web/templates/medias_list.html`
- Modify: `web/static/medias.js`
- Test: `tests/test_medias_list_filters.py`
- Test: `tests/test_medias_translation_assets.py`

- [ ] **Step 1: Write failing static tests**

Assert the template/JS contain:

- `id="filterDeliveryStatus"`
- labels `投放情况：全部`, `投放情况：投放中`, `投放情况：终止投放`, `投放情况：未投`
- request parameter `delivery_status`
- table header `投放情况`
- render helpers/classes for `overall-roas`, `lang-push-zero`, and delivery status pills

- [ ] **Step 2: Verify tests fail**

Run:

```bash
pytest tests/test_medias_list_filters.py::test_medias_list_html_has_filter_dropdowns tests/test_medias_list_filters.py::test_medias_toolbar_compacts_actions_and_filters tests/test_medias_translation_assets.py::test_medias_list_keeps_compact_lang_coverage_rows -q
```

Expected: static assertions fail.

- [ ] **Step 3: Implement JS/CSS/template**

Update toolbar grid to four filters, add select, include request param, adjust table colgroup/header/row cells, and render cached payload.

- [ ] **Step 4: Verify tests pass**

Run:

```bash
pytest tests/test_medias_list_filters.py::test_medias_list_html_has_filter_dropdowns tests/test_medias_list_filters.py::test_medias_toolbar_compacts_actions_and_filters tests/test_medias_translation_assets.py::test_medias_list_keeps_compact_lang_coverage_rows -q
```

Expected: all tests pass.

### Task 5: Final Verification

**Files:** all modified files.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
pytest tests/test_media_product_ad_status_cache.py tests/test_media_product_ad_status_cache_scheduler.py tests/test_media_products_listing_service.py tests/test_medias_list_filters.py tests/test_medias_translation_assets.py::test_medias_list_keeps_compact_lang_coverage_rows tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_media_product_ad_status_cache_refresh -q
```

Expected: all tests pass without touching local MySQL.

- [ ] **Step 2: Review diff**

Run:

```bash
git diff --stat
git diff --check
```

Expected: no whitespace errors; changes are limited to docs, migration, cache module, scheduler, list service, serializer, and frontend.
