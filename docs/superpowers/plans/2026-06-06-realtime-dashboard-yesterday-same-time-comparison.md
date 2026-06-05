# Realtime Dashboard Yesterday Same-Time Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add yesterday-same-business-progress percentage changes for only the current-day global realtime dashboard cards: total sales, order count, and profit.

**Architecture:** The backend owns comparison semantics and returns a stable `comparison.yesterday_same_time` object from `/order-analytics/realtime-overview`. Current values come from the already-built response; previous values are recalculated at the previous Meta business day's matching `data_until_at` waterline. Frontend rendering is limited to the global realtime card DOM ids and hides comparison rows whenever `enabled=false`.

**Tech Stack:** Python 3.12, Flask route layer, `appcore.order_analytics.realtime`, MySQL SQL strings through existing `query()` facade, Jinja template with inline JavaScript, pytest.

---

## File Structure

- Modify `appcore/order_analytics/realtime.py`
  - Import `_compute_pct_change`.
  - Add comparison response helpers near existing realtime summary helpers.
  - Add waterline-capped realtime ad-cost adjustment helper local to realtime dashboard code.
  - Attach `comparison.yesterday_same_time` to all realtime overview response branches.
- Create `tests/test_order_analytics_realtime_yesterday_comparison.py`
  - Backend unit tests for comparison enable/disable rules, waterline calculation, percent semantics, and waterline-capped profit adjustment.
- Modify `web/templates/order_analytics.html`
  - Add three global-card sub nodes.
  - Add JS formatting/rendering for `comparison.yesterday_same_time`.
- Modify `tests/test_order_analytics_template_layout.py`
  - Template tests for DOM ids and JS behavior.
- No database migration.

Docs anchor for code changes:

`docs/superpowers/specs/2026-06-06-realtime-dashboard-yesterday-same-time-comparison-design.md`

---

### Task 1: Backend Failing Tests

**Files:**
- Create: `tests/test_order_analytics_realtime_yesterday_comparison.py`
- Reference: `docs/superpowers/specs/2026-06-06-realtime-dashboard-yesterday-same-time-comparison-design.md`

- [ ] **Step 1: Create backend tests**

Create `tests/test_order_analytics_realtime_yesterday_comparison.py` with this content:

