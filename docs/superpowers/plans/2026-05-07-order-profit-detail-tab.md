# Order Profit Detail Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a realtime dashboard sub-tab that shows order-level profit details with every fee item displayed separately.

**Architecture:** Keep the existing `/order-analytics/realtime-overview` response shape and add an `order_profit_details` array for the current Meta business day. Put Shopify fee split math in `appcore/order_analytics/shopify_fee.py`, keep order aggregation and refund/profit calculation in `appcore/order_analytics/realtime.py`, and render a new plain-JS table in `web/templates/order_analytics.html`. Do not modify `order_profit_lines.profit_usd` or the standalone `/order-profit` dashboard.

**Tech Stack:** Flask, PyMySQL-style raw SQL wrappers, Jinja templates, vanilla JavaScript, pytest.

---

## Confirmed Business Rules

Use the approved spec:

- `docs/superpowers/specs/2026-05-07-order-profit-detail-tab-design.md`

Order profit formula:

```text
order_profit =
  total_revenue
  - refund_deduction
  - purchase_cost
  - logistics_cost
  - shopify_platform_fee
  - international_card_fee
  - currency_conversion_fee
  - ad_cost
```

Rules:

- Deduct ad cost.
- Do not deduct `return_reserve_usd`.
- Show every fee/cost item separately in the frontend.
- Partial refunds use actual `refund_amount_usd`.
- Refund amount is order-level and may be duplicated on SKU rows, so aggregate with `MAX(refund_amount_usd)`.
- Full refunds, or refund/cancel states with no usable refund amount, deduct the full order `total_revenue`.
- `合计手续费` is display-only and must not be subtracted a second time.

## File Structure

- Modify `appcore/order_analytics/shopify_fee.py`
  - Add `split_shopify_fee_for_order()` to expose base platform fee, international card fee, currency conversion fee, total fee, tier, and presentment currency.

- Modify `appcore/order_analytics/realtime.py`
  - Add refund helpers.
  - Add `_get_realtime_order_profit_details()`.
  - Include `order_profit_details` in realtime overview responses for snapshot, fallback, and range branches.

- Modify `web/templates/order_analytics.html`
  - Add the `订单盈亏明细` realtime sub-tab.
  - Add the wide table.
  - Add loading/error rendering.
  - Add `renderRealtimeOrderProfitDetails()`.

- Modify `tests/test_shopify_fee.py`
  - Cover fee split behavior and one fixed fee per order.

- Create `tests/test_order_analytics_realtime_profit_details.py`
  - Cover refund deduction, status derivation, backend aggregation, and API schema.

- Modify `tests/test_order_analytics_true_roas.py`
  - Add template regression tests for the new sub-tab and all fee columns.

No migration. No scheduled task change.

## Task 1: Shopify Fee Split Helper

**Files:**
- Modify: `appcore/order_analytics/shopify_fee.py`
- Test: `tests/test_shopify_fee.py`

- [ ] **Step 1: Add failing tests for order-level fee split**

Append these tests to `tests/test_shopify_fee.py`:

```python
def test_split_shopify_fee_for_order_domestic_usd():
    from appcore.order_analytics.shopify_fee import split_shopify_fee_for_order

    result = split_shopify_fee_for_order(amount=100, buyer_country="US")

    assert result["presentment_currency"] == "USD"
    assert result["shopify_tier"] == "A"
    assert result["shopify_platform_fee_usd"] == 2.80
    assert result["international_card_fee_usd"] == 0.0
    assert result["currency_conversion_fee_usd"] == 0.0
    assert result["shopify_fee_total_usd"] == 2.80


def test_split_shopify_fee_for_order_international_eur():
    from appcore.order_analytics.shopify_fee import split_shopify_fee_for_order

    result = split_shopify_fee_for_order(amount=100, buyer_country="DE")

    assert result["presentment_currency"] == "EUR"
    assert result["shopify_tier"] == "D"
    assert result["shopify_platform_fee_usd"] == 2.80
    assert result["international_card_fee_usd"] == 1.00
    assert result["currency_conversion_fee_usd"] == 1.50
    assert result["shopify_fee_total_usd"] == 5.30


def test_split_shopify_fee_for_order_unknown_country_uses_estimated_cross_border_usd():
    from appcore.order_analytics.shopify_fee import split_shopify_fee_for_order

    result = split_shopify_fee_for_order(amount=100, buyer_country=None)

    assert result["presentment_currency"] == "USD"
    assert result["shopify_tier"] == "B_estimated"
    assert result["shopify_platform_fee_usd"] == 2.80
    assert result["international_card_fee_usd"] == 1.00
    assert result["currency_conversion_fee_usd"] == 0.0
    assert result["shopify_fee_total_usd"] == 3.80
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
pytest tests/test_shopify_fee.py::test_split_shopify_fee_for_order_domestic_usd tests/test_shopify_fee.py::test_split_shopify_fee_for_order_international_eur tests/test_shopify_fee.py::test_split_shopify_fee_for_order_unknown_country_uses_estimated_cross_border_usd -q
```

