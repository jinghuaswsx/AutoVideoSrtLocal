# New Product Launch Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `新品投放分析` analytics tab that reuses realtime dashboard metrics while splitting data into new products, old products, and unmatched ads by fixed product ad-launch date.

**Architecture:** Store product ad-launch dates in a new focused module/table, refresh them from daily Campaign / Ad Set / Ad matches, and pass a launch-scope filter into the existing realtime dashboard backend. The UI should reuse the realtime dashboard structure, with a separate state object and `product_launch_scope` API parameter so the existing realtime dashboard remains unchanged.

**Tech Stack:** Python 3.12, Flask routes/templates, MySQL migrations, pytest, existing `appcore.order_analytics` facade pattern.

---

## File Structure

- Create `db/migrations/2026_05_27_product_ad_launch_dates.sql`
  - Owns the new persistent `product_ad_launch_dates` schema.
- Create `appcore/order_analytics/product_ad_launch.py`
  - Owns launch-date fallback seeding, ad-match refresh, scope classification, and product-id set lookup.
- Modify `appcore/order_analytics/__init__.py`
  - Re-export product launch helpers through the existing facade.
- Modify `tools/meta_daily_final_sync.py`
  - Refresh launch dates for products matched during daily Campaign / Ad Set / Ad imports.
- Modify `appcore/order_analytics/campaign_overrides.py`
  - Refresh launch dates after manual campaign-to-product override applies to history.
- Modify `appcore/order_analytics/realtime.py`
  - Add product launch scope filters to realtime overview, summaries, details, campaigns, and ROAS points.
- Modify `web/routes/order_analytics.py`
  - Validate `product_launch_scope` and include scope metadata in `data_quality`.
- Modify `web/templates/order_analytics.html`
  - Add top-level `新品投放分析` tab and internal `新品分析 / 老品数据 / 未匹配产品` controls.
- Test `tests/test_order_analytics_product_ad_launch_dates.py`
  - Unit tests for launch-date persistence rules.
- Test `tests/test_order_analytics_realtime_product_launch_scope.py`
  - Unit tests for realtime scope filters.
- Modify `tests/test_order_analytics_template_layout.py`
  - Static UI tests for the new tab and request parameter.

---

### Task 1: Migration For Fixed Product Ad-Launch Dates

**Files:**
- Create: `db/migrations/2026_05_27_product_ad_launch_dates.sql`
- Test: `tests/test_order_analytics_product_ad_launch_dates.py`

- [ ] **Step 1: Write the migration-file test**