```python
from __future__ import annotations

from datetime import date, datetime

import pytest

from appcore import order_analytics as oa
from appcore.order_analytics import realtime as realtime_oa


def test_build_yesterday_same_time_comparison_for_current_global(monkeypatch):
    target = date(2026, 6, 5)
    current_until = datetime(2026, 6, 6, 10, 20)
    previous_until = datetime(2026, 6, 5, 10, 20)
    calls = {}

    def fake_order_summary(day, data_until, **kwargs):
        calls["order_summary"] = (day, data_until, kwargs)
        assert day == date(2026, 6, 4)
        assert data_until == previous_until
        assert kwargs["site_codes"] == ("newjoy", "omurio")
        return {
            "order_count": 50,
            "line_count": 55,
            "units": 70,
            "order_revenue": 900.0,
            "line_revenue": 900.0,
            "shipping_revenue": 100.0,
            "revenue_with_shipping": 1000.0,
            "first_order_at": datetime(2026, 6, 4, 16, 30),
            "last_order_at": datetime(2026, 6, 5, 9, 50),
            "last_order_updated_at": datetime(2026, 6, 5, 10, 5),
        }

    def fake_profit_summary_until(day, day_start, data_until, **kwargs):
        calls["profit_summary"] = (day, day_start, data_until, kwargs)
        assert day == date(2026, 6, 4)
        assert data_until == previous_until
        return {"profit_with_estimate_usd": 200.0}

    monkeypatch.setattr(realtime_oa, "_get_realtime_order_summary", fake_order_summary)
    monkeypatch.setattr(realtime_oa, "_build_order_profit_summary_until", fake_profit_summary_until)

    current_result = {
        "period": {
            "date": target,
            "day_start_at": datetime(2026, 6, 5, 16, 0),
            "day_end_at": datetime(2026, 6, 6, 16, 0),
            "data_until_at": current_until,
        },
        "summary": {
            "revenue_with_shipping": 1200.0,
            "order_count": 60,
        },
        "order_profit_summary": {
            "profit_with_estimate_usd": 240.0,
        },
    }

    comparison = realtime_oa._build_yesterday_same_time_comparison(
        current_result,
        target=target,
        now=datetime(2026, 6, 6, 10, 25),
        product_id=None,
        product_ids=None,
        unmatched_ads=False,
        product_launch_scope=None,
        site_codes=("newjoy", "omurio"),
    )

    assert comparison["enabled"] is True
    assert comparison["label"] == "较昨天同刻"
    assert comparison["basis"]["current_business_date"] == "2026-06-05"
    assert comparison["basis"]["previous_business_date"] == "2026-06-04"
    assert comparison["basis"]["current_until_at"] == current_until
    assert comparison["basis"]["previous_until_at"] == previous_until
    assert comparison["summary"]["revenue_with_shipping"] == {
        "current": 1200.0,
        "previous": 1000.0,
        "pct": 20.0,
    }
    assert comparison["summary"]["order_count"] == {
        "current": 60,
        "previous": 50,
        "pct": 20.0,
    }
    assert comparison["summary"]["profit_with_estimate_usd"] == {
        "current": 240.0,
        "previous": 200.0,
        "pct": 20.0,
    }
    assert "order_summary" in calls
    assert "profit_summary" in calls


@pytest.mark.parametrize(
    "target, product_id, product_ids, unmatched_ads, product_launch_scope, site_codes",
    [
        (date(2026, 6, 4), None, None, False, None, ("newjoy", "omurio")),
        (date(2026, 6, 5), 42, None, False, None, ("newjoy", "omurio")),
        (date(2026, 6, 5), None, (42,), False, "new", ("newjoy", "omurio")),
        (date(2026, 6, 5), None, None, True, "unmatched", ("newjoy", "omurio")),
        (date(2026, 6, 5), None, None, False, None, ("newjoy",)),
    ],
)
def test_build_yesterday_same_time_comparison_disabled_outside_current_global(
    target,
    product_id,
    product_ids,
    unmatched_ads,
    product_launch_scope,
    site_codes,
):
    current_result = {
        "period": {
            "date": target,
            "day_start_at": datetime(2026, 6, 5, 16, 0),
            "day_end_at": datetime(2026, 6, 6, 16, 0),
            "data_until_at": datetime(2026, 6, 6, 10, 20),
        },
        "summary": {"revenue_with_shipping": 1200.0, "order_count": 60},
        "order_profit_summary": {"profit_with_estimate_usd": 240.0},
    }

    comparison = realtime_oa._build_yesterday_same_time_comparison(
        current_result,
        target=target,
        now=datetime(2026, 6, 6, 10, 25),
        product_id=product_id,
        product_ids=product_ids,
        unmatched_ads=unmatched_ads,
        product_launch_scope=product_launch_scope,
        site_codes=site_codes,
    )

    assert comparison == {
        "enabled": False,
        "label": "较昨天同刻",
        "basis": None,
        "summary": {},
    }


def test_metric_comparison_handles_previous_zero():
    assert realtime_oa._metric_comparison(0, 0, integer=True) == {
        "current": 0,
        "previous": 0,
        "pct": 0.0,
    }
    assert realtime_oa._metric_comparison(5, 0, integer=True) == {
        "current": 5,
        "previous": 0,
        "pct": None,
    }


def test_load_realtime_ad_cost_adjustments_until_caps_snapshot_and_units(monkeypatch):
    target = date(2026, 6, 4)
    snapshot_until = datetime(2026, 6, 5, 10, 20)
    account_snapshot = datetime(2026, 6, 5, 10, 0)
    calls = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if "SELECT business_date, ad_account_id, MAX(snapshot_at) AS snapshot_at" in sql:
            assert "snapshot_at<=%s" in sql
            assert args == (target, snapshot_until)
            return [
                {
                    "business_date": target,
                    "ad_account_id": "act_1",
                    "snapshot_at": account_snapshot,
                }
            ]
        if "SELECT business_date, campaign_name, normalized_campaign_code, spend_usd" in sql:
            assert args == (target, "act_1", account_snapshot)
            return [
                {
                    "business_date": target,
                    "campaign_name": "demo-product-rjc",
                    "normalized_campaign_code": "demo-product-rjc",
                    "spend_usd": 30.0,
                }
            ]
        if "COALESCE(SUM(d.quantity), 0) AS units" in sql:
            assert "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at) <= %s" in sql
            assert args == (target, snapshot_until)
            return [{"business_date": target, "product_id": 42, "units": 3}]
        if "SELECT d.dxm_package_id" in sql and "p.ad_cost_usd" in sql:
            assert "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at) <= %s" in sql
            assert args == (target, snapshot_until)
            return [
                {
                    "dxm_package_id": "PKG-1",
                    "business_date": target,
                    "status": "ok",
                    "product_id": 42,
                    "quantity": 1,
                    "ad_cost_usd": 2.0,
                },
                {
                    "dxm_package_id": "PKG-2",
                    "business_date": target,
                    "status": "ok",
                    "product_id": 42,
                    "quantity": 2,
                    "ad_cost_usd": 4.0,
                },
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(
        realtime_oa,
        "resolve_ad_product_match",
        lambda code: {"id": 42, "product_code": code},
        raising=False,
    )

    result = realtime_oa._load_realtime_ad_cost_adjustments_until(
        target,
        snapshot_until,
        product_id=None,
        site_codes=("newjoy", "omurio"),
    )

    assert result["package_deltas"] == {"PKG-1": 8.0, "PKG-2": 16.0}
    assert result["total_delta"] == 24.0
    assert result["unallocated_spend"] == 0.0
    assert result["has_realtime_ad_watermark"] is True
    assert any("snapshot_at<=%s" in sql for sql, _args in calls)
```

- [ ] **Step 2: Run backend tests and confirm they fail**

Run:

```bash
pytest tests/test_order_analytics_realtime_yesterday_comparison.py -q
```

Expected: FAIL with missing attributes such as `_build_yesterday_same_time_comparison`, `_metric_comparison`, and `_load_realtime_ad_cost_adjustments_until`.

---

### Task 2: Backend Comparison Helpers

**Files:**
- Modify: `appcore/order_analytics/realtime.py`
- Test: `tests/test_order_analytics_realtime_yesterday_comparison.py`

- [ ] **Step 1: Import percent helper**