Expected: FAIL with `ImportError` or `AttributeError` for `split_shopify_fee_for_order`.

- [ ] **Step 3: Implement the helper**

Add this function near `estimate_fee_for_buyer_country()` in `appcore/order_analytics/shopify_fee.py`:

```python
def split_shopify_fee_for_order(
    *,
    amount: Any,
    buyer_country: str | None,
    settlement_currency: str = DEFAULT_SETTLEMENT_CURRENCY,
    store_country: str = DEFAULT_STORE_COUNTRY,
) -> dict[str, Any]:
    """Split Shopify Payments fee into displayable order-level fee parts."""
    amount_d = _to_decimal(amount)
    normalized_country = (buyer_country or "").strip().upper() or None
    if normalized_country:
        presentment_currency = infer_presentment_currency_from_country(normalized_country)
        is_cross_border = normalized_country != store_country.upper()
        tier = classify_tier(
            presentment_currency,
            normalized_country,
            settlement_currency=settlement_currency,
            store_country=store_country,
        )
    else:
        presentment_currency = settlement_currency
        is_cross_border = True
        tier = classify_tier(
            presentment_currency,
            None,
            settlement_currency=settlement_currency,
            store_country=store_country,
        ) + "_estimated"

    needs_conversion = presentment_currency.upper() != settlement_currency.upper()
    platform_fee = amount_d * BASE_RATE + FIXED_FEE
    international_fee = amount_d * CROSS_BORDER_RATE if is_cross_border else Decimal("0")
    conversion_fee = amount_d * CURRENCY_CONVERSION_RATE if needs_conversion else Decimal("0")
    total_fee = platform_fee + international_fee + conversion_fee

    return {
        "presentment_currency": presentment_currency,
        "shopify_tier": tier,
        "shopify_platform_fee_usd": _round2(platform_fee),
        "international_card_fee_usd": _round2(international_fee),
        "currency_conversion_fee_usd": _round2(conversion_fee),
        "shopify_fee_total_usd": _round2(total_fee),
    }
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
pytest tests/test_shopify_fee.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add appcore/order_analytics/shopify_fee.py tests/test_shopify_fee.py
git commit -m "feat(order-analytics): split shopify order fees"
```

## Task 2: Refund and Profit Pure Helpers

**Files:**
- Modify: `appcore/order_analytics/realtime.py`
- Create: `tests/test_order_analytics_realtime_profit_details.py`

- [ ] **Step 1: Write failing tests for refund and profit helpers**

Create `tests/test_order_analytics_realtime_profit_details.py` with:

```python
from __future__ import annotations

from appcore.order_analytics.realtime import (
    _derive_order_profit_status,
    _derive_refund_status,
    _is_refund_like_state,
    _resolve_refund_deduction,
)


def test_resolve_refund_deduction_uses_actual_partial_refund():
    assert _resolve_refund_deduction(
        total_revenue=100.0,
        refund_amount_usd=12.5,
        order_state="paid",
    ) == 12.5


def test_resolve_refund_deduction_caps_refund_to_total_revenue():
    assert _resolve_refund_deduction(
        total_revenue=100.0,
        refund_amount_usd=150.0,
        order_state="paid",
    ) == 100.0


def test_resolve_refund_deduction_uses_full_revenue_for_refund_state_without_amount():
    assert _resolve_refund_deduction(
        total_revenue=88.0,
        refund_amount_usd=0,
        order_state="refunded",
    ) == 88.0


def test_refund_state_detects_english_and_chinese_values():
    assert _is_refund_like_state("refund success") is True
    assert _is_refund_like_state("cancelled") is True
    assert _is_refund_like_state("已退款") is True
    assert _is_refund_like_state("已取消") is True
    assert _is_refund_like_state("paid") is False


def test_derive_refund_status():
    assert _derive_refund_status(total_revenue=100, refund_deduction=0) == "none"
    assert _derive_refund_status(total_revenue=100, refund_deduction=20) == "partial_refund"
    assert _derive_refund_status(total_revenue=100, refund_deduction=100) == "full_refund"


def test_derive_order_profit_status():
    assert _derive_order_profit_status(line_count=2, ok_count=2, incomplete_count=0) == "ok"
    assert _derive_order_profit_status(line_count=2, ok_count=1, incomplete_count=1) == "partially_complete"
    assert _derive_order_profit_status(line_count=2, ok_count=0, incomplete_count=2) == "incomplete"
    assert _derive_order_profit_status(line_count=0, ok_count=0, incomplete_count=0) == "not_computed"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
pytest tests/test_order_analytics_realtime_profit_details.py -q
```

