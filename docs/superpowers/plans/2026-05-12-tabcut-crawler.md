# Tabcut Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the US-only Tabcut daily selection module that collects Top500 videos and products, stores daily snapshots, exposes filtered results in 选品中心, and runs daily at 08:00 Beijing time on the production server under the `cjh` desktop Chrome session.

**Architecture:** Add a focused `appcore.tabcut_selection` service package for persistence, normalization, scoring, and query APIs; add `tools/tabcut_crawler` for CDP-backed Tabcut collection with hard 3-second throttling; add a small medias route adapter and a template tab that reads internal APIs. Scheduling is a systemd timer registered in `appcore/scheduled_tasks.py`.

**Tech Stack:** Python 3.12, Flask, MySQL-compatible SQL through `appcore.db`, Playwright over Chrome CDP, pytest.

---

## File Map

- Create `db/migrations/2026_05_12_tabcut_selection.sql`: production schema.
- Create `appcore/tabcut_selection/models.py`: normalized dataclasses and helper conversion.
- Create `appcore/tabcut_selection/scoring.py`: candidate scoring.
- Create `appcore/tabcut_selection/store.py`: upserts and filtered reads.
- Create `appcore/tabcut_selection/service.py`: page/API response builders and refresh trigger.
- Create `tools/tabcut_crawler/client.py`: CDP browser API client with throttle.
- Create `tools/tabcut_crawler/runner.py`: crawl orchestration.
- Create `tools/tabcut_crawler/main.py`: CLI entrypoint.
- Create `tools/tabcut_crawler/__init__.py`: package marker.
- Modify `appcore/scheduled_tasks.py`: register `tabcut_daily_selection`.
- Modify `web/routes/medias/pages.py`: add `/medias/tabcut-selection`.
- Modify `web/routes/medias/__init__.py`: import/export route adapter.
- Create `web/routes/medias/tabcut_selection.py`: admin-only JSON endpoints.
- Modify `web/templates/mk_selection.html`: add `TABCUT 选品` tab.
- Create `web/templates/tabcut_selection.html`: module UI.
- Create tests in `tests/test_tabcut_selection_*.py`.

## Task 1: Schema And Scheduled Task Registry

**Files:**
- Create: `db/migrations/2026_05_12_tabcut_selection.sql`
- Modify: `appcore/scheduled_tasks.py`
- Test: `tests/test_tabcut_selection_schema.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tabcut_selection_schema.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tabcut_schema_defines_required_tables_and_indexes():
    sql = (ROOT / "db" / "migrations" / "2026_05_12_tabcut_selection.sql").read_text(encoding="utf-8")
    for table in [
        "tabcut_crawl_runs",
        "tabcut_videos",
        "tabcut_video_snapshots",
        "tabcut_goods",
        "tabcut_goods_snapshots",
        "tabcut_video_candidates",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "uniq_tabcut_video_snapshot" in sql
    assert "uniq_tabcut_goods_snapshot" in sql
    assert "uniq_tabcut_video_candidate" in sql


def test_tabcut_daily_selection_registered():
    from appcore import scheduled_tasks

    task = scheduled_tasks.get_task_definition("tabcut_daily_selection")
    assert task["runner"] == "tools/tabcut_crawler/main.py"
    assert "08:00" in task["schedule"]
    assert task["log_table"] == "scheduled_task_runs"
```

- [ ] **Step 2: Run red test**

Run: `pytest tests/test_tabcut_selection_schema.py -q`

Expected: fail because migration and registry do not exist.

- [ ] **Step 3: Implement schema and registry**

Add the SQL migration and `TASK_DEFINITIONS["tabcut_daily_selection"]` with runner, schedule, source, and log table.

- [ ] **Step 4: Run green test**

Run: `pytest tests/test_tabcut_selection_schema.py -q`

Expected: pass.

## Task 2: Normalization And Scoring

**Files:**
- Create: `appcore/tabcut_selection/models.py`
- Create: `appcore/tabcut_selection/scoring.py`
- Test: `tests/test_tabcut_selection_scoring.py`

- [ ] **Step 1: Write failing tests**

Tests cover:

```python
from appcore.tabcut_selection.models import normalize_goods_row, normalize_video_row
from appcore.tabcut_selection.scoring import score_candidate


def test_normalize_video_row_strips_signed_video_url():
    row = normalize_video_row({
        "videoId": "v1",
        "videoUrl": "https://cdn.example/v1.mp4?auth_key=secret",
        "videoCoverUrl": "cover",
        "videoDesc": "demo",
        "playCount": 100,
        "itemSoldCount": 8,
        "itemList": [{"itemId": "i1", "itemName": "Item", "soldCount": 20}],
    })
    assert row["video_id"] == "v1"
    assert "video_url" not in row
    assert row["primary_item_id"] == "i1"


def test_normalize_goods_row_extracts_gmv_and_categories():
    row = normalize_goods_row({
        "itemId": "i1",
        "itemName": "Item",
        "categoryLv1Name": "食品饮料",
        "soldCount7d": 12,
        "gmvInfo": {"period7d": {"local": 34.5}, "total": {"local": 99}},
        "relatedVideoCount": 7,
    })
    assert row["item_id"] == "i1"
    assert row["category_l1_name"] == "食品饮料"
    assert row["sold_count_7d"] == 12
    assert row["gmv_7d"] == 34.5


def test_score_candidate_prefers_sales_and_revenue_with_explainable_parts():
    score = score_candidate({
        "play_count": 1_000_000,
        "item_sold_count": 100,
        "video_split_sold_count": 50,
        "goods_sold_count_7d": 1000,
        "goods_gmv_7d": 20000,
        "goods_growth_rate_7d": 0.8,
    })
    assert score["score"] > 0
    assert score["parts"]["goods_gmv_7d"] > 0
```