In `appcore/order_analytics/realtime.py`, extend the `_helpers` import block:

```python
from ._helpers import (
    _beijing_now,
    current_meta_business_date,
    _business_hour,
    _compute_pct_change,
    _money,
    _parse_iso_date_param,
    _revenue_with_shipping,
    _roas,
)
```

- [ ] **Step 2: Add comparison primitives**

Insert this block after `_get_realtime_order_summary()`:

```python
def _empty_yesterday_same_time_comparison() -> dict[str, Any]:
    return {
        "enabled": False,
        "label": "较昨天同刻",
        "basis": None,
        "summary": {},
    }


def _metric_comparison(current: Any, previous: Any, *, integer: bool = False) -> dict[str, Any]:
    if integer:
        current_value = int(current or 0)
        previous_value = int(previous or 0)
    else:
        current_value = _money(current)
        previous_value = _money(previous)
    return {
        "current": current_value,
        "previous": previous_value,
        "pct": _compute_pct_change(current_value, previous_value),
    }


def _clamp_datetime(value: datetime, start: datetime, end: datetime) -> datetime:
    if value < start:
        return start
    if value > end:
        return end
    return value
```

- [ ] **Step 3: Add waterline-capped ad adjustment loader**

Insert this block after `_apply_realtime_ad_cost_adjustments()`:

```python
def _load_realtime_ad_cost_adjustments_until(
    target: date,
    snapshot_until: datetime,
    *,
    product_id: int | None = None,
    site_codes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    sites = _normalize_site_codes(site_codes)
    allowed_account_ids = _resolve_ad_account_ids_for_sites(sites)
    if allowed_account_ids is not None and not allowed_account_ids:
        return {
            "package_deltas": {},
            "status_deltas": {},
            "total_delta": 0.0,
            "unallocated_spend": 0.0,
            "has_realtime_ad_watermark": False,
        }

    account_sql = ""
    account_args: list[Any] = []
    if allowed_account_ids is not None:
        placeholders = ", ".join(["%s"] * len(allowed_account_ids))
        account_sql = f"AND ad_account_id IN ({placeholders}) "
        account_args = list(allowed_account_ids)

    snapshot_rows = query(
        "SELECT business_date, ad_account_id, MAX(snapshot_at) AS snapshot_at "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s AND snapshot_at<=%s "
        "AND data_completeness='realtime_partial' "
        + account_sql +
        "GROUP BY business_date, ad_account_id",
        tuple([target, snapshot_until] + account_args),
    )
    snapshots: list[tuple[date, str | None, Any]] = []
    for row in snapshot_rows or []:
        business_date = row.get("business_date")
        snapshot_at = row.get("snapshot_at")
        if business_date and snapshot_at:
            snapshots.append((business_date, row.get("ad_account_id"), snapshot_at))

    if not snapshots:
        return {
            "package_deltas": {},
            "status_deltas": {},
            "total_delta": 0.0,
            "unallocated_spend": 0.0,
            "has_realtime_ad_watermark": False,
        }

    target_product_id = int(product_id) if product_id else None
    spend_by_product: dict[tuple[date, int], float] = {}
    unallocated_spend = 0.0
    match_cache: dict[str, int | None] = {}

    for business_date, ad_account_id, snapshot_at in snapshots:
        if ad_account_id is None:
            campaign_rows = query(
                "SELECT business_date, campaign_name, normalized_campaign_code, spend_usd "
                "FROM meta_ad_realtime_daily_campaign_metrics "
                "WHERE business_date=%s AND ad_account_id IS NULL AND snapshot_at=%s "
                "AND data_completeness='realtime_partial'",
                (business_date, snapshot_at),
            )
        else:
            campaign_rows = query(
                "SELECT business_date, campaign_name, normalized_campaign_code, spend_usd "
                "FROM meta_ad_realtime_daily_campaign_metrics "
                "WHERE business_date=%s AND ad_account_id=%s AND snapshot_at=%s "
                "AND data_completeness='realtime_partial'",
                (business_date, ad_account_id, snapshot_at),
            )
        for row in campaign_rows or []:
            spend = float(row.get("spend_usd") or 0)
            if spend <= 0:
                continue
            code = str(row.get("normalized_campaign_code") or row.get("campaign_name") or "").strip().lower()
            product_match_id: int | None = None
            if code:
                if code not in match_cache:
                    match = resolve_ad_product_match(code)
                    match_cache[code] = int(match["id"]) if match and match.get("id") is not None else None
                product_match_id = match_cache[code]
            if product_match_id is None:
                if target_product_id is None:
                    unallocated_spend += spend
                continue
            if target_product_id and product_match_id != target_product_id:
                continue
            key = (business_date, product_match_id)
            spend_by_product[key] = round(float(spend_by_product.get(key) or 0.0) + spend, 4)

    if not spend_by_product:
        return {
            "package_deltas": {},
            "status_deltas": {},
            "total_delta": 0.0,
            "unallocated_spend": unallocated_spend,
            "has_realtime_ad_watermark": True,
        }

    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_filter = ""
    product_args: list[Any] = []
    if target_product_id:
        product_filter = " AND p.product_id = %s"
        product_args.append(target_product_id)

    unit_rows = query(
        "SELECT d.meta_business_date AS business_date, p.product_id, "
        "COALESCE(SUM(d.quantity), 0) AS units "
        "FROM order_profit_lines p "
        "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id "
        "WHERE d.meta_business_date=%s "
        "AND " + order_time_expr + " <= %s "
        "AND p.product_id IS NOT NULL "
        f"{product_filter} "
        "GROUP BY d.meta_business_date, p.product_id",
        tuple([target, snapshot_until] + product_args),
    )
    units_by_product: dict[tuple[date, int], int] = {}
    for row in unit_rows or []:
        business_date = row.get("business_date")
        product = row.get("product_id")
        if business_date and product is not None:
            units_by_product[(business_date, int(product))] = int(row.get("units") or 0)

    no_unit_spend = sum(
        float(spend or 0)
        for key, spend in spend_by_product.items()
        if int(units_by_product.get(key) or 0) <= 0
    )
    allocated_spend_by_product = {
        key: spend
        for key, spend in spend_by_product.items()
        if int(units_by_product.get(key) or 0) > 0
    }
    if not allocated_spend_by_product:
        return {
            "package_deltas": {},
            "status_deltas": {},
            "total_delta": 0.0,
            "unallocated_spend": unallocated_spend + no_unit_spend,
            "has_realtime_ad_watermark": True,
        }

    line_rows = query(
        "SELECT d.dxm_package_id, d.meta_business_date AS business_date, "
        "p.status, p.product_id, d.quantity, p.ad_cost_usd "
        "FROM order_profit_lines p "
        "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id "
        "WHERE d.meta_business_date=%s "
        "AND " + order_time_expr + " <= %s "
        "AND p.product_id IS NOT NULL "
        f"{product_filter}",
        tuple([target, snapshot_until] + product_args),
    )

    package_deltas: dict[str, float] = {}
    status_deltas: dict[str, float] = {}
    total_delta = 0.0
    for row in line_rows or []:
        business_date = row.get("business_date")
        product = row.get("product_id")
        if not business_date or product is None:
            continue
        key = (business_date, int(product))
        spend = float(allocated_spend_by_product.get(key) or 0)
        units = int(units_by_product.get(key) or 0)
        quantity = int(row.get("quantity") or 0)
        if spend <= 0 or units <= 0 or quantity <= 0:
            continue
        realtime_cost = round(spend * quantity / units, 4)
        stored_cost = float(row.get("ad_cost_usd") or 0)
        delta = realtime_cost - stored_cost
        if abs(delta) < 0.0001:
            continue
        package_id = str(row.get("dxm_package_id") or "")
        if package_id:
            package_deltas[package_id] = round(float(package_deltas.get(package_id) or 0.0) + delta, 4)
        status = str(row.get("status") or "")
        if status:
            status_deltas[status] = round(float(status_deltas.get(status) or 0.0) + delta, 4)
        total_delta += delta

    return {
        "package_deltas": package_deltas,
        "status_deltas": status_deltas,
        "total_delta": total_delta,
        "unallocated_spend": unallocated_spend + no_unit_spend,
        "has_realtime_ad_watermark": True,
    }
```