Expected: FAIL because helpers do not exist.

- [ ] **Step 3: Implement pure helpers**

Add these helpers after `_get_realtime_order_details()` in `appcore/order_analytics/realtime.py`:

```python
_REFUND_STATE_KEYWORDS = (
    "refund",
    "refunded",
    "cancel",
    "cancelled",
    "closed",
    "return",
    "退款",
    "已退款",
    "取消",
    "已取消",
    "退货",
)


def _is_refund_like_state(order_state: Any) -> bool:
    text = str(order_state or "").strip().lower()
    if not text:
        return False
    return any(keyword in text for keyword in _REFUND_STATE_KEYWORDS)


def _resolve_refund_deduction(
    *,
    total_revenue: Any,
    refund_amount_usd: Any,
    order_state: Any,
) -> float:
    total = _money(total_revenue)
    refund_amount = _money(refund_amount_usd)
    if refund_amount > 0:
        return round(min(refund_amount, total), 2)
    if _is_refund_like_state(order_state):
        return round(total, 2)
    return 0.0


def _derive_refund_status(*, total_revenue: Any, refund_deduction: Any) -> str:
    total = _money(total_revenue)
    refund = _money(refund_deduction)
    if refund <= 0:
        return "none"
    if total > 0 and refund >= total:
        return "full_refund"
    return "partial_refund"


def _derive_order_profit_status(*, line_count: int, ok_count: int, incomplete_count: int) -> str:
    if line_count <= 0 or ok_count + incomplete_count <= 0:
        return "not_computed"
    if incomplete_count <= 0:
        return "ok"
    if ok_count <= 0:
        return "incomplete"
    return "partially_complete"


def _build_order_profit_status_label(profit_status: str, refund_status: str) -> str:
    labels = {
        "ok": "完备",
        "partially_complete": "部分完备",
        "incomplete": "不完备",
        "not_computed": "未核算",
    }
    label = labels.get(profit_status, "未核算")
    if refund_status == "full_refund":
        return label + " / 全额退款"
    if refund_status == "partial_refund":
        return label + " / 部分退款"
    return label
```

- [ ] **Step 4: Run tests**

Run:

```powershell
pytest tests/test_order_analytics_realtime_profit_details.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add appcore/order_analytics/realtime.py tests/test_order_analytics_realtime_profit_details.py
git commit -m "feat(order-analytics): add realtime profit helpers"
```

## Task 3: Backend Order Profit Detail Aggregation

**Files:**
- Modify: `appcore/order_analytics/realtime.py`
- Modify: `tests/test_order_analytics_realtime_profit_details.py`
- Modify: `tests/test_order_analytics_true_roas.py`

- [ ] **Step 1: Add failing backend aggregation tests**

Append to `tests/test_order_analytics_realtime_profit_details.py`:

```python
from datetime import date, datetime

from appcore import order_analytics as oa
from appcore.order_analytics.realtime import _get_realtime_order_profit_details


def test_realtime_order_profit_details_aggregates_costs_refund_and_profit(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{
            "site_code": "newjoy",
            "dxm_package_id": "pkg-1",
            "dxm_order_id": "DXM-1",
            "package_number": "PKG-1",
            "order_state": "paid",
            "buyer_country": "DE",
            "buyer_country_name": "Germany",
            "order_time": datetime(2026, 5, 7, 18, 30),
            "line_count": 2,
            "profit_line_count": 2,
            "profit_ok_count": 2,
            "profit_incomplete_count": 0,
            "units": 3,
            "product_revenue": 100.0,
            "shipping_revenue": 10.0,
            "total_revenue": 110.0,
            "refund_amount_usd": 12.0,
            "purchase_cost": 30.0,
            "logistics_cost": 8.0,
            "ad_cost": 11.0,
            "stored_shopify_fee_total": 5.80,
            "skus": "SKU-A / SKU-B",
            "product_names": "Alpha / Beta",
        }]

    monkeypatch.setattr(oa, "query", fake_query)

    rows = _get_realtime_order_profit_details(
        date(2026, 5, 7),
        datetime(2026, 5, 7, 16, 0),
        datetime(2026, 5, 7, 19, 0),
    )

    assert "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id" in captured["sql"]
    assert "MAX(COALESCE(d.refund_amount_usd, 0)) AS refund_amount_usd" in captured["sql"]
    assert captured["args"] == (date(2026, 5, 7), datetime(2026, 5, 7, 19, 0))

    row = rows[0]
    assert row["dxm_package_id"] == "pkg-1"
    assert row["refund_deduction_usd"] == 12.0
    assert row["purchase_cost_usd"] == 30.0
    assert row["logistics_cost_usd"] == 8.0
    assert row["ad_cost_usd"] == 11.0
    assert row["shopify_platform_fee_usd"] == 3.05
    assert row["international_card_fee_usd"] == 1.10
    assert row["currency_conversion_fee_usd"] == 1.65
    assert row["shopify_fee_total_usd"] == 5.80
    assert row["order_profit_usd"] == 44.2
    assert row["profit_status"] == "ok"
    assert row["refund_status"] == "partial_refund"
    assert row["status_label"] == "完备 / 部分退款"


def test_realtime_order_profit_details_uses_full_refund_for_refund_state_without_amount(monkeypatch):
    def fake_query(sql, args=()):
        return [{
            "site_code": "omurio",
            "dxm_package_id": "pkg-2",
            "dxm_order_id": "DXM-2",
            "package_number": "PKG-2",
            "order_state": "refunded",
            "buyer_country": "US",
            "buyer_country_name": "United States",
            "order_time": datetime(2026, 5, 7, 18, 30),
            "line_count": 1,
            "profit_line_count": 1,
            "profit_ok_count": 1,
            "profit_incomplete_count": 0,
            "units": 1,
            "product_revenue": 50.0,
            "shipping_revenue": 5.0,
            "total_revenue": 55.0,
            "refund_amount_usd": 0,
            "purchase_cost": 10.0,
            "logistics_cost": 4.0,
            "ad_cost": 3.0,
            "stored_shopify_fee_total": 1.68,
            "skus": "SKU-A",
            "product_names": "Alpha",
        }]

    monkeypatch.setattr(oa, "query", fake_query)

    row = _get_realtime_order_profit_details(
        date(2026, 5, 7),
        datetime(2026, 5, 7, 16, 0),
        datetime(2026, 5, 7, 19, 0),
    )[0]

    assert row["refund_deduction_usd"] == 55.0
    assert row["refund_status"] == "full_refund"
    assert row["order_profit_usd"] == -18.68
```

- [ ] **Step 2: Add failing schema tests for overview responses**

Append to `tests/test_order_analytics_true_roas.py`:

```python
def test_get_realtime_roas_overview_range_includes_empty_order_profit_details(monkeypatch):
    def fake_query(sql, args=()):
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-04-29",
        end_date="2026-04-30",
        now=datetime(2026, 5, 1, 12, 0),
    )

    assert result["order_profit_details"] == []


def test_get_realtime_roas_overview_single_day_includes_order_profit_details(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [{"ad_spend": 0, "meta_purchase_value": 0, "meta_purchases": 0, "last_ad_updated_at": None}]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-04-29",
        end_date="2026-04-29",
        now=datetime(2026, 4, 29, 14, 0),
    )

    assert "order_profit_details" in result
    assert result["order_profit_details"] == []
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```powershell
pytest tests/test_order_analytics_realtime_profit_details.py tests/test_order_analytics_true_roas.py::test_get_realtime_roas_overview_range_includes_empty_order_profit_details tests/test_order_analytics_true_roas.py::test_get_realtime_roas_overview_single_day_includes_order_profit_details -q
```

Expected: FAIL because `_get_realtime_order_profit_details()` and `order_profit_details` response fields are not implemented.

- [ ] **Step 4: Implement aggregation and response inclusion**

In `appcore/order_analytics/realtime.py`, add this import:

```python
from .shopify_fee import split_shopify_fee_for_order
```

Add `_get_realtime_order_profit_details()` after the pure helpers:

```python
def _get_realtime_order_profit_details(target: date, day_start: datetime, data_until: datetime) -> list[dict[str, Any]]:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    rows = query(
        "SELECT d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " AS order_time, "
        "COUNT(*) AS line_count, COUNT(p.id) AS profit_line_count, "
        "SUM(CASE WHEN p.status='ok' THEN 1 ELSE 0 END) AS profit_ok_count, "
        "SUM(CASE WHEN p.status='incomplete' THEN 1 ELSE 0 END) AS profit_incomplete_count, "
        "SUM(COALESCE(d.quantity, 0)) AS units, "
        "SUM(COALESCE(d.line_amount, 0)) AS product_revenue, "
        "SUM(COALESCE(d.ship_amount, 0)) AS shipping_revenue, "
        "SUM(COALESCE(d.line_amount, 0)) + SUM(COALESCE(d.ship_amount, 0)) AS total_revenue, "
        "MAX(COALESCE(d.refund_amount_usd, 0)) AS refund_amount_usd, "
        "SUM(COALESCE(p.purchase_usd, 0)) AS purchase_cost, "
        "SUM(COALESCE(p.shipping_cost_usd, 0)) AS logistics_cost, "
        "SUM(COALESCE(p.ad_cost_usd, 0)) AS ad_cost, "
        "SUM(COALESCE(p.shopify_fee_usd, 0)) AS stored_shopify_fee_total, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_sku, '') ORDER BY d.product_sku SEPARATOR ' / ') AS skus, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_name, '') ORDER BY d.product_name SEPARATOR ' / ') AS product_names "
        "FROM dianxiaomi_order_lines d "
        "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE d.site_code IN ('newjoy', 'omurio') "
        "AND d.meta_business_date=%s "
        "AND " + order_time_expr + " <= %s "
        "GROUP BY d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " "
        "ORDER BY order_time DESC, d.dxm_package_id DESC",
        (target, data_until),
    )
    details: list[dict[str, Any]] = []
    for row in rows:
        total_revenue = _money(row.get("total_revenue"))
        refund_deduction = _resolve_refund_deduction(
            total_revenue=total_revenue,
            refund_amount_usd=row.get("refund_amount_usd"),
            order_state=row.get("order_state"),
        )
        fees = split_shopify_fee_for_order(
            amount=total_revenue,
            buyer_country=row.get("buyer_country"),
        )
        purchase_cost = _money(row.get("purchase_cost"))
        logistics_cost = _money(row.get("logistics_cost"))
        ad_cost = _money(row.get("ad_cost"))
        order_profit = round(
            total_revenue
            - refund_deduction
            - purchase_cost
            - logistics_cost
            - float(fees["shopify_platform_fee_usd"])
            - float(fees["international_card_fee_usd"])
            - float(fees["currency_conversion_fee_usd"])
            - ad_cost,
            2,
        )
        profit_line_count = int(row.get("profit_line_count") or 0)
        ok_count = int(row.get("profit_ok_count") or 0)
        incomplete_count = int(row.get("profit_incomplete_count") or 0)
        profit_status = _derive_order_profit_status(
            line_count=profit_line_count,
            ok_count=ok_count,
            incomplete_count=incomplete_count,
        )
        refund_status = _derive_refund_status(
            total_revenue=total_revenue,
            refund_deduction=refund_deduction,
        )
        order_time = row.get("order_time")
        details.append({
            "order_time": order_time,
            "business_hour": _business_hour(order_time, day_start),
            "site_code": row.get("site_code"),
            "dxm_package_id": row.get("dxm_package_id"),
            "dxm_order_id": row.get("dxm_order_id"),
            "package_number": row.get("package_number"),
            "order_state": row.get("order_state"),
            "buyer_country": row.get("buyer_country"),
            "buyer_country_name": row.get("buyer_country_name"),
            "line_count": int(row.get("line_count") or 0),
            "profit_line_count": profit_line_count,
            "profit_ok_count": ok_count,
            "profit_incomplete_count": incomplete_count,
            "units": int(row.get("units") or 0),
            "product_revenue": _money(row.get("product_revenue")),
            "shipping_revenue": _money(row.get("shipping_revenue")),
            "total_revenue": total_revenue,
            "refund_deduction_usd": refund_deduction,
            "purchase_cost_usd": purchase_cost,
            "logistics_cost_usd": logistics_cost,
            "shopify_platform_fee_usd": float(fees["shopify_platform_fee_usd"]),
            "international_card_fee_usd": float(fees["international_card_fee_usd"]),
            "currency_conversion_fee_usd": float(fees["currency_conversion_fee_usd"]),
            "shopify_fee_total_usd": float(fees["shopify_fee_total_usd"]),
            "stored_shopify_fee_total_usd": _money(row.get("stored_shopify_fee_total")),
            "ad_cost_usd": ad_cost,
            "order_profit_usd": order_profit,
            "shopify_tier": fees.get("shopify_tier"),
            "presentment_currency": fees.get("presentment_currency"),
            "profit_status": profit_status,
            "refund_status": refund_status,
            "status_label": _build_order_profit_status_label(profit_status, refund_status),
            "skus": row.get("skus"),
            "product_names": row.get("product_names"),
        })
    return details