Add this test file with an initial migration assertion:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_product_ad_launch_dates_migration_defines_required_columns():
    sql = (ROOT / "db" / "migrations" / "2026_05_27_product_ad_launch_dates.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS product_ad_launch_dates" in sql
    assert "product_id INT NOT NULL" in sql
    assert "ad_launch_date DATE NOT NULL" in sql
    assert "source VARCHAR(32) NOT NULL" in sql
    assert "source_level VARCHAR(32) NOT NULL" in sql
    assert "source_table VARCHAR(64) NOT NULL" in sql
    assert "source_row_id BIGINT DEFAULT NULL" in sql
    assert "UNIQUE KEY uk_product_ad_launch_product (product_id)" in sql
    assert "KEY idx_product_ad_launch_date_source (ad_launch_date, source)" in sql
```

- [ ] **Step 2: Run the migration-file test and verify it fails**

Run:

```bash
pytest tests/test_order_analytics_product_ad_launch_dates.py::test_product_ad_launch_dates_migration_defines_required_columns -q
```

Expected: FAIL because `db/migrations/2026_05_27_product_ad_launch_dates.sql` does not exist.

- [ ] **Step 3: Add the migration**

Create `db/migrations/2026_05_27_product_ad_launch_dates.sql`:

```sql
-- Product ad-launch dates for 新品投放分析.
-- Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md

CREATE TABLE IF NOT EXISTS product_ad_launch_dates (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  ad_launch_date DATE NOT NULL COMMENT 'Beijing natural date when product first entered ad data',
  source VARCHAR(32) NOT NULL COMMENT 'ad_match or created_at_fallback',
  source_level VARCHAR(32) NOT NULL COMMENT 'campaign/adset/ad/product_created_at',
  source_table VARCHAR(64) NOT NULL,
  source_row_id BIGINT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_product_ad_launch_product (product_id),
  KEY idx_product_ad_launch_date_source (ad_launch_date, source),
  KEY idx_product_ad_launch_source_updated (source, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Fixed product ad-launch dates for new/old product ad analysis';
```

- [ ] **Step 4: Run the migration-file test and verify it passes**

Run:

```bash
pytest tests/test_order_analytics_product_ad_launch_dates.py::test_product_ad_launch_dates_migration_defines_required_columns -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add db/migrations/2026_05_27_product_ad_launch_dates.sql tests/test_order_analytics_product_ad_launch_dates.py
git commit -m "feat(order-analytics): add product ad launch date schema" -m "Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md#数据模型"
```

---

### Task 2: Product Ad-Launch Date Service

**Files:**
- Create: `appcore/order_analytics/product_ad_launch.py`
- Modify: `appcore/order_analytics/__init__.py`
- Test: `tests/test_order_analytics_product_ad_launch_dates.py`

- [ ] **Step 1: Add service behavior tests**

Append these tests to `tests/test_order_analytics_product_ad_launch_dates.py`:

```python
from datetime import date, datetime

from appcore import order_analytics as oa
from appcore.order_analytics import product_ad_launch as pal


def test_beijing_today_uses_natural_midnight_not_meta_business_day():
    assert pal.beijing_today(datetime(2026, 5, 27, 1, 30)) == date(2026, 5, 27)


def test_launch_scope_cutoff_classifies_last_7_calendar_days():
    today = date(2026, 5, 27)

    assert pal.classify_launch_date(date(2026, 5, 20), today=today) == "new"
    assert pal.classify_launch_date(date(2026, 5, 19), today=today) == "old"


def test_seed_missing_fallback_rows_uses_media_product_created_at(monkeypatch):
    executed: list[tuple[str, tuple]] = []

    monkeypatch.setattr(oa, "execute", lambda sql, args=(): executed.append((sql, args)) or 3)

    inserted = pal.seed_missing_fallback_launch_dates()

    assert inserted == 3
    assert executed
    sql, args = executed[0]
    assert "INSERT INTO product_ad_launch_dates" in sql
    assert "FROM media_products p" in sql
    assert "DATE(COALESCE(p.created_at, NOW()))" in sql
    assert "created_at_fallback" in args
    assert "product_created_at" in args


def test_refresh_ad_match_launch_dates_keeps_existing_ad_match_locked(monkeypatch):
    queries: list[tuple[str, tuple]] = []
    executed: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        queries.append((sql, args))
        if "FROM (" in sql and "meta_ad_daily_campaign_metrics" in sql:
            return [
                {
                    "product_id": 101,
                    "ad_launch_date": date(2026, 5, 21),
                    "source_level": "campaign",
                    "source_table": "meta_ad_daily_campaign_metrics",
                    "source_row_id": 11,
                }
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    result = pal.refresh_ad_match_launch_dates_for_products([101])

    assert result["matched_products"] == 1
    assert executed
    sql, args = executed[0]
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "product_ad_launch_dates.source = 'created_at_fallback'" in sql
    assert args[0] == 101
    assert args[1] == date(2026, 5, 21)
    assert args[2] == "ad_match"


def test_get_product_ids_for_launch_scope_seeds_fallback_and_queries_cutoff(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(pal, "seed_missing_fallback_launch_dates", lambda: calls.append("seed") or 0)
    monkeypatch.setattr(
        oa,
        "query",
        lambda sql, args=(): calls.append(sql) or [{"product_id": 101}, {"product_id": 102}],
    )

    ids = pal.get_product_ids_for_launch_scope("new", today=date(2026, 5, 27))

    assert calls[0] == "seed"
    assert ids == (101, 102)
    assert "ad_launch_date >= %s" in calls[1]
```

- [ ] **Step 2: Run service tests and verify they fail**

Run:

```bash
pytest tests/test_order_analytics_product_ad_launch_dates.py -q
```

Expected: FAIL because `appcore/order_analytics/product_ad_launch.py` does not exist and helpers are not exported.

- [ ] **Step 3: Create the service module**

Create `appcore/order_analytics/product_ad_launch.py`:

```python
"""Product ad-launch date helpers for 新品投放分析.

Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ._constants import META_ATTRIBUTION_TIMEZONE

NEW_PRODUCT_WINDOW_DAYS = 7
VALID_PRODUCT_LAUNCH_SCOPES = frozenset({"new", "old", "unmatched"})
AD_MATCH_SOURCE = "ad_match"
FALLBACK_SOURCE = "created_at_fallback"


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def beijing_today(now: datetime | None = None) -> date:
    value = now or datetime.now(ZoneInfo(META_ATTRIBUTION_TIMEZONE))
    if value.tzinfo is not None:
        value = value.astimezone(ZoneInfo(META_ATTRIBUTION_TIMEZONE)).replace(tzinfo=None)
    return value.date()


def launch_cutoff(today: date | None = None) -> date:
    return (today or beijing_today()) - timedelta(days=NEW_PRODUCT_WINDOW_DAYS)


def classify_launch_date(ad_launch_date: date, *, today: date | None = None) -> str:
    return "new" if ad_launch_date >= launch_cutoff(today) else "old"


def normalize_product_launch_scope(value: Any) -> str | None:
    scope = str(value or "").strip().lower()
    if not scope:
        return None
    if scope not in VALID_PRODUCT_LAUNCH_SCOPES:
        raise ValueError("product_launch_scope must be one of new/old/unmatched")
    return scope


def seed_missing_fallback_launch_dates() -> int:
    return int(execute(
        "INSERT INTO product_ad_launch_dates "
        "(product_id, ad_launch_date, source, source_level, source_table) "
        "SELECT p.id, DATE(COALESCE(p.created_at, NOW())), %s, %s, %s "
        "FROM media_products p "
        "LEFT JOIN product_ad_launch_dates l ON l.product_id = p.id "
        "WHERE p.deleted_at IS NULL AND l.product_id IS NULL",
        (FALLBACK_SOURCE, "product_created_at", "media_products"),
    ) or 0)


def _earliest_ad_matches_for_products(product_ids: tuple[int, ...]) -> list[dict[str, Any]]:
    if not product_ids:
        return []
    placeholders = ", ".join(["%s"] * len(product_ids))
    rows = query(
        "SELECT product_id, ad_launch_date, source_level, source_table, source_row_id "
        "FROM ("
        "  SELECT product_id, COALESCE(meta_business_date, report_date) AS ad_launch_date, "
        "         'campaign' AS source_level, 'meta_ad_daily_campaign_metrics' AS source_table, MIN(id) AS source_row_id "
        "  FROM meta_ad_daily_campaign_metrics "
        f"  WHERE product_id IN ({placeholders}) AND product_id IS NOT NULL "
        "  GROUP BY product_id, COALESCE(meta_business_date, report_date) "
        "  UNION ALL "
        "  SELECT product_id, COALESCE(meta_business_date, report_date) AS ad_launch_date, "
        "         'adset' AS source_level, 'meta_ad_daily_adset_metrics' AS source_table, MIN(id) AS source_row_id "
        "  FROM meta_ad_daily_adset_metrics "
        f"  WHERE product_id IN ({placeholders}) AND product_id IS NOT NULL "
        "  GROUP BY product_id, COALESCE(meta_business_date, report_date) "
        "  UNION ALL "
        "  SELECT product_id, COALESCE(meta_business_date, report_date) AS ad_launch_date, "
        "         'ad' AS source_level, 'meta_ad_daily_ad_metrics' AS source_table, MIN(id) AS source_row_id "
        "  FROM meta_ad_daily_ad_metrics "
        f"  WHERE product_id IN ({placeholders}) AND product_id IS NOT NULL "
        "  GROUP BY product_id, COALESCE(meta_business_date, report_date) "
        ") matches "
        "WHERE ad_launch_date IS NOT NULL "
        "ORDER BY product_id, ad_launch_date, FIELD(source_level, 'campaign', 'adset', 'ad')",
        tuple(product_ids + product_ids + product_ids),
    ) or []
    earliest: dict[int, dict[str, Any]] = {}
    for row in rows:
        pid = int(row["product_id"])
        if pid not in earliest:
            earliest[pid] = dict(row)
    return list(earliest.values())


def refresh_ad_match_launch_dates_for_products(product_ids: list[int] | tuple[int, ...]) -> dict[str, int]:
    normalized = tuple(sorted({int(pid) for pid in product_ids if int(pid) > 0}))
    rows = _earliest_ad_matches_for_products(normalized)
    updated = 0
    for row in rows:
        updated += int(execute(
            "INSERT INTO product_ad_launch_dates "
            "(product_id, ad_launch_date, source, source_level, source_table, source_row_id) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "ad_launch_date = IF(product_ad_launch_dates.source = 'created_at_fallback', VALUES(ad_launch_date), product_ad_launch_dates.ad_launch_date), "
            "source = IF(product_ad_launch_dates.source = 'created_at_fallback', VALUES(source), product_ad_launch_dates.source), "
            "source_level = IF(product_ad_launch_dates.source = 'created_at_fallback', VALUES(source_level), product_ad_launch_dates.source_level), "
            "source_table = IF(product_ad_launch_dates.source = 'created_at_fallback', VALUES(source_table), product_ad_launch_dates.source_table), "
            "source_row_id = IF(product_ad_launch_dates.source = 'created_at_fallback', VALUES(source_row_id), product_ad_launch_dates.source_row_id)",
            (
                int(row["product_id"]),
                row["ad_launch_date"],
                AD_MATCH_SOURCE,
                row["source_level"],
                row["source_table"],
                row["source_row_id"],
            ),
        ) or 0)
    return {"matched_products": len(rows), "updated_rows": updated}


def backfill_product_ad_launch_dates() -> dict[str, int]:
    seeded = seed_missing_fallback_launch_dates()
    rows = query(
        "SELECT id FROM media_products WHERE deleted_at IS NULL",
        (),
    ) or []
    product_ids = [int(row["id"]) for row in rows if row.get("id")]
    refreshed = refresh_ad_match_launch_dates_for_products(product_ids)
    return {
        "fallback_inserted": seeded,
        "matched_products": refreshed["matched_products"],
        "updated_rows": refreshed["updated_rows"],
    }


def get_product_ids_for_launch_scope(scope: str, *, today: date | None = None) -> tuple[int, ...]:
    normalized = normalize_product_launch_scope(scope)
    if normalized == "unmatched":
        return ()
    if normalized not in {"new", "old"}:
        raise ValueError("product_launch_scope must be one of new/old/unmatched")
    seed_missing_fallback_launch_dates()
    cutoff = launch_cutoff(today)
    op = ">=" if normalized == "new" else "<"
    rows = query(
        f"SELECT product_id FROM product_ad_launch_dates WHERE ad_launch_date {op} %s ORDER BY product_id",
        (cutoff,),
    ) or []
    return tuple(int(row["product_id"]) for row in rows if row.get("product_id") is not None)
```

- [ ] **Step 4: Re-export service helpers**

Modify `appcore/order_analytics/__init__.py` imports:

```python
from .product_ad_launch import (
    AD_MATCH_SOURCE,
    FALLBACK_SOURCE,
    VALID_PRODUCT_LAUNCH_SCOPES,
    backfill_product_ad_launch_dates,
    beijing_today,
    classify_launch_date,
    get_product_ids_for_launch_scope,
    normalize_product_launch_scope,
    refresh_ad_match_launch_dates_for_products,
    seed_missing_fallback_launch_dates,
)
```

- [ ] **Step 5: Run service tests and verify they pass**

Run:

```bash
pytest tests/test_order_analytics_product_ad_launch_dates.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add appcore/order_analytics/product_ad_launch.py appcore/order_analytics/__init__.py tests/test_order_analytics_product_ad_launch_dates.py
git commit -m "feat(order-analytics): track product ad launch dates" -m "Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md#回填与同步更新"
```

---

### Task 3: Refresh Launch Dates After Ad Matching

**Files:**
- Modify: `tools/meta_daily_final_sync.py`
- Modify: `appcore/order_analytics/campaign_overrides.py`
- Test: `tests/test_order_analytics_product_ad_launch_dates.py`

- [ ] **Step 1: Add sync-hook tests**

Append these tests:

```python
def test_daily_sync_refreshes_launch_dates_for_matched_products(monkeypatch):
    from tools import meta_daily_final_sync as sync

    refreshed: list[list[int]] = []
    monkeypatch.setattr(sync.oa, "refresh_ad_match_launch_dates_for_products", lambda ids: refreshed.append(list(ids)) or {"matched_products": len(ids), "updated_rows": len(ids)})

    summary = sync._refresh_product_ad_launch_dates({101, 102, 101})

    assert refreshed == [[101, 102]]
    assert summary == {"matched_products": 2, "updated_rows": 2}


def test_manual_campaign_override_refreshes_launch_date(monkeypatch):
    from appcore.order_analytics import campaign_overrides

    refreshed: list[list[int]] = []
    monkeypatch.setattr(campaign_overrides, "query_one", lambda sql, args=(): {"id": 101, "product_code": "abc", "name": "ABC"})
    monkeypatch.setattr(campaign_overrides, "execute", lambda sql, args=(): 1)
    monkeypatch.setattr(campaign_overrides, "apply_override_to_history", lambda **kwargs: {"matched_periodic": 0, "matched_daily": 3})
    monkeypatch.setattr(oa, "refresh_ad_match_launch_dates_for_products", lambda ids: refreshed.append(list(ids)) or {"matched_products": len(ids), "updated_rows": len(ids)})

    result = campaign_overrides.create_override(
        normalized_campaign_code="abc",
        product_id=101,
        reason="manual match",
        created_by="test",
    )

    assert result["product_id"] == 101
    assert refreshed == [[101]]
```

- [ ] **Step 2: Run sync-hook tests and verify they fail**

Run:

```bash
pytest tests/test_order_analytics_product_ad_launch_dates.py::test_daily_sync_refreshes_launch_dates_for_matched_products tests/test_order_analytics_product_ad_launch_dates.py::test_manual_campaign_override_refreshes_launch_date -q
```

Expected: FAIL because `_refresh_product_ad_launch_dates` and the manual override hook are not present.

- [ ] **Step 3: Add daily sync refresh helper**

In `tools/meta_daily_final_sync.py`, add near `_finish_batch`:

```python
def _refresh_product_ad_launch_dates(product_ids: set[int]) -> dict[str, int]:
    normalized = sorted({int(pid) for pid in product_ids if int(pid) > 0})
    if not normalized:
        return {"matched_products": 0, "updated_rows": 0}
    return oa.refresh_ad_match_launch_dates_for_products(normalized)
```

- [ ] **Step 4: Call helper from Campaign / Ad / Ad Set replace functions**

In each of these functions:

- `_replace_campaign_daily_rows`
- `_replace_ad_daily_rows`
- `_replace_adset_daily_rows`
- `_replace_campaign_daily_rows_from_api`
- `_replace_ad_daily_rows_from_api`
- `_replace_adset_daily_rows_from_api`

Add `matched_product_ids: set[int] = set()` before the loop, add `matched_product_ids.add(int(product_id))` inside `if product_id:`, and include this after `_finish_batch(...)`:

```python
    launch_refresh = _refresh_product_ad_launch_dates(matched_product_ids)
    return {
        "batch_id": batch_id,
        "rows": imported,
        "matched": matched,
        "spend_usd": spend_total,
        "ad_launch_refresh": launch_refresh,
    }
```

Keep existing return keys unchanged and only append `ad_launch_refresh`.

- [ ] **Step 5: Refresh after manual campaign override**

In `appcore/order_analytics/campaign_overrides.py::create_override`, after `applied = apply_override_to_history(...)`, add:

```python
    try:
        _facade().refresh_ad_match_launch_dates_for_products([pid])
    except Exception:
        pass
```

This hook must not block manual pairing if launch-date refresh has a transient failure.

- [ ] **Step 6: Run sync-hook tests and verify they pass**

Run:

```bash
pytest tests/test_order_analytics_product_ad_launch_dates.py::test_daily_sync_refreshes_launch_dates_for_matched_products tests/test_order_analytics_product_ad_launch_dates.py::test_manual_campaign_override_refreshes_launch_date -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add tools/meta_daily_final_sync.py appcore/order_analytics/campaign_overrides.py tests/test_order_analytics_product_ad_launch_dates.py
git commit -m "feat(order-analytics): refresh ad launch dates after ad matching" -m "Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md#后续同步更新"
```

---

### Task 4: Backend Realtime Scope Filtering

**Files:**
- Modify: `appcore/order_analytics/realtime.py`
- Test: `tests/test_order_analytics_realtime_product_launch_scope.py`

- [ ] **Step 1: Write failing realtime scope tests**

Create `tests/test_order_analytics_realtime_product_launch_scope.py`:

```python
from datetime import date, datetime

from appcore import order_analytics as oa
from appcore.order_analytics import realtime as realtime_oa


def test_new_launch_scope_limits_order_and_daily_ad_queries(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    monkeypatch.setattr(oa, "get_product_ids_for_launch_scope", lambda scope: (101, 102))
    monkeypatch.setattr(oa, "query", lambda sql, args=(): calls.append((sql, args)) or [])

    result = oa.get_realtime_roas_overview(
        "2026-05-09",
        now=datetime(2026, 5, 10, 12, 0),
        product_launch_scope="new",
    )

    assert result["scope"]["product_launch_scope"] == "new"
    assert result["scope"]["product_launch_product_count"] == 2
    assert any("d.product_id IN" in sql and 101 in args and 102 in args for sql, args in calls)
    assert any("product_id IN" in sql and 101 in args and 102 in args for sql, args in calls)


def test_unmatched_launch_scope_returns_empty_order_side_and_unmatched_ads(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if "FROM meta_ad_daily_campaign_metrics" in sql and "SUM(spend_usd)" in sql:
            return [{
                "ad_spend": 25.5,
                "meta_purchase_value": 0,
                "meta_purchases": 0,
                "last_ad_updated_at": datetime(2026, 5, 10, 16, 30),
            }]
        if "FROM meta_ad_daily_campaign_metrics" in sql and "campaign_name" in sql:
            return [{
                "ad_account_id": "act_1",
                "ad_account_name": "Meta",
                "campaign_name": "unmatched-campaign",
                "normalized_campaign_code": "unmatched-campaign",
                "result_count": 0,
                "spend": 25.5,
                "purchase_value": 0,
            }]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-05-09",
        now=datetime(2026, 5, 10, 12, 0),
        include_details=True,
        product_launch_scope="unmatched",
    )

    assert result["scope"]["product_launch_scope"] == "unmatched"
    assert result["summary"]["order_count"] == 0
    assert result["summary"]["ad_spend"] == 25.5
    assert result["campaigns"]
    assert any("product_id IS NULL" in sql for sql, _ in calls)


def test_empty_new_scope_does_not_fall_back_to_all_products(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(oa, "get_product_ids_for_launch_scope", lambda scope: ())
    monkeypatch.setattr(oa, "query", lambda sql, args=(): calls.append(sql) or [])

    result = oa.get_realtime_roas_overview(
        "2026-05-09",
        now=datetime(2026, 5, 10, 12, 0),
        product_launch_scope="new",
    )

    assert result["scope"]["product_launch_scope"] == "new"
    assert result["scope"]["product_launch_product_count"] == 0
    assert result["summary"]["order_count"] == 0
    assert any("1=0" in sql for sql in calls)
```

- [ ] **Step 2: Run realtime scope tests and verify they fail**

Run:

```bash
pytest tests/test_order_analytics_realtime_product_launch_scope.py -q
```

Expected: FAIL because `product_launch_scope` is not accepted by `get_realtime_roas_overview`.

- [ ] **Step 3: Add scope normalization and SQL helpers**

In `appcore/order_analytics/realtime.py`, import helper names:

```python
from .product_ad_launch import normalize_product_launch_scope
```

Replace `_product_filter_sql` with this signature and logic:

```python
def _product_filter_sql(
    column: str,
    product_id: int | None,
    *,
    product_ids: tuple[int, ...] | None = None,
    unmatched: bool = False,
    empty_matches_none: bool = False,
) -> tuple[str, list[Any]]:
    if unmatched:
        return f"AND {column} IS NULL ", []
    if product_id:
        if product_ids is not None and int(product_id) not in set(product_ids):
            return "AND 1=0 ", []
        return f"AND {column} = %s ", [int(product_id)]
    if product_ids is not None:
        if not product_ids:
            return "AND 1=0 ", []
        placeholders = ", ".join(["%s"] * len(product_ids))
        return f"AND {column} IN ({placeholders}) ", list(product_ids)
    if empty_matches_none:
        return "AND 1=0 ", []
    return "", []
```

- [ ] **Step 4: Derive launch scope in `get_realtime_roas_overview`**

Add parameter:

```python
    product_launch_scope: str | None = None,
```

After site normalization, add:

```python
    normalized_launch_scope = normalize_product_launch_scope(product_launch_scope)
    launch_product_ids: tuple[int, ...] | None = None
    launch_scope_unmatched = normalized_launch_scope == "unmatched"
    if normalized_launch_scope in {"new", "old"}:
        launch_product_ids = _facade().get_product_ids_for_launch_scope(normalized_launch_scope)
```

- [ ] **Step 5: Pass scope filters through order and ad helpers**

Add optional parameters to the helper functions that currently call `_product_filter_sql`:

```python
product_ids: tuple[int, ...] | None = None,
unmatched_ads: bool = False,
```

Use these rules:

- Order-side functions pass `product_ids=launch_product_ids`, `unmatched_ads=False`.
- If `launch_scope_unmatched` is true on an order-side function, return empty rows or zero counts before querying.
- Daily ad functions pass `unmatched=launch_scope_unmatched` and `product_ids=launch_product_ids`.
- Campaign detail functions filter daily `product_id IS NULL` for unmatched scope.
- Realtime campaign filtering must use `resolve_ad_product_match` for `new` / `old` and include rows with no match only for `unmatched`.

Use this function for realtime campaign rows before formatting:

```python
def _filter_realtime_campaign_rows_for_launch_scope(
    rows: list[dict[str, Any]],
    *,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
) -> list[dict[str, Any]]:
    if product_ids is None and not unmatched_ads:
        return rows
    allowed = set(product_ids or ())
    cache: dict[str, int | None] = {}
    filtered: list[dict[str, Any]] = []
    for row in rows:
        code = _campaign_code(row)
        if not code:
            if unmatched_ads:
                filtered.append(row)
            continue
        if code not in cache:
            match = resolve_ad_product_match(code)
            cache[code] = int(match["id"]) if match and match.get("id") is not None else None
        matched_pid = cache[code]
        if unmatched_ads and matched_pid is None:
            filtered.append(row)
        elif product_ids is not None and matched_pid in allowed:
            filtered.append(row)
    return filtered
```

- [ ] **Step 6: Add scope metadata to returned `scope` blocks**

Every return in `get_realtime_roas_overview` and `_build_realtime_overview_for_range` must include:

```python
"product_launch_scope": normalized_launch_scope,
"product_launch_product_count": len(launch_product_ids or ()),
```

For no launch scope, values should be `None` and `0`.

- [ ] **Step 7: Prevent scoped ROAS trend from using full dashboard nodes**

In the ROAS node query branch, treat any `normalized_launch_scope` as node-ineligible:

```python
if site_filter_active or normalized_launch_scope:
    roas_node_rows = []
else:
    roas_node_rows = query(...)
```

Add a scoped point builder for `normalized_launch_scope` instead of returning the full dashboard nodes:

```python
def _build_scoped_roas_points(
    *,
    target: date,
    day_start: datetime,
    data_until: datetime,
    orders_by_hour: dict[int, dict[str, Any]],
    product_ids: tuple[int, ...] | None,
    unmatched_ads: bool,
    site_codes: tuple[str, ...],
) -> list[dict[str, Any]]:
    ...
```

Implementation requirements:

- Keep the existing 24-point response schema.
- Use cumulative scoped order revenue/shipping/units from `orders_by_hour` up to each hour.
- For each eligible node hour, read the latest `meta_ad_realtime_daily_campaign_metrics` snapshot at or before that node time, filter campaign rows through `_filter_realtime_campaign_rows_for_launch_scope`, then sum scoped spend and purchase value.
- For `new` / `old`, compute `true_roas` from scoped cumulative revenue with shipping divided by scoped spend.
- For `unmatched`, leave `true_roas=None` because there is no product-scoped order revenue, but still populate scoped ad spend when unmatched realtime campaign rows exist.
- Never query `roi_daily_roas_nodes` when `product_launch_scope` is present.
- If no scoped realtime snapshots exist for a historical day or date range, return the 24-point schema with `true_roas=None` and zero scoped spend rather than falling back to full-dashboard nodes.

Add one assertion to the scoped realtime tests that `product_launch_scope="new"` does not query `roi_daily_roas_nodes` and that at least one returned point has scoped `ad_spend` from a filtered realtime campaign snapshot when the mocked data supplies it.

- [ ] **Step 8: Run realtime scope tests**

Run:

```bash
pytest tests/test_order_analytics_realtime_product_launch_scope.py -q
```

Expected: PASS.

- [ ] **Step 9: Run existing realtime site filter tests**

Run:

```bash
pytest tests/test_order_analytics_realtime_site_filter.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit Task 4**

```bash
git add appcore/order_analytics/realtime.py tests/test_order_analytics_realtime_product_launch_scope.py
git commit -m "feat(order-analytics): filter realtime overview by product launch scope" -m "Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md#api-设计"
```

---

### Task 5: Route Validation And Data Quality Metadata

**Files:**
- Modify: `web/routes/order_analytics.py`
- Test: `tests/test_order_analytics_realtime_product_launch_scope.py`

- [ ] **Step 1: Add route tests**

Append:

```python
def test_route_rejects_invalid_product_launch_scope(authed_client_no_db):
    response = authed_client_no_db.get(
        "/order-analytics/realtime-overview?product_launch_scope=maybe"
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_param"


def test_route_passes_product_launch_scope_to_overview(authed_client_no_db, monkeypatch):
    captured: dict = {}

    def fake_overview(date_text, **kwargs):
        captured.update(kwargs)
        return {
            "period": {"date": date(2026, 5, 9)},
            "scope": {"product_launch_scope": kwargs.get("product_launch_scope"), "ad_source": "meta_ad_daily_campaign_metrics"},
            "summary": {},
            "freshness": {},
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_realtime_roas_overview", fake_overview)
    monkeypatch.setattr("web.routes.order_analytics._attach_realtime_data_quality", lambda result: result)

    response = authed_client_no_db.get(
        "/order-analytics/realtime-overview?product_launch_scope=old"
    )

    assert response.status_code == 200
    assert captured["product_launch_scope"] == "old"
```

- [ ] **Step 2: Run route tests and verify they fail**

Run:

```bash
pytest tests/test_order_analytics_realtime_product_launch_scope.py::test_route_rejects_invalid_product_launch_scope tests/test_order_analytics_realtime_product_launch_scope.py::test_route_passes_product_launch_scope_to_overview -q
```

Expected: FAIL because the route does not parse `product_launch_scope`.

- [ ] **Step 3: Validate and pass route parameter**

In `web/routes/order_analytics.py::realtime_overview`, after site-code handling:

```python
    product_launch_scope = (request.args.get("product_launch_scope") or "").strip().lower()
    if product_launch_scope:
        if product_launch_scope not in ("new", "old", "unmatched"):
            return _json_response(
                error="invalid_param",
                detail="product_launch_scope must be one of new, old, unmatched",
            ), 400
        kwargs["product_launch_scope"] = product_launch_scope
```

- [ ] **Step 4: Add data quality scope annotation**

In `_attach_realtime_data_quality`, after `result["data_quality"] = ...`, add:

```python
        launch_scope = scope.get("product_launch_scope")
        if launch_scope:
            result["data_quality"]["product_launch_scope"] = launch_scope
            result["data_quality"].setdefault("checks", []).append({
                "code": "product_launch_scope",
                "status": dq.STATUS_WARNING if launch_scope == "unmatched" else dq.STATUS_OK,
                "message": (
                    "未匹配产品广告无法归因到订单，订单与利润指标为空"
                    if launch_scope == "unmatched"
                    else "已按产品上广告时间限定产品范围"
                ),
                "product_count": scope.get("product_launch_product_count", 0),
            })
            if launch_scope == "unmatched" and result["data_quality"].get("status") == dq.STATUS_OK:
                result["data_quality"]["status"] = dq.STATUS_WARNING
```

- [ ] **Step 5: Run route tests**

Run:

```bash
pytest tests/test_order_analytics_realtime_product_launch_scope.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

```bash
git add web/routes/order_analytics.py tests/test_order_analytics_realtime_product_launch_scope.py
git commit -m "feat(order-analytics): expose product launch scope in realtime route" -m "Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md#数据质量"
```

---

### Task 6: New Product Launch Analysis UI

**Files:**
- Modify: `web/templates/order_analytics.html`
- Modify: `tests/test_order_analytics_template_layout.py`

- [ ] **Step 1: Add static template tests**

Append:

```python
def test_new_product_launch_analysis_tab_is_next_to_realtime():
    template = _template_source()
    first_tabs = template[: template.index("{% endblock %}")]

    assert 'data-tab="realtime"' in first_tabs
    assert 'data-tab="newProductLaunch"' in first_tabs
    assert first_tabs.index('data-tab="realtime"') < first_tabs.index('data-tab="newProductLaunch"')
    assert "新品投放分析" in first_tabs


def test_new_product_launch_panel_has_three_scope_tabs_and_request_param():
    template = _template_source()

    assert 'id="panelNewProductLaunch"' in template
    assert 'data-new-product-scope="new"' in template
    assert 'data-new-product-scope="old"' in template
    assert 'data-new-product-scope="unmatched"' in template
    assert "新品分析" in template
    assert "老品数据" in template
    assert "未匹配产品" in template
    assert "product_launch_scope" in template
    assert "loadNewProductLaunchOverview" in template
```

- [ ] **Step 2: Run template tests and verify they fail**

Run:

```bash
pytest tests/test_order_analytics_template_layout.py::test_new_product_launch_analysis_tab_is_next_to_realtime tests/test_order_analytics_template_layout.py::test_new_product_launch_panel_has_three_scope_tabs_and_request_param -q
```

Expected: FAIL because the new tab and panel do not exist.

- [ ] **Step 3: Add top-level tabs**

Add this button after each existing `实时大盘` tab in both desktop and mobile tab lists:

```html
<button type="button" class="oa-tab" data-tab="newProductLaunch" role="tab" aria-selected="false">新品投放分析</button>
```

- [ ] **Step 4: Add the new panel by reusing realtime markup IDs with an `npl` prefix**

Create `<section class="oa-panel" id="panelNewProductLaunch">` after `panelRealtime`.

Use the same toolbar/card/subtable structure as realtime, but prefix ids:

- `nplStartDate`, `nplEndDate`, `nplRefresh`
- `nplSiteFilter`
- KPI ids: `nplRevenue`, `nplSpend`, `nplRoas`, `nplMetaRoas`, `nplProfit`
- Subtab buttons with `data-new-product-scope="new|old|unmatched"`
- Table bodies: `nplOrderBody`, `nplOrderProfitBody`, `nplProductSalesBody`, `nplCampaignBody`
- ROAS chart: `nplRoasChart`

Keep the same table columns as realtime. For the first implementation, copy the realtime panel structure and replace ids consistently; do not nest cards inside cards.

- [ ] **Step 5: Add JS state and loader**

Near `realtimeState`, add:

```javascript
var newProductLaunchState = {
  range: 'today',
  scope: 'new',
  siteCode: '',
  orderPage: 1,
  orderPageSize: 30,
  profitPage: 1,
  profitPageSize: 30
};
```

Add a loader that mirrors realtime top/subtab requests:

```javascript
function loadNewProductLaunchOverview() {
  var range = {
    start: (document.getElementById('nplStartDate') || {}).value || '',
    end: (document.getElementById('nplEndDate') || {}).value || ''
  };
  if (!range.start || !range.end || range.end < range.start) return;
  var params = new URLSearchParams();
  params.set('start_date', range.start);
  params.set('end_date', range.end);
  params.set('include_details', '1');
  params.set('include_profit_summary', '1');
  params.set('product_launch_scope', newProductLaunchState.scope || 'new');
  if (newProductLaunchState.siteCode) params.set('site_code', newProductLaunchState.siteCode);
  fetch('/order-analytics/realtime-overview?' + params.toString())
    .then(function(r) {
      if (!r.ok) return r.json().then(function(data) { throw new Error(data.detail || data.error || '查询失败'); });
      return r.json();
    })
    .then(function(data) {
      renderNewProductLaunchOverview(data);
    })
    .catch(function(err) {
      var note = document.getElementById('nplRangeNote');
      if (note) note.textContent = '加载失败：' + err.message;
    });
}
```

Implement `renderNewProductLaunchOverview(data)` by calling the same formatting helpers used by realtime and rendering the `npl*` ids. For tables, create wrapper render functions that call the existing row renderers after temporarily targeting the `npl*` body ids, or copy the small table render loops with `npl` ids to keep the first version explicit.

- [ ] **Step 6: Wire tab switching**

In the existing top-level tab click handler, add:

```javascript
      } else if (tab.dataset.tab === 'newProductLaunch') {
        initNewProductLaunch();
```

Add:

```javascript
var newProductLaunchInitialized = false;
function initNewProductLaunch() {
  if (newProductLaunchInitialized) {
    loadNewProductLaunchOverview();
    return;
  }
  newProductLaunchInitialized = true;
  setInputValue('nplStartDate', (document.getElementById('realtimeStartDate') || {}).value || '');
  setInputValue('nplEndDate', (document.getElementById('realtimeEndDate') || {}).value || '');
  document.querySelectorAll('[data-new-product-scope]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      newProductLaunchState.scope = btn.dataset.newProductScope || 'new';
      document.querySelectorAll('[data-new-product-scope]').forEach(function(item) {
        item.classList.toggle('is-active', item === btn);
      });
      loadNewProductLaunchOverview();
    });
  });
  var refresh = document.getElementById('nplRefresh');
  if (refresh) refresh.addEventListener('click', loadNewProductLaunchOverview);
  loadNewProductLaunchOverview();
}
```

- [ ] **Step 7: Run template tests**

Run:

```bash
pytest tests/test_order_analytics_template_layout.py::test_new_product_launch_analysis_tab_is_next_to_realtime tests/test_order_analytics_template_layout.py::test_new_product_launch_panel_has_three_scope_tabs_and_request_param -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 6**

```bash
git add web/templates/order_analytics.html tests/test_order_analytics_template_layout.py
git commit -m "feat(order-analytics): add new product launch analysis tab" -m "Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md#ui-设计"
```

---

### Task 7: Verification And Regression

**Files:**
- Verify only unless failures require scoped fixes.

- [ ] **Step 1: Run focused product launch tests**

Run:

```bash
pytest tests/test_order_analytics_product_ad_launch_dates.py tests/test_order_analytics_realtime_product_launch_scope.py -q
```

Expected: PASS.

- [ ] **Step 2: Run realtime and data quality regressions**

Run:

```bash
pytest tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_data_quality.py \
       tests/test_order_analytics_template_layout.py \
       tests/characterization/test_order_analytics_baseline.py -q
```

Expected: PASS.

- [ ] **Step 3: Run formatting check**

Run:

```bash
git diff --check HEAD~6..HEAD
```

Expected: no output, exit 0.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git status --short
git log --oneline -7
```

Expected: clean worktree except intentional uncommitted fixes if a previous step failed; recent commits include the spec commit and Tasks 1-6.

- [ ] **Step 5: Final implementation commit if verification fixes were needed**

If Step 1 or Step 2 required small fixes after Task 6, commit only those fixes:

```bash
git add appcore/order_analytics/realtime.py web/routes/order_analytics.py web/templates/order_analytics.html tests/test_order_analytics_product_ad_launch_dates.py tests/test_order_analytics_realtime_product_launch_scope.py tests/test_order_analytics_template_layout.py
git commit -m "fix(order-analytics): stabilize product launch analysis regressions" -m "Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md#验收标准"
```

---

## Self-Review Notes

- Spec coverage: Tasks 1-3 cover the fixed上广告时间 table, fallback, backfill, and ad-match refresh. Tasks 4-5 cover API scope and data quality. Task 6 covers the top-level tab, three child scopes, and realtime-dashboard dimensions. Task 7 covers verification.
- The plan deliberately keeps product launch logic in `product_ad_launch.py` so `realtime.py` only receives product-id filters and scope metadata.
- The plan keeps unmatched ads out of new/old scopes and exposes them only through `product_launch_scope=unmatched`.
- The plan prevents scoped ROAS trend from using full `roi_daily_roas_nodes`; Task 4 requires scoped 24-point aggregation from realtime campaign snapshots when available and an explicit empty scoped series when hourly source data is unavailable.