- [ ] **Step 4: Add waterline apply helper**

Insert this block immediately after `_load_realtime_ad_cost_adjustments_until()`:

```python
def _apply_realtime_ad_cost_adjustments_until(
    details: list[dict[str, Any]],
    *,
    target: date,
    snapshot_until: datetime,
    product_id: int | None,
    site_codes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    adjustments = _load_realtime_ad_cost_adjustments_until(
        target,
        snapshot_until,
        product_id=product_id,
        site_codes=site_codes,
    )
    package_deltas = adjustments.get("package_deltas") or {}
    for row in details:
        package_id = str(row.get("dxm_package_id") or "")
        if not package_id:
            continue
        delta = float(package_deltas.get(package_id) or 0.0)
        if not delta:
            continue
        row["ad_cost_usd"] = round(float(row.get("ad_cost_usd") or 0.0) + delta, 4)
        if row.get("order_profit_usd") is not None:
            row["order_profit_usd"] = round(float(row.get("order_profit_usd") or 0.0) - delta, 2)
        if row.get("order_profit_with_estimate_usd") is not None:
            row["order_profit_with_estimate_usd"] = round(
                float(row.get("order_profit_with_estimate_usd") or 0.0) - delta,
                2,
            )
    return adjustments
```

- [ ] **Step 5: Extend `_get_realtime_order_profit_details()` with an optional waterline**

Change the function signature to include `ad_adjustment_snapshot_until`:

```python
def _get_realtime_order_profit_details(
    target: date,
    day_start: datetime,
    data_until: datetime,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    page: int | None = None,
    page_size: int | None = None,
    site_codes: tuple[str, ...] | None = None,
    ad_adjustment_snapshot_until: datetime | None = None,
) -> list[dict[str, Any]]:
```

Replace the existing adjustment block at the end of that function with:

```python
    if product_ids is None and not unmatched_ads:
        if ad_adjustment_snapshot_until is not None:
            _apply_realtime_ad_cost_adjustments_until(
                details,
                target=target,
                snapshot_until=ad_adjustment_snapshot_until,
                product_id=product_id,
                site_codes=sites,
            )
        else:
            _apply_realtime_ad_cost_adjustments(
                details,
                date_from=target,
                date_to=target,
                product_id=product_id,
            )
```

