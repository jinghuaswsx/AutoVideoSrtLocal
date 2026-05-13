# Tabcut Video Price Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist each Tabcut video candidate's primary item price and expose backend price filters for the future frontend.

**Architecture:** Extend the existing Tabcut normalization path to extract price fields once, store them on `tabcut_video_candidates`, and keep the list API reading from candidate columns with raw JSON fallback. Add a narrow backfill CLI for existing candidate rows instead of changing crawler behavior outside Tabcut.

**Tech Stack:** Python 3.12, Flask route adapters, MySQL migrations, pytest.

---

### Task 1: Schema And Price Normalization

**Files:**
- Modify: `appcore/tabcut_selection/models.py`
- Create: `db/migrations/2026_05_13_tabcut_video_candidate_price.sql`
- Modify: `tests/test_tabcut_selection_scoring.py`
- Modify: `tests/test_tabcut_selection_schema.py`

- [x] Write tests proving `normalize_video_row()` extracts `primary_item_price_min`, `primary_item_price_max`, and `price_currency` from `itemList[0].skuPrice`.
- [x] Run `pytest tests/test_tabcut_selection_scoring.py::test_normalize_video_row_extracts_primary_item_price -q` and confirm it fails before implementation.
- [x] Add focused helpers in `models.py` for currency/price extraction and reuse them from `normalize_video_row()`.
- [x] Add the migration with three candidate price columns and a price index.
- [x] Run the schema and normalization tests.

### Task 2: Candidate Persistence And Query Filters

**Files:**
- Modify: `tools/tabcut_crawler/runner.py`
- Modify: `appcore/tabcut_selection/store.py`
- Modify: `appcore/tabcut_selection/service.py`
- Modify: `tests/test_tabcut_selection_store.py`
- Modify: `tests/test_tabcut_crawler.py`

- [x] Write tests proving candidates carry video price fields into `store.upsert_video_candidate()`.
- [x] Write tests proving `list_video_candidates()` accepts `min_item_price` and `max_item_price`.
- [x] Run the new tests and confirm they fail before implementation.
- [x] Add candidate price fields in `_build_candidates()`.
- [x] Extend `upsert_video_candidate()` insert/update/select logic and expose `currency_symbol` from `price_currency`.
- [x] Run focused store/crawler tests.

### Task 3: Existing Data Backfill

**Files:**
- Create: `tools/tabcut_price_backfill.py`
- Create: `tests/test_tabcut_price_backfill.py`

- [x] Write tests for parsing price from `candidate_json.video.raw.itemList[0].skuPrice`, `candidate_json.goods.price_min`, and goods snapshot fallback.
- [x] Run `pytest tests/test_tabcut_price_backfill.py -q` and confirm failure before implementation.
- [x] Implement the CLI with `--dry-run`, `--batch-size`, and `--limit`.
- [x] Run the backfill tests.

### Task 4: Verification

**Files:**
- No new production files.

- [x] Run `pytest tests/test_tabcut_selection_scoring.py tests/test_tabcut_selection_store.py tests/test_tabcut_crawler.py tests/test_tabcut_selection_schema.py tests/test_tabcut_price_backfill.py -q`.
- [x] Do not run live DB backfill from a worktree without a configured non-local DB target. Report the exact command for test/prod after deploy.