```

Update `_build_realtime_overview_for_range()` to include:

```python
"order_profit_details": [],
```

Update the snapshot branch before `return`:

```python
order_profit_details = _get_realtime_order_profit_details(target, day_start, snapshot_at)
```

and include:

```python
"order_profit_details": order_profit_details,
```

Update the fallback branch return to include:

```python
"order_profit_details": _get_realtime_order_profit_details(target, day_start, data_until),
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
pytest tests/test_order_analytics_realtime_profit_details.py tests/test_order_analytics_true_roas.py::test_get_realtime_roas_overview_range_includes_empty_order_profit_details tests/test_order_analytics_true_roas.py::test_get_realtime_roas_overview_single_day_includes_order_profit_details -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add appcore/order_analytics/realtime.py tests/test_order_analytics_realtime_profit_details.py tests/test_order_analytics_true_roas.py
git commit -m "feat(order-analytics): add realtime order profit data"
```

## Task 4: Frontend Realtime Sub-Tab and Rendering

**Files:**
- Modify: `web/templates/order_analytics.html`
- Modify: `tests/test_order_analytics_true_roas.py`

- [ ] **Step 1: Add failing template tests**

Append to `tests/test_order_analytics_true_roas.py`:

```python
def test_realtime_tab_has_order_profit_detail_subtab(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    panel_start = body.index('id="panelRealtime"')
    panel_end = body.index("<!-- ═══════ Tab 0: 产品看板 ═══════ -->", panel_start)
    panel = body[panel_start:panel_end]

    assert 'data-realtime-subtab="profitDetails"' in panel
    assert 'id="realtimeSubProfitDetails"' in panel
    assert 'id="realtimeOrderProfitBody"' in panel


def test_realtime_order_profit_table_shows_every_fee_column(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    panel_start = body.index('id="panelRealtime"')
    panel_end = body.index("<!-- ═══════ Tab 0: 产品看板 ═══════ -->", panel_start)
    panel = body[panel_start:panel_end]

    for label in [
        "退款扣减",
        "采购成本",
        "物流成本",
        "Shopify平台手续费",
        "国际信用卡费",
        "货币转换费",
        "合计手续费",
        "广告费分摊",
        "订单利润",
    ]:
        assert label in panel


def test_realtime_order_profit_renderer_is_wired(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "function renderRealtimeOrderProfitDetails(rows)" in body
    assert "renderRealtimeOrderProfitDetails(data.order_profit_details || [])" in body
```

- [ ] **Step 2: Run template tests and verify they fail**

Run:

```powershell
pytest tests/test_order_analytics_true_roas.py::test_realtime_tab_has_order_profit_detail_subtab tests/test_order_analytics_true_roas.py::test_realtime_order_profit_table_shows_every_fee_column tests/test_order_analytics_true_roas.py::test_realtime_order_profit_renderer_is_wired -q
```

Expected: FAIL because the sub-tab and renderer do not exist.

- [ ] **Step 3: Add the sub-tab button and panel**

In `web/templates/order_analytics.html`, near the existing realtime sub-tab buttons around the `orders/products/campaigns/trend` buttons, add:

```html
<button type="button" class="oar-subtab" data-realtime-subtab="profitDetails">订单盈亏明细</button>
```

After the existing `realtimeSubOrders` panel and before `realtimeSubProducts`, add:

```html
  <div class="oar-subpanel" id="realtimeSubProfitDetails">
    <div class="oa-table-wrap">
      <div class="oa-table-header">
        <div class="oa-table-title">订单盈亏明细</div>
        <div class="oar-note">利润=总销售额-退款扣减-采购成本-物流成本-手续费-广告费分摊，不扣 1% 退货预留</div>
      </div>
      <div class="oa-table-scroll">
        <table class="oa-table oar-compact-table">
          <thead>
            <tr>
              <th>订单时间</th>
              <th>广告日小时</th>
              <th>店铺</th>
              <th>订单号</th>
              <th>国家</th>
              <th>商品</th>
              <th>件数</th>
              <th>总销售额</th>
              <th>退款扣减</th>
              <th>采购成本</th>
              <th>物流成本</th>
              <th>Shopify平台手续费</th>
              <th>国际信用卡费</th>
              <th>货币转换费</th>
              <th>合计手续费</th>
              <th>广告费分摊</th>
              <th>订单利润</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody id="realtimeOrderProfitBody"></tbody>
        </table>
      </div>
    </div>
  </div>
```

- [ ] **Step 4: Wire loading, error, and render call**

In `loadRealtimeSubTabs()`, add:

```javascript
var orderProfitBody = document.getElementById('realtimeOrderProfitBody');
```

After the existing loading rows, add:

```javascript
if (orderProfitBody) orderProfitBody.innerHTML = '<tr><td colspan="18">加载中...</td></tr>';
```

After `renderRealtimeOrders(data.order_details || []);`, add:

```javascript
renderRealtimeOrderProfitDetails(data.order_profit_details || []);
```

In the catch block, add:

```javascript
if (orderProfitBody) orderProfitBody.innerHTML = '<tr><td colspan="18">' + escapeHtml(err.message) + '</td></tr>';
```

- [ ] **Step 5: Add renderer**

Add this function after `renderRealtimeOrders(rows)`:

```javascript
  function renderRealtimeOrderProfitDetails(rows) {
    var body = document.getElementById('realtimeOrderProfitBody');
    if (!body) return;
    body.innerHTML = '';
    (rows || []).forEach(function(row) {
      var tr = document.createElement('tr');
      var profit = Number(row.order_profit_usd || 0);
      var statusText = row.status_label || row.profit_status || '-';
      addTextCell(tr, formatShortDateTime(row.order_time));
      addTextCell(tr, row.business_hour === null || row.business_hour === undefined ? '-' : String(row.business_hour).padStart(2, '0') + ':00');
      addTextCell(tr, row.site_code || '-');
      addTextCell(tr, row.dxm_order_id || row.dxm_package_id || '-', 'oar-id-cell');
      addTextCell(tr, (row.buyer_country_name || row.buyer_country || '-') + (row.buyer_country ? ' / ' + row.buyer_country : ''));
      addTextCell(tr, row.product_names || row.skus || '-', 'oar-product-cell');
      addTextCell(tr, fmtInt(row.units || 0));
      addTextCell(tr, fmtMoney(row.total_revenue));
      addTextCell(tr, fmtMoney(row.refund_deduction_usd));
      addTextCell(tr, fmtMoney(row.purchase_cost_usd));
      addTextCell(tr, fmtMoney(row.logistics_cost_usd));
      addTextCell(tr, fmtMoney(row.shopify_platform_fee_usd));
      addTextCell(tr, fmtMoney(row.international_card_fee_usd));
      addTextCell(tr, fmtMoney(row.currency_conversion_fee_usd));
      addTextCell(tr, fmtMoney(row.shopify_fee_total_usd));
      addTextCell(tr, fmtMoney(row.ad_cost_usd));
      addTextCell(tr, fmtMoney(row.order_profit_usd), profit < 0 ? 'oar-profit-loss' : 'oar-profit-ok');
      addTextCell(tr, statusText);
      body.appendChild(tr);
    });
    if (!body.children.length) {
      body.innerHTML = '<tr><td colspan="18">暂无订单盈亏数据</td></tr>';
    }
  }
```

If `oar-profit-loss` and `oar-profit-ok` classes do not exist, add CSS near realtime table styles:

```css
.oar-profit-loss { color: var(--danger-fg); font-weight: 700; }
.oar-profit-ok { color: var(--success-fg); font-weight: 700; }
```

- [ ] **Step 6: Run template tests**

Run:

```powershell
pytest tests/test_order_analytics_true_roas.py::test_realtime_tab_has_order_profit_detail_subtab tests/test_order_analytics_true_roas.py::test_realtime_order_profit_table_shows_every_fee_column tests/test_order_analytics_true_roas.py::test_realtime_order_profit_renderer_is_wired -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add web/templates/order_analytics.html tests/test_order_analytics_true_roas.py
git commit -m "feat(order-analytics): render realtime order profit tab"
```

## Task 5: Integrated Verification

**Files:**
- No source changes expected.

- [ ] **Step 1: Run focused unit and template tests**

Run:

```powershell
pytest tests/test_shopify_fee.py tests/test_order_analytics_realtime_profit_details.py tests/test_order_analytics_true_roas.py -q
```

Expected: PASS.

- [ ] **Step 2: Run existing order analytics focused tests that do not require local MySQL**

Run:

```powershell
pytest tests/test_order_analytics_dashboard.py tests/test_order_analytics_true_roas.py tests/test_order_profit_aggregation.py tests/test_order_profit_routes.py -q
```

Expected: PASS. If a test attempts to connect to `127.0.0.1:3306`, stop the command and report that project rules forbid Windows local MySQL validation.

- [ ] **Step 3: Inspect final diff**

Run:

```powershell
git diff --stat origin/master...HEAD
git diff --check origin/master...HEAD
```

Expected: changed files are limited to:

```text
appcore/order_analytics/shopify_fee.py
appcore/order_analytics/realtime.py
web/templates/order_analytics.html
tests/test_shopify_fee.py
tests/test_order_analytics_realtime_profit_details.py
tests/test_order_analytics_true_roas.py
docs/superpowers/specs/2026-05-07-order-profit-detail-tab-design.md
docs/superpowers/plans/2026-05-07-order-profit-detail-tab.md
```

Expected: `git diff --check` exits 0.

- [ ] **Step 4: Commit final verification note if needed**

If verification required small test-only adjustments, commit them:

```powershell
git add appcore/order_analytics/shopify_fee.py appcore/order_analytics/realtime.py web/templates/order_analytics.html tests/test_shopify_fee.py tests/test_order_analytics_realtime_profit_details.py tests/test_order_analytics_true_roas.py
git commit -m "test(order-analytics): verify realtime profit details"
```

If no changes are needed, do not create an empty commit.

## Task 6: Optional Server-Side Manual Check After Merge

**Files:**
- No source changes expected.

- [ ] **Step 1: Use test environment only when user asks for test release or verification**

Do not connect to Windows local MySQL. If real data verification is requested, use the project test environment described in `AGENTS.md`: `http://172.16.254.106:8080/`.

- [ ] **Step 2: Browser check**

After code is deployed to the test environment, open:

```text
http://172.16.254.106:8080/order-analytics
```

Expected:

- Realtime dashboard loads.
- Sub-tab `订单盈亏明细` appears next to existing realtime sub-tabs.
- The table shows all fee columns.
- Negative `订单利润` rows are visually distinct.
- Date range controls at the top still only affect top cards; realtime sub-tabs remain current Meta business day.

- [ ] **Step 3: API check**

Request:

```text
GET http://172.16.254.106:8080/order-analytics/realtime-overview
```

Expected response includes:

```json
{
  "order_profit_details": []
}
```

If there are current-day orders and profit lines exist, rows include:

```json
{
  "total_revenue": 0,
  "refund_deduction_usd": 0,
  "purchase_cost_usd": 0,
  "logistics_cost_usd": 0,
  "shopify_platform_fee_usd": 0,
  "international_card_fee_usd": 0,
  "currency_conversion_fee_usd": 0,
  "shopify_fee_total_usd": 0,
  "ad_cost_usd": 0,
  "order_profit_usd": 0,
  "status_label": "完备"
}
```

## Self-Review Notes

Spec coverage:

- New realtime sub-tab: Task 4.
- All frontend fee columns: Task 4 tests and implementation.
- Profit formula deducting ads but not return reserve: Task 3 aggregation tests and implementation.
- Partial refund by `refund_amount_usd`: Task 2 and Task 3 tests.
- Full refund fallback: Task 2 and Task 3 tests.
- Fee split display: Task 1 helper and Task 4 frontend.
- Stable API schema for range mode: Task 3 schema tests.
- No DB migration or scheduled task: File Structure and Task list keep those out of scope.

Placeholder scan: no placeholder steps; each task includes concrete commands and expected results.

Type consistency: field names use the spec names consistently: `refund_deduction_usd`, `purchase_cost_usd`, `logistics_cost_usd`, `shopify_platform_fee_usd`, `international_card_fee_usd`, `currency_conversion_fee_usd`, `shopify_fee_total_usd`, `ad_cost_usd`, `order_profit_usd`, `profit_status`, `refund_status`, `status_label`.