- [ ] **Step 6: Add profit summary until helper**

Insert this block after `_get_realtime_order_profit_details()`:

```python
def _build_order_profit_summary_until(
    target: date,
    day_start: datetime,
    data_until: datetime,
    *,
    site_codes: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    ad_summary = _get_realtime_ad_summary_for_business_date(
        target,
        data_until,
        site_codes=site_codes,
    )
    if not ad_summary or ad_summary.get("snapshot_at") is None:
        return None
    details = _get_realtime_order_profit_details(
        target,
        day_start,
        data_until,
        site_codes=site_codes,
        ad_adjustment_snapshot_until=data_until,
    )
    return _build_order_profit_summary(
        details,
        total_ad_spend_usd=ad_summary.get("ad_spend"),
    )
```

- [ ] **Step 7: Add comparison builder**

Insert this block after `_build_order_profit_summary_until()`:

```python
def _build_yesterday_same_time_comparison(
    current_result: dict[str, Any],
    *,
    target: date,
    now: datetime,
    product_id: int | None,
    product_ids: tuple[int, ...] | None,
    unmatched_ads: bool,
    product_launch_scope: str | None,
    site_codes: tuple[str, ...],
) -> dict[str, Any]:
    if target != current_meta_business_date(now):
        return _empty_yesterday_same_time_comparison()
    if product_id is not None or product_ids is not None or unmatched_ads or product_launch_scope:
        return _empty_yesterday_same_time_comparison()
    if not _site_codes_use_default(site_codes):
        return _empty_yesterday_same_time_comparison()

    period = current_result.get("period") or {}
    current_until = period.get("data_until_at")
    current_day_start = period.get("day_start_at")
    current_day_end = period.get("day_end_at")
    if not isinstance(current_until, datetime) or not isinstance(current_day_start, datetime):
        return _empty_yesterday_same_time_comparison()
    if not isinstance(current_day_end, datetime):
        current_day_end = current_day_start + timedelta(hours=24)

    current_until = _clamp_datetime(current_until, current_day_start, current_day_end)
    elapsed = current_until - current_day_start
    if elapsed.total_seconds() < 0:
        elapsed = timedelta(0)
    if elapsed > timedelta(hours=24):
        elapsed = timedelta(hours=24)

    previous_date = target - timedelta(days=1)
    previous_day_start, previous_day_end = compute_meta_business_window_bj(previous_date)
    previous_until = _clamp_datetime(previous_day_start + elapsed, previous_day_start, previous_day_end)

    previous_order_summary = _get_realtime_order_summary(
        previous_date,
        previous_until,
        site_codes=site_codes,
    )
    previous_profit_summary = _build_order_profit_summary_until(
        previous_date,
        previous_day_start,
        previous_until,
        site_codes=site_codes,
    )

    current_summary = current_result.get("summary") or {}
    current_profit_summary = current_result.get("order_profit_summary") or {}
    previous_profit_value = None
    if previous_profit_summary is not None:
        previous_profit_value = previous_profit_summary.get("profit_with_estimate_usd")

    return {
        "enabled": True,
        "label": "较昨天同刻",
        "basis": {
            "current_business_date": target.isoformat(),
            "previous_business_date": previous_date.isoformat(),
            "current_until_at": current_until,
            "previous_until_at": previous_until,
        },
        "summary": {
            "revenue_with_shipping": _metric_comparison(
                current_summary.get("revenue_with_shipping"),
                previous_order_summary.get("revenue_with_shipping"),
            ),
            "order_count": _metric_comparison(
                current_summary.get("order_count"),
                previous_order_summary.get("order_count"),
                integer=True,
            ),
            "profit_with_estimate_usd": _metric_comparison(
                current_profit_summary.get("profit_with_estimate_usd"),
                previous_profit_value,
            ),
        },
    }


def _attach_yesterday_same_time_comparison(
    result: dict[str, Any],
    *,
    target: date,
    now: datetime,
    product_id: int | None,
    product_ids: tuple[int, ...] | None,
    unmatched_ads: bool,
    product_launch_scope: str | None,
    site_codes: tuple[str, ...],
) -> dict[str, Any]:
    try:
        comparison = _build_yesterday_same_time_comparison(
            result,
            target=target,
            now=now,
            product_id=product_id,
            product_ids=product_ids,
            unmatched_ads=unmatched_ads,
            product_launch_scope=product_launch_scope,
            site_codes=site_codes,
        )
    except Exception:
        comparison = _empty_yesterday_same_time_comparison()
    result["comparison"] = {"yesterday_same_time": comparison}
    return result


def _attach_disabled_yesterday_same_time_comparison(result: dict[str, Any]) -> dict[str, Any]:
    result["comparison"] = {"yesterday_same_time": _empty_yesterday_same_time_comparison()}
    return result
```

- [ ] **Step 8: Run backend tests**

Run:

```bash
pytest tests/test_order_analytics_realtime_yesterday_comparison.py -q
```

Expected: PASS.

---

### Task 3: Wire Comparison Into Realtime Overview Responses

**Files:**
- Modify: `appcore/order_analytics/realtime.py`
- Test: `tests/test_order_analytics_realtime_yesterday_comparison.py`

- [ ] **Step 1: Add response-level tests**