- [ ] **Step 2: Run red test**

Run: `pytest tests/test_tabcut_selection_scoring.py -q`

Expected: fail because modules do not exist.

- [ ] **Step 3: Implement models and scoring**

Implement pure functions only; no DB and no network.

- [ ] **Step 4: Run green test**

Run: `pytest tests/test_tabcut_selection_scoring.py -q`

Expected: pass.

## Task 3: Store And Filtering Service

**Files:**
- Create: `appcore/tabcut_selection/store.py`
- Create: `appcore/tabcut_selection/service.py`
- Test: `tests/test_tabcut_selection_store.py`

- [ ] **Step 1: Write failing tests**

Tests monkeypatch query/execute and verify:

- filtered video query includes category, min sales, min GMV, and sort whitelist.
- upsert functions call execute with safe parameterized SQL.
- refresh response returns a started payload without touching Tabcut directly.

- [ ] **Step 2: Run red test**

Run: `pytest tests/test_tabcut_selection_store.py -q`

Expected: fail because service files do not exist.

- [ ] **Step 3: Implement store and service**

Use injected `query_fn` / `execute_fn` defaults so tests never connect to local DB.

- [ ] **Step 4: Run green test**

Run: `pytest tests/test_tabcut_selection_store.py -q`

Expected: pass.

## Task 4: TabcutCrawler Client And Runner

**Files:**
- Create: `tools/tabcut_crawler/__init__.py`
- Create: `tools/tabcut_crawler/client.py`
- Create: `tools/tabcut_crawler/runner.py`
- Create: `tools/tabcut_crawler/main.py`
- Test: `tests/test_tabcut_crawler.py`

- [ ] **Step 1: Write failing tests**

Tests cover:

- client enforces minimum interval >= 3 seconds.
- API URL builders generate US video Top500 paging and goods paging.
- runner computes default target date as previous Beijing date.
- runner does not persist signed `auth_key` URLs.

- [ ] **Step 2: Run red test**

Run: `pytest tests/test_tabcut_crawler.py -q`

Expected: fail because crawler package does not exist.

- [ ] **Step 3: Implement crawler**

Use Playwright `connect_over_cdp`; requests execute in browser context via `fetch(..., {credentials: "include"})`; throttle all requests through one method.

- [ ] **Step 4: Run green test**

Run: `pytest tests/test_tabcut_crawler.py -q`

Expected: pass.

## Task 5: Web Routes And Template

**Files:**
- Modify: `web/routes/medias/pages.py`
- Modify: `web/routes/medias/__init__.py`
- Create: `web/routes/medias/tabcut_selection.py`
- Modify: `web/templates/mk_selection.html`
- Create: `web/templates/tabcut_selection.html`
- Test: `tests/test_tabcut_selection_routes.py`

- [ ] **Step 1: Write failing tests**

Tests verify:

- `/medias/tabcut-selection` is login-required and admin-only.
- list APIs delegate to service builders.
- POST refresh requires admin and delegates to service.

- [ ] **Step 2: Run red test**

Run: `pytest tests/test_tabcut_selection_routes.py -q`

Expected: fail because routes do not exist.

- [ ] **Step 3: Implement routes and UI**

Add a dense admin table with filters for category, sales, GMV, and sort. Read CSRF token from `layout.html` meta for refresh POST.

- [ ] **Step 4: Run green test**

Run: `pytest tests/test_tabcut_selection_routes.py -q`

Expected: pass.

## Task 6: Deploy Units And Final Verification

**Files:**
- Create: `deploy/tabcut-daily-selection.service`
- Create: `deploy/tabcut-daily-selection.timer`
- Test: extend `tests/test_tabcut_selection_schema.py`

- [ ] **Step 1: Write failing deploy-unit assertions**

Assert service runs as `cjh`, uses `/opt/autovideosrt`, invokes `python -m tools.tabcut_crawler.main --target-date yesterday`, and timer has `OnCalendar=*-*-* 08:00:00`.

- [ ] **Step 2: Implement deploy units**

Add service/timer files.

- [ ] **Step 3: Run all targeted tests**

Run:

```bash
pytest tests/test_tabcut_selection_schema.py tests/test_tabcut_selection_scoring.py tests/test_tabcut_selection_store.py tests/test_tabcut_crawler.py tests/test_tabcut_selection_routes.py tests/test_appcore_scheduled_tasks.py -q
```

Expected: pass.

- [ ] **Step 4: Commit, merge, deploy**

Commit the feature branch, merge into master, push, update `/opt/autovideosrt`, install timer, restart web service only because the user explicitly requested production deployment, and verify service active + HTTP 200/302.
