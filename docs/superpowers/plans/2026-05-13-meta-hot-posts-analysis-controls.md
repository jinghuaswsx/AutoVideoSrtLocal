# Meta Hot Posts Analysis Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Meta hot post product analysis run every 10 minutes with up to 100 product links per run, and expose category prompt and failure records in the backend page.

**Architecture:** Keep the existing `meta_hot_post_product_analyses` table as the analysis queue and failure log. Add read APIs in the Meta hot posts service/store layer, wire admin routes, and add lightweight modal UI controls to `meta_hot_posts.html`.

**Tech Stack:** Python 3.12, Flask, MySQL, APScheduler, pytest, vanilla template JavaScript.

---

### Task 1: Scheduler Batch Size

**Files:**
- Modify: `appcore/meta_hot_posts/scheduler.py`
- Modify: `appcore/scheduled_tasks.py`
- Test: `tests/test_meta_hot_posts_scheduler.py`
- Test: `tests/test_appcore_scheduled_tasks.py`

- [ ] Add tests asserting `analysis_tick_once()` defaults to `limit=100` and scheduler metadata mentions 100 links per run.
- [ ] Update `analysis_tick_once(limit=100)` and scheduled task description.
- [ ] Run `pytest tests/test_meta_hot_posts_scheduler.py tests/test_appcore_scheduled_tasks.py -q`.

### Task 2: Prompt And Failure APIs

**Files:**
- Modify: `appcore/meta_hot_posts/store.py`
- Modify: `appcore/meta_hot_posts/service.py`
- Modify: `web/routes/xuanpin.py`
- Test: `tests/test_meta_hot_posts_store.py`
- Test: `tests/test_meta_hot_posts_routes.py`

- [ ] Add store query for failed analyses: `status='failed'`, ordered by `updated_at DESC`, capped at 100.
- [ ] Add service response for category prompt using `product_analysis.build_category_prompt()` and category pool options.
- [ ] Add service response for failure rows with decoded SKU JSON omitted.
- [ ] Add admin routes `GET /xuanpin/api/meta-hot-posts/category-prompt` and `GET /xuanpin/api/meta-hot-posts/failures`.
- [ ] Run `pytest tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_routes.py -q`.

### Task 3: Backend Page Controls

**Files:**
- Modify: `web/templates/meta_hot_posts.html`
- Test: `tests/test_meta_hot_posts_routes.py`

- [ ] Add the top tool button row above filters.
- [ ] Add modals for category prompt and failed analysis records.
- [ ] Change manual analysis request body to `{limit: 100}`.
- [ ] Run `pytest tests/test_meta_hot_posts_routes.py -q`.

### Task 4: Verification And Release

**Files:**
- No new source files.

- [ ] Run `pytest tests/test_meta_hot_posts_scheduler.py tests/test_meta_hot_posts_product_analysis.py tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_routes.py tests/test_appcore_scheduled_tasks.py tests/test_xuanpin_routes.py -q`.
- [ ] Run `git diff --check`.
- [ ] Commit with `Docs-anchor: docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md#定时任务`.
- [ ] Push `HEAD:master`.
- [ ] Pull and restart `/opt/autovideosrt-test` and `/opt/autovideosrt`; verify services active and HTTP 302/200 as appropriate.

### Task 5: Singleton Guard And Billing Registration

**Files:**
- Modify: `appcore/meta_hot_posts/scheduler.py`
- Modify: `appcore/meta_hot_posts/product_analysis.py`
- Modify: `appcore/meta_hot_posts/store.py`
- Modify: `appcore/scheduled_tasks.py`
- Modify: `appcore/llm_use_cases.py`
- Create: `db/migrations/2026_05_13_meta_hot_posts_llm_binding.sql`
- Test: `tests/test_meta_hot_posts_scheduler.py`
- Test: `tests/test_meta_hot_posts_product_analysis.py`
- Test: `tests/test_llm_use_cases_registry.py`

- [ ] Add scheduler tests for skipping when an analysis run started within one hour.
- [ ] Add scheduler tests for marking stale running runs failed and then starting a new run.
- [ ] Add product analysis test proving `user_id` is passed into `llm_client.invoke_generate`.
- [ ] Add migration binding `meta_hot_posts.categorize -> gemini_vertex / gemini-3-flash-preview`.
- [ ] Add or update registry tests proving the use case is present, token-priced, and Gemini Vertex backed.
