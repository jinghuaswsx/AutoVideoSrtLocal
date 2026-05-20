# Mingkong Product Local Aggregate Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/xuanpin/mk#products` read accurate locally maintained Mingkong product video count, 90-day spend, and ad count.

**Architecture:** Extend the existing Mingkong daily material snapshot job so it writes product-level aggregates into `mingkong_material_products` while keeping playable card rows path-only. The product list API joins the latest successful product aggregate snapshot and falls back to legacy ranking fields when no local aggregate exists.

**Tech Stack:** Python 3.12, Flask service layer, MySQL migrations, systemd timer, pytest.

---

### Task 1: Document And Regression Tests

**Files:**
- Modify: `docs/superpowers/specs/2026-05-18-mingkong-daily-material-snapshot-top100-design.md`
- Create: `docs/superpowers/specs/2026-05-20-mingkong-product-local-aggregate-stats-design.md`
- Modify: `tests/test_mingkong_materials.py`
- Modify: `tests/test_media_mk_selection_service.py`
- Modify: `tests/test_mingkong_materials_scheduler.py`
- Modify: `tests/test_mingkong_materials_schema.py`

- [ ] Add spec coverage for 05:00/17:00 and product aggregate semantics.
- [ ] Add failing tests for pathless non-hidden product rows, aggregate upsert columns, local aggregate API join, migration columns, and timer metadata.
- [ ] Run focused tests and confirm the new tests fail for the current implementation.

### Task 2: Snapshot Product Aggregates

**Files:**
- Modify: `appcore/mingkong_materials.py`
- Create: `db/migrations/2026_05_20_mingkong_material_product_aggregate_stats.sql`

- [ ] Add a helper that counts all non-hidden detail videos and sums `spends` / `ads_count`.
- [ ] Extend `record_product_status()` to upsert `video_count`, `path_video_count`, `total_90_spend`, and `total_ads`.
- [ ] Keep `material_count` as playable/path-backed row count.
- [ ] Add an idempotent migration for the new columns and lookup index.
- [ ] Run the Mingkong material tests and schema tests until green.

### Task 3: Product List Local Stats Read Path

**Files:**
- Modify: `web/services/media_mk_selection.py`
- Modify: `tests/test_media_mk_selection_service.py`
- Modify: `tests/test_mk_selection_routes.py`

- [ ] Join the latest successful `mingkong_material_products` aggregate snapshot by normalized product code.
- [ ] Prefer local aggregate values for `mk_video_count`, `mk_total_spends`, and `mk_total_ads`.
- [ ] Keep legacy `dianxiaomi_rankings.mk_*` fallback for older databases or products without a local aggregate.
- [ ] Run service and route tests until green.

### Task 4: Scheduler Time Change

**Files:**
- Modify: `appcore/scheduled_tasks.py`
- Modify: `deploy/server_browser/autovideosrt-mingkong-material-daily-snapshot.timer`
- Modify: `tests/test_mingkong_materials_scheduler.py`

- [ ] Change task metadata and timer calendar from 06:00/18:00 to 05:00/17:00.
- [ ] Run scheduler tests until green.

### Task 5: Verify, Commit, Deploy, And Seed Data

**Files:**
- Commit all touched docs, tests, migrations, service code, and deploy timer.

- [ ] Run focused pytest suite.
- [ ] Run `git diff --check`.
- [ ] Commit with `Docs-anchor: docs/superpowers/specs/2026-05-20-mingkong-product-local-aggregate-stats-design.md`.
- [ ] Merge to `master`, push, deploy to test and production.
- [ ] Restart services so migrations apply.
- [ ] Manually run one production `tools/mingkong_material_daily_snapshot.py` snapshot.
- [ ] Verify local aggregate data exists for `21-fitness-resistance-bands-4-tube-pedal-ankle-puller`.