Append these tests to `tests/test_order_analytics_realtime_yesterday_comparison.py`:

```python
def test_get_realtime_roas_overview_attaches_disabled_comparison_for_range(monkeypatch):
    def fake_query(sql, args=()):
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-06-03",
        end_date="2026-06-05",
        now=datetime(2026, 6, 6, 10, 25),
    )

    assert result["comparison"]["yesterday_same_time"] == {
        "enabled": False,
        "label": "较昨天同刻",
        "basis": None,
        "summary": {},
    }


def test_get_realtime_roas_overview_attaches_current_day_global_comparison(monkeypatch):
    target = date(2026, 6, 5)
    snapshot_at = datetime(2026, 6, 6, 10, 20)

    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql:
            return []
        if "FROM roi_realtime_daily_snapshots" in sql:
            return [
                {
                    "id": 900,
                    "snapshot_at": snapshot_at,
                    "source_run_id": 901,
                    "order_count": 60,
                    "line_count": 66,
                    "units": 80,
                    "order_revenue_usd": 1100.0,
                    "shipping_revenue_usd": 100.0,
                    "ad_spend_usd": 300.0,
                    "last_order_at": datetime(2026, 6, 6, 10, 5),
                    "order_data_status": "ok",
                    "ad_data_status": "ok",
                }
            ]
        if "FROM roi_hourly_sync_runs" in sql:
            return [{"last_order_updated_at": datetime(2026, 6, 6, 10, 21)}]
        if "COALESCE(MAX(r.finished_at)" in sql:
            return [{"last_ad_updated_at": datetime(2026, 6, 6, 10, 18)}]
        if "SELECT ad_account_id, MAX(snapshot_at) AS latest_at" in sql:
            return [{"ad_account_id": "act_1", "latest_at": snapshot_at}]
        if "SELECT MAX(snapshot_at) AS latest_at" in sql:
            return [{"latest_at": snapshot_at}]
        if "SELECT business_date, ad_account_id, MAX(snapshot_at) AS snapshot_at" in sql:
            return [
                {
                    "business_date": date(2026, 6, 4),
                    "ad_account_id": "act_1",
                    "snapshot_at": datetime(2026, 6, 5, 10, 0),
                }
            ]
        if "SELECT business_date, campaign_name, normalized_campaign_code, spend_usd" in sql:
            return [
                {
                    "business_date": date(2026, 6, 4),
                    "campaign_name": "demo-product-rjc",
                    "normalized_campaign_code": "demo-product-rjc",
                    "spend_usd": 100.0,
                }
            ]
        if "SELECT ad_account_id, ad_account_name, campaign_id" in sql:
            return [
                {
                    "ad_account_id": "act_1",
                    "ad_account_name": "Account",
                    "campaign_id": "cmp_1",
                    "campaign_name": "demo-product-rjc",
                    "normalized_campaign_code": "demo-product-rjc",
                    "result_count": 5,
                    "spend_usd": 300.0,
                    "purchase_value_usd": 400.0,
                    "impressions": 1000,
                    "clicks": 50,
                }
            ]
        if "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS order_revenue" in sql:
            if args[0] == date(2026, 6, 4):
                return [
                    {
                        "order_count": 50,
                        "line_count": 55,
                        "units": 70,
                        "order_revenue": 900.0,
                        "line_revenue": 900.0,
                        "shipping_revenue": 100.0,
                        "first_order_at": datetime(2026, 6, 4, 16, 30),
                        "last_order_at": datetime(2026, 6, 5, 9, 50),
                        "last_order_updated_at": datetime(2026, 6, 5, 10, 5),
                    }
                ]
            return []
        if "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return []
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(
        realtime_oa,
        "resolve_ad_product_match",
        lambda code: {"id": 42, "product_code": code},
        raising=False,
    )

    result = oa.get_realtime_roas_overview(
        start_date=target.isoformat(),
        end_date=target.isoformat(),
        now=datetime(2026, 6, 6, 10, 25),
        include_profit_summary=True,
    )

    comparison = result["comparison"]["yesterday_same_time"]
    assert comparison["enabled"] is True
    assert comparison["summary"]["revenue_with_shipping"]["pct"] == 20.0
    assert comparison["summary"]["order_count"]["pct"] == 20.0
    assert "profit_with_estimate_usd" in comparison["summary"]
```

- [ ] **Step 2: Run new response tests and confirm they fail**

Run:

```bash
pytest tests/test_order_analytics_realtime_yesterday_comparison.py::test_get_realtime_roas_overview_attaches_disabled_comparison_for_range tests/test_order_analytics_realtime_yesterday_comparison.py::test_get_realtime_roas_overview_attaches_current_day_global_comparison -q
```

Expected: FAIL because realtime overview responses do not attach `comparison` yet.

- [ ] **Step 3: Attach disabled comparison in range branch**

In `get_realtime_roas_overview()`, replace the range branch's direct return of `_build_realtime_overview_for_range` inside the `if start != end:` block with:

```python
            return _attach_disabled_yesterday_same_time_comparison(
                _build_realtime_overview_for_range(
                    start,
                    end,
                    now,
                    include_details=include_details,
                    include_profit_summary=include_profit_summary,
                    product_id=normalized_product_id,
                    product_launch_scope=normalized_launch_scope,
                    product_ids=launch_product_ids,
                    unmatched_ads=launch_scope_unmatched,
                    order_page=normalized_order_page,
                    order_page_size=normalized_order_page_size,
                    page=normalized_page,
                    page_size=normalized_page_size,
                    site_codes=normalized_site_codes,
                )
            )
```

