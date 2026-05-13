# Meta Hot Posts Analysis Pacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Limit Meta hot post product analysis throughput so scheduled runs process 30 products every 10 minutes with 20 seconds between products, while manual full catch-up can run at 10 seconds per product.

**Architecture:** Keep APScheduler registration unchanged at a 10-minute interval and move pacing into `appcore.meta_hot_posts.scheduler`. `analysis_tick_once()` owns scheduled defaults; lower-level product/category analysis helpers accept an injected sleep function so tests verify pacing without waiting.

**Tech Stack:** Python 3.12, Flask service layer, APScheduler, pytest.

---

### Task 1: Document Pacing Rules

**Files:**
- Modify: `docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md`

- [x] **Step 1:** Add requirements for scheduled `limit=30`, scheduled `per_item_delay_seconds=20`, manual catch-up `per_item_delay_seconds=10`, and singleton behavior.

### Task 2: Add Failing Tests

**Files:**
- Modify: `tests/test_meta_hot_posts_scheduler.py`
- Modify: `tests/test_appcore_scheduled_tasks.py`

- [x] **Step 1:** Update `analysis_tick_once()` default test to expect `limit=30` and `per_item_delay_seconds=20`.
- [x] **Step 2:** Add tests that two-item product analysis and category reanalysis call the injected sleep function exactly once between items.
- [x] **Step 3:** Update scheduled task definition test text from `100 个` to `30 个` and `20 秒`.

### Task 3: Implement Pacing

**Files:**
- Modify: `appcore/meta_hot_posts/scheduler.py`
- Modify: `appcore/meta_hot_posts/service.py`
- Modify: `appcore/scheduled_tasks.py`
- Modify: `web/templates/meta_hot_posts.html`

- [x] **Step 1:** Add constants for scheduled limit and delays.
- [x] **Step 2:** Thread `per_item_delay_seconds` and injectable `sleep_fn` through product and category analysis loops.
- [x] **Step 3:** Make `analysis_tick_once()` default to `limit=30`, `per_item_delay_seconds=20`.
- [x] **Step 4:** Let API payload override `per_item_delay_seconds`; make the UI manual button request `limit=30` and `per_item_delay_seconds=20`.
- [x] **Step 5:** Update task registry description.

### Task 4: Verify, Commit, Deploy

**Files:**
- All modified files above.

- [ ] **Step 1:** Run focused pytest command for Meta hot posts, scheduled task definitions, LLM use case, and xuanpin routes.
- [ ] **Step 2:** Commit with `Docs-anchor: docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md#Gemini 分类`.
- [ ] **Step 3:** Push to `origin/master`.
- [ ] **Step 4:** Pull and restart `/opt/autovideosrt-test` and `/opt/autovideosrt`; verify `active` and HTTP `302`.

### Task 5: Manual Catch-Up

**Files:**
- No code changes.

- [ ] **Step 1:** Run a production root Python command that calls `analysis_tick_once(limit=<remaining>, per_item_delay_seconds=10)` so all remaining pending products are processed at 10 seconds per product.
- [ ] **Step 2:** Query final counts and category distribution.