- [ ] **Step 4: Attach comparison before single-day returns**

For each single-day response dictionary in `get_realtime_roas_overview()`, assign the dictionary to `res` and return:

```python
        return _attach_yesterday_same_time_comparison(
            res,
            target=target,
            now=now,
            product_id=normalized_product_id,
            product_ids=launch_product_ids,
            unmatched_ads=launch_scope_unmatched,
            product_launch_scope=normalized_launch_scope,
            site_codes=normalized_site_codes,
        )
```

There are three single-day response branches to update:

- Product-specific realtime campaign snapshot branch.
- Default `roi_realtime_daily_snapshots` branch.
- Fallback daily / realtime detail branch near the end of the function.

In product-specific and launch-scope branches, the helper returns disabled comparison because the guard rejects non-global scope.

- [ ] **Step 5: Run backend comparison tests**

Run:

```bash
pytest tests/test_order_analytics_realtime_yesterday_comparison.py -q
```

Expected: PASS.

- [ ] **Step 6: Run focused realtime regression tests**

Run:

```bash
pytest tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_realtime_profit_details.py \
       tests/test_order_analytics_realtime_profit_margin.py \
       tests/test_order_analytics_realtime_site_filter.py \
       -q
```

Expected: PASS.

- [ ] **Step 7: Commit backend changes**

Run:

```bash
git add appcore/order_analytics/realtime.py tests/test_order_analytics_realtime_yesterday_comparison.py
git commit -m "feat: add realtime dashboard same-time comparison backend" -m "Docs-anchor: docs/superpowers/specs/2026-06-06-realtime-dashboard-yesterday-same-time-comparison-design.md"
```

Expected: commit succeeds.

---

### Task 4: Frontend Failing Tests and Rendering

**Files:**
- Modify: `web/templates/order_analytics.html`
- Modify: `tests/test_order_analytics_template_layout.py`

- [ ] **Step 1: Add template tests**

Append these tests to `tests/test_order_analytics_template_layout.py`:

```python
def test_realtime_global_cards_have_yesterday_same_time_compare_targets():
    panel = _realtime_panel_source()

    assert 'id="realtimeRevenueWithShippingCompare"' in panel
    assert 'id="realtimeOrderCountCompare"' in panel
    assert 'id="realtimeProfitCompare"' in panel
    assert panel.index('id="realtimeRevenueWithShipping"') < panel.index('id="realtimeRevenueWithShippingCompare"')
    assert panel.index('id="realtimeOrderCount"') < panel.index('id="realtimeOrderCountCompare"')
    assert panel.index('id="realtimeProfitMargin"') < panel.index('id="realtimeProfitCompare"')
    assert 'id="realtimeNewRevenueWithShippingCompare"' not in panel
    assert 'id="realtimeOldRevenueWithShippingCompare"' not in panel
    assert 'id="realtimeUnmatchedRevenueWithShippingCompare"' not in panel


def test_realtime_global_same_time_compare_js_only_renders_global_scope():
    template = _template_source()
    render_block = template[
        template.index("function renderRealtimeScopeSummary"):
        template.index("function renderRealtimeFreshness")
    ]

    assert "function formatRealtimeSameTimeCompare" in template
    assert "function setRealtimeSameTimeCompare" in template
    assert "function clearRealtimeSameTimeCompare" in template
    assert "data.comparison.yesterday_same_time" in render_block
    assert "if (scope !== 'global')" in render_block
    assert "realtimeRevenueWithShippingCompare" in render_block
    assert "realtimeOrderCountCompare" in render_block
    assert "realtimeProfitCompare" in render_block
    assert "toFixed(0)" in template
    assert "'+'" in template
    assert "'较昨天同刻 --'" in template
```

- [ ] **Step 2: Run template tests and confirm they fail**

Run:

```bash
pytest tests/test_order_analytics_template_layout.py::test_realtime_global_cards_have_yesterday_same_time_compare_targets tests/test_order_analytics_template_layout.py::test_realtime_global_same_time_compare_js_only_renders_global_scope -q
```

Expected: FAIL because the DOM ids and JS helpers do not exist.

- [ ] **Step 3: Add global card compare DOM nodes**

In `web/templates/order_analytics.html`, update only the three global card blocks:

```html
<div class="oar-scope-metric"><div class="oa-stat-label">总销售额</div><div class="oa-stat-value" id="realtimeRevenueWithShipping">$0.00</div><div class="oa-stat-sub">商品销售额 + 运费</div><div class="oa-stat-sub oar-same-time-compare" id="realtimeRevenueWithShippingCompare"></div></div>
```

```html
<div class="oar-scope-metric"><div class="oa-stat-label">订单数</div><div class="oa-stat-value" id="realtimeOrderCount">0</div><div class="oa-stat-sub oar-same-time-compare" id="realtimeOrderCountCompare"></div></div>
```

```html
<div class="oar-scope-metric"><div class="oa-stat-label">利润</div><div class="oa-stat-value" id="realtimeProfit">$0.00</div><div class="oa-stat-sub" id="realtimeProfitSub">按订单盈亏口径</div><div class="oa-stat-sub" id="realtimeProfitMargin">利润率 -</div><div class="oa-stat-sub oar-same-time-compare" id="realtimeProfitCompare"></div></div>
```

- [ ] **Step 4: Add compare JS helpers**

Insert this block before `function renderRealtimeScopeSummary(scope, data)`:

```javascript
  function formatRealtimeSameTimeCompare(metric) {
    var pct = metric && Number(metric.pct);
    if (!Number.isFinite(pct)) return '较昨天同刻 --';
    var rounded = pct.toFixed(0);
    var sign = pct > 0 ? '+' : '';
    return '较昨天同刻 ' + sign + rounded + '%';
  }

  function setRealtimeSameTimeCompare(id, metric) {
    var el = document.getElementById(id);
    if (!el) return;
    var pct = metric && Number(metric.pct);
    el.textContent = formatRealtimeSameTimeCompare(metric);
    el.classList.toggle('oar-profit-ok', Number.isFinite(pct) && pct > 0);
    el.classList.toggle('oar-profit-loss', Number.isFinite(pct) && pct < 0);
    el.classList.toggle('oad-pct-flat', !Number.isFinite(pct) || pct === 0);
  }

  function clearRealtimeSameTimeCompare() {
    ['realtimeRevenueWithShippingCompare', 'realtimeOrderCountCompare', 'realtimeProfitCompare'].forEach(function(id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.textContent = '';
      el.classList.remove('oar-profit-ok', 'oar-profit-loss', 'oad-pct-flat');
    });
  }
```

- [ ] **Step 5: Render comparison only for global scope**

Inside `renderRealtimeScopeSummary(scope, data)`, after the existing profit margin rendering block:

```javascript
    if (scope !== 'global') {
      return;
    }
    var sameTime = data.comparison && data.comparison.yesterday_same_time;
    if (!sameTime || !sameTime.enabled) {
      clearRealtimeSameTimeCompare();
      return;
    }
    var sameTimeSummary = sameTime.summary || {};
    setRealtimeSameTimeCompare('realtimeRevenueWithShippingCompare', sameTimeSummary.revenue_with_shipping);
    setRealtimeSameTimeCompare('realtimeOrderCountCompare', sameTimeSummary.order_count);
    setRealtimeSameTimeCompare('realtimeProfitCompare', sameTimeSummary.profit_with_estimate_usd);
```

- [ ] **Step 6: Clear comparison in loading and error states**

At the end of `setRealtimeScopeCardsLoading()` add:

```javascript
    clearRealtimeSameTimeCompare();
```

At the end of `setRealtimeScopeCardsError(message)` add:

```javascript
    clearRealtimeSameTimeCompare();
```

- [ ] **Step 7: Run template tests**

Run:

```bash
pytest tests/test_order_analytics_template_layout.py::test_realtime_global_cards_have_yesterday_same_time_compare_targets tests/test_order_analytics_template_layout.py::test_realtime_global_same_time_compare_js_only_renders_global_scope -q
```

Expected: PASS.

- [ ] **Step 8: Run full template layout tests**

Run:

```bash
pytest tests/test_order_analytics_template_layout.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit frontend changes**

Run:

```bash
git add web/templates/order_analytics.html tests/test_order_analytics_template_layout.py
git commit -m "feat: render realtime dashboard same-time comparison" -m "Docs-anchor: docs/superpowers/specs/2026-06-06-realtime-dashboard-yesterday-same-time-comparison-design.md"
```

Expected: commit succeeds.

---

### Task 5: Final Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run required test set**

Run:

```bash
pytest tests/test_order_analytics_realtime_yesterday_comparison.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_realtime_profit_details.py \
       tests/test_order_analytics_realtime_profit_margin.py \
       tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_template_layout.py \
       -q
```

Expected: PASS.

- [ ] **Step 2: Inspect git status**

Run:

```bash
git status --short
```

Expected: no unstaged or uncommitted changes.

- [ ] **Step 3: Report implementation result**

Report:

```text
Implemented current-day global realtime dashboard yesterday-same-time comparison.
Verified with the required pytest set.
```

If any test fails, stop and report the failing test name and assertion before changing more code.

---

## Self-Review

Spec coverage:

- Current-day only: Task 1 disabled-guard test and Task 2 comparison guard.
- Global only: Task 1 disabled-guard test and Task 4 JS scope guard.
- Three metrics only: Task 1 summary assertions and Task 4 three DOM ids.
- 0-decimal signed percent: Task 4 `formatRealtimeSameTimeCompare`.
- Yesterday same business progress: Task 1 `previous_until_at` assertion and Task 2 elapsed-waterline logic.
- Profit waterline: Task 1 ad-cost adjustment test and Task 2 `_load_realtime_ad_cost_adjustments_until`.
- Stable disabled response: Task 3 range response test.

Placeholder scan:

- No unresolved placeholder markers.
- No unspecified test names.
- Every command has an expected result.

Type consistency:

- Backend object path is `comparison.yesterday_same_time`.
- Metric keys are `revenue_with_shipping`, `order_count`, and `profit_with_estimate_usd`.
- DOM ids are `realtimeRevenueWithShippingCompare`, `realtimeOrderCountCompare`, and `realtimeProfitCompare`.
