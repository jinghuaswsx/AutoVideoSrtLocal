# Dianxiaomi Order Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Dianxiaomi-based order analysis tab and country dashboard to `/order-analytics`, while making all dashboard lists default to order-count descending.

**Architecture:** Keep the existing single-page Flask/Jinja order analytics module. Add focused DAO functions in `appcore/order_analytics.py`, expose them through two JSON routes in `web/routes/order_analytics.py`, and extend `web/templates/order_analytics.html` with two new panels that fetch those routes. Tests stay in the existing order analytics test family and use monkeypatched DAO/query calls rather than a local MySQL database.

**Tech Stack:** Flask, Jinja, vanilla JavaScript, PyMySQL helper functions through `appcore.db`, pytest.

---

## File Structure

- Modify: `appcore/order_analytics.py`
  - Add `get_dianxiaomi_order_analysis(...)`.
  - Add `get_country_dashboard(...)`.
  - Change `get_dashboard(...)` default sort key to `orders` and add deterministic tie breakers.
- Modify: `web/routes/order_analytics.py`
  - Add `GET /order-analytics/dianxiaomi-orders`.
  - Add `GET /order-analytics/country-dashboard`.
- Modify: `web/templates/order_analytics.html`
  - Rename Shopify tabs.
  - Add “国家看板” panel.
  - Add Dianxiaomi “订单分析” panel.
  - Add JavaScript state/load/render functions for both panels.
  - Update product dashboard initial `sort_by` to `orders`.
- Create: `tests/test_order_analytics_dianxiaomi_analysis.py`
  - DAO tests for Dianxiaomi order analysis and country dashboard.
  - Route tests for the two new endpoints.
  - Template tests for new labels and panels.
- Modify: `tests/test_order_analytics_ads.py`
  - Add or adjust product dashboard default-sort test.

## Task 1: Dianxiaomi Order Analysis DAO

**Files:**
- Create: `tests/test_order_analytics_dianxiaomi_analysis.py`
- Modify: `appcore/order_analytics.py`

- [ ] **Step 1: Write failing DAO tests**

Create `tests/test_order_analytics_dianxiaomi_analysis.py` with these imports and tests:

```python
from __future__ import annotations

from datetime import date, datetime

from appcore import order_analytics as oa


def test_get_dianxiaomi_order_analysis_summarizes_and_paginates(monkeypatch):
    calls = []

    def fake_query_one(sql, args=()):
        calls.append(("one", sql, args))
        if "COUNT(DISTINCT dxm_package_id)" in sql:
            return {
                "order_count": 2,
                "units": 5,
                "product_net_sales": 100.0,
                "shipping": 12.5,
            }
        if "COUNT(*) AS total" in sql:
            return {"total": 3}
        return {}

    def fake_query(sql, args=()):
        calls.append(("many", sql, args))
        assert "meta_business_date >= %s" in sql
        assert "meta_business_date <= %s" in sql
        assert "ORDER BY order_time DESC" in sql
        assert args == (
            oa._parse_meta_date("2026-04-01"),
            oa._parse_meta_date("2026-04-30"),
            2,
            2,
        )
        return [
            {
                "id": 10,
                "site_code": "newjoy",
                "dxm_shop_name": "NewJoy",
                "dxm_package_id": "pkg-2",
                "dxm_order_id": "order-2",
                "extended_order_id": "ext-2",
                "package_number": "PN-2",
                "order_state": "shipped",
                "buyer_country": "DE",
                "buyer_country_name": "Germany",
                "order_time": datetime(2026, 4, 20, 18, 30),
                "meta_business_date": date(2026, 4, 20),
                "product_name": "Product B",
                "product_sku": "SKU-B",
                "product_sub_sku": "SUB-B",
                "product_display_sku": "DISP-B",
                "variant_text": "Black",
                "quantity": 2,
                "unit_price": 30.0,
                "line_amount": 60.0,
                "ship_amount": 5.0,
                "order_currency": "USD",
            }
        ]

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_dianxiaomi_order_analysis(
        "2026-04-01",
        "2026-04-30",
        page=2,
        page_size=2,
    )

    assert result["period"]["date_field"] == "meta_business_date"
    assert result["summary"] == {
        "total_sales": 112.5,
        "order_count": 2,
        "units": 5,
        "shipping": 12.5,
        "product_net_sales": 100.0,
    }
    assert result["pagination"] == {
        "page": 2,
        "page_size": 2,
        "total": 3,
        "total_pages": 2,
    }
    assert result["rows"][0]["total_sales"] == 65.0
    assert result["rows"][0]["order_time"] == datetime(2026, 4, 20, 18, 30)
    assert any("FROM dianxiaomi_order_lines" in sql for _kind, sql, _args in calls)


def test_get_dianxiaomi_order_analysis_rejects_reversed_range():
    try:
        oa.get_dianxiaomi_order_analysis("2026-04-30", "2026-04-01", page=1, page_size=50)
    except ValueError as exc:
        assert "end_date must be >= start_date" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
pytest tests/test_order_analytics_dianxiaomi_analysis.py::test_get_dianxiaomi_order_analysis_summarizes_and_paginates tests/test_order_analytics_dianxiaomi_analysis.py::test_get_dianxiaomi_order_analysis_rejects_reversed_range -q
```

Expected: both tests fail with `AttributeError: module 'appcore.order_analytics' has no attribute 'get_dianxiaomi_order_analysis'`.

- [ ] **Step 3: Add DAO implementation**

In `appcore/order_analytics.py`, insert this function after `get_true_roas_summary(...)` and before `_coerce_ad_frequency(...)`:

```python
def _dianxiaomi_order_time_expr() -> str:
    return "COALESCE(order_paid_at, paid_at, order_created_at, shipped_at, attribution_time_at)"


def get_dianxiaomi_order_analysis(
    start_date: str,
    end_date: str,
    *,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    start = _parse_iso_date_param(start_date, "start_date")
    end = _parse_iso_date_param(end_date, "end_date")
    if end < start:
        raise ValueError("end_date must be >= start_date")

    page = max(1, int(page or 1))
    page_size = max(10, min(int(page_size or 50), 200))
    offset = (page - 1) * page_size

    where_sql = "FROM dianxiaomi_order_lines WHERE meta_business_date >= %s AND meta_business_date <= %s"
    where_args = (start, end)
    summary_row = query_one(
        "SELECT COUNT(DISTINCT dxm_package_id) AS order_count, "
        "SUM(COALESCE(quantity, 0)) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS product_net_sales, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping "
        + where_sql,
        where_args,
    ) or {}
    total_row = query_one(
        "SELECT COUNT(*) AS total " + where_sql,
        where_args,
    ) or {}

    order_time_expr = _dianxiaomi_order_time_expr()
    rows = query(
        "SELECT id, site_code, dxm_shop_name, dxm_package_id, dxm_order_id, extended_order_id, "
        "package_number, order_state, buyer_country, buyer_country_name, "
        + order_time_expr + " AS order_time, meta_business_date, product_name, product_sku, "
        "product_sub_sku, product_display_sku, variant_text, quantity, unit_price, line_amount, "
        "ship_amount, order_currency "
        + where_sql + " "
        "ORDER BY order_time DESC, dxm_package_id DESC, id DESC LIMIT %s OFFSET %s",
        where_args + (page_size, offset),
    )

    total = int(total_row.get("total") or 0)
    product_net_sales = _money(summary_row.get("product_net_sales"))
    shipping = _money(summary_row.get("shipping"))
    return {
        "period": {
            "start_date": start,
            "end_date": end,
            "date_field": "meta_business_date",
            "timezone": META_ATTRIBUTION_TIMEZONE,
        },
        "summary": {
            "total_sales": _revenue_with_shipping(product_net_sales, shipping),
            "order_count": int(summary_row.get("order_count") or 0),
            "units": int(summary_row.get("units") or 0),
            "shipping": shipping,
            "product_net_sales": product_net_sales,
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        },
        "rows": [
            {
                **row,
                "quantity": int(row.get("quantity") or 0),
                "unit_price": _money(row.get("unit_price")),
                "line_amount": _money(row.get("line_amount")),
                "ship_amount": _money(row.get("ship_amount")),
                "total_sales": _revenue_with_shipping(
                    _money(row.get("line_amount")),
                    _money(row.get("ship_amount")),
                ),
            }
            for row in rows
        ],
    }
```

- [ ] **Step 4: Run focused DAO tests**

Run:

```powershell
pytest tests/test_order_analytics_dianxiaomi_analysis.py::test_get_dianxiaomi_order_analysis_summarizes_and_paginates tests/test_order_analytics_dianxiaomi_analysis.py::test_get_dianxiaomi_order_analysis_rejects_reversed_range -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

Run:

```powershell
git add appcore/order_analytics.py tests/test_order_analytics_dianxiaomi_analysis.py
git commit -m "feat(order-analytics): add dianxiaomi order analysis dao"
```

## Task 2: Country Dashboard DAO and Product Dashboard Sorting

**Files:**
- Modify: `tests/test_order_analytics_dianxiaomi_analysis.py`
- Modify: `tests/test_order_analytics_ads.py`
- Modify: `appcore/order_analytics.py`

- [ ] **Step 1: Add failing country dashboard DAO test**

Append to `tests/test_order_analytics_dianxiaomi_analysis.py`:

```python
def test_get_country_dashboard_groups_by_country_and_sorts_by_orders(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        assert "meta_business_date >= %s" in sql
        assert "meta_business_date <= %s" in sql
        assert "GROUP BY buyer_country, buyer_country_name" in sql
        return [
            {
                "buyer_country": "FR",
                "buyer_country_name": "France",
                "order_count": 2,
                "units": 4,
                "product_net_sales": 50.0,
                "shipping": 5.0,
            },
            {
                "buyer_country": "DE",
                "buyer_country_name": "Germany",
                "order_count": 5,
                "units": 7,
                "product_net_sales": 90.0,
                "shipping": 10.0,
            },
        ]

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_country_dashboard(
        period="month",
        year=2026,
        month=4,
        today=oa._parse_meta_date("2026-04-29"),
    )

    assert result["period"]["type"] == "month"
    assert result["period"]["start"] == date(2026, 4, 1)
    assert result["period"]["end"] == date(2026, 4, 28)
    assert result["countries"][0]["buyer_country"] == "DE"
    assert result["countries"][0]["total_sales"] == 100.0
    assert result["summary"] == {
        "country_count": 2,
        "total_orders": 7,
        "total_units": 11,
        "total_sales": 155.0,
        "shipping": 15.0,
        "product_net_sales": 140.0,
    }
```

- [ ] **Step 2: Add failing product dashboard default sort test**

Append to `tests/test_order_analytics_ads.py`:

```python
def test_get_dashboard_defaults_to_order_count_sort(monkeypatch):
    monkeypatch.setattr(
        "appcore.order_analytics._resolve_period_range",
        lambda *args, **kwargs: (oa._parse_meta_date("2026-04-01"), oa._parse_meta_date("2026-04-30")),
    )
    monkeypatch.setattr(
        "appcore.order_analytics._aggregate_orders_by_product",
        lambda start, end, country=None: {
            1: {"orders": 1, "units": 1, "revenue": 500.0},
            2: {"orders": 3, "units": 3, "revenue": 100.0},
        },
    )
    monkeypatch.setattr("appcore.order_analytics._aggregate_ads_by_product", lambda start, end: {})
    monkeypatch.setattr("appcore.order_analytics._count_media_items_by_product", lambda: {})
    monkeypatch.setattr(
        "appcore.order_analytics._load_products",
        lambda ids, search=None: {
            1: {"id": 1, "name": "Low Orders", "product_code": "low"},
            2: {"id": 2, "name": "High Orders", "product_code": "high"},
        },
    )

    result = oa.get_dashboard(period="day", date_str="2026-04-20", compare=False)

    assert [row["product_id"] for row in result["products"]] == [2, 1]
```

Add this import near the top of `tests/test_order_analytics_ads.py` if it is not already present:

```python
from appcore import order_analytics as oa
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```powershell
pytest tests/test_order_analytics_dianxiaomi_analysis.py::test_get_country_dashboard_groups_by_country_and_sorts_by_orders tests/test_order_analytics_ads.py::test_get_dashboard_defaults_to_order_count_sort -q
```

Expected: country dashboard test fails with `AttributeError`; product dashboard test fails because default sorting is not order-count descending.

- [ ] **Step 4: Add country DAO and default dashboard sorting**

In `appcore/order_analytics.py`, insert after `get_dianxiaomi_order_analysis(...)`:

```python
def _sort_order_dashboard_rows(rows: list[dict], *, name_key: str) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            -(int(row.get("orders") or row.get("order_count") or 0)),
            -(float(row.get("revenue") or row.get("total_sales") or 0)),
            str(row.get(name_key) or "").lower(),
        ),
    )


def get_country_dashboard(
    *,
    period: str,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    date_str: str | None = None,
    today: date | None = None,
) -> dict:
    period = (period or "month").strip().lower()
    if period not in ("day", "week", "month"):
        raise ValueError("period must be one of day/week/month")
    start, end = _resolve_period_range(
        period,
        year=year,
        month=month,
        week=week,
        date_str=date_str,
        today=today,
    )

    rows = query(
        "SELECT buyer_country, buyer_country_name, "
        "COUNT(DISTINCT dxm_package_id) AS order_count, "
        "SUM(COALESCE(quantity, 0)) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS product_net_sales, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping "
        "FROM dianxiaomi_order_lines "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        "GROUP BY buyer_country, buyer_country_name",
        (start, end),
    )

    countries = []
    for row in rows:
        product_net_sales = _money(row.get("product_net_sales"))
        shipping = _money(row.get("shipping"))
        country_code = (row.get("buyer_country") or "").strip()
        country_name = (row.get("buyer_country_name") or "").strip()
        display_name = (
            f"{country_name} / {country_code}"
            if country_name and country_code
            else country_name or country_code or "未知"
        )
        countries.append({
            "buyer_country": country_code,
            "buyer_country_name": country_name,
            "display_name": display_name,
            "order_count": int(row.get("order_count") or 0),
            "units": int(row.get("units") or 0),
            "product_net_sales": product_net_sales,
            "shipping": shipping,
            "total_sales": _revenue_with_shipping(product_net_sales, shipping),
        })

    countries = _sort_order_dashboard_rows(countries, name_key="display_name")
    summary = {
        "country_count": len(countries),
        "total_orders": sum(row["order_count"] for row in countries),
        "total_units": sum(row["units"] for row in countries),
        "total_sales": round(sum(row["total_sales"] for row in countries), 2),
        "shipping": round(sum(row["shipping"] for row in countries), 2),
        "product_net_sales": round(sum(row["product_net_sales"] for row in countries), 2),
    }
    return {
        "period": {
            "type": period,
            "start": start,
            "end": end,
            "label": _format_period_label(start, end, period),
            "date_field": "meta_business_date",
            "timezone": META_ATTRIBUTION_TIMEZONE,
        },
        "summary": summary,
        "countries": countries,
    }
```

In `get_dashboard(...)`, replace the sorting block with:

```python
    # 排序：默认所有看板按订单量倒序；用户点击列头时仍可使用指定字段。
    sort_key = sort_by if sort_by in _DASHBOARD_SORT_FIELDS else "orders"
    reverse = (sort_dir.lower() == "desc")
    if sort_by in _DASHBOARD_SORT_FIELDS:
        rows.sort(
            key=lambda r: (
                r.get(sort_key) is None,
                r.get(sort_key) or 0,
                r.get("orders") or 0,
                r.get("revenue") or 0,
                str(r.get("product_name") or "").lower(),
            ),
            reverse=reverse,
        )
    else:
        rows = _sort_order_dashboard_rows(rows, name_key="product_name")
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
pytest tests/test_order_analytics_dianxiaomi_analysis.py::test_get_country_dashboard_groups_by_country_and_sorts_by_orders tests/test_order_analytics_ads.py::test_get_dashboard_defaults_to_order_count_sort -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit**

Run:

```powershell
git add appcore/order_analytics.py tests/test_order_analytics_dianxiaomi_analysis.py tests/test_order_analytics_ads.py
git commit -m "feat(order-analytics): add country dashboard dao"
```

## Task 3: JSON Routes

**Files:**
- Modify: `tests/test_order_analytics_dianxiaomi_analysis.py`
- Modify: `web/routes/order_analytics.py`

- [ ] **Step 1: Add failing route tests**

Append to `tests/test_order_analytics_dianxiaomi_analysis.py`:

```python
def test_dianxiaomi_orders_endpoint_returns_json(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_analysis(start_date, end_date, page=1, page_size=50):
        captured.update({
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "page_size": page_size,
        })
        return {
            "period": {"start_date": start_date, "end_date": end_date},
            "summary": {"total_sales": 1.0},
            "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0},
            "rows": [],
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_dianxiaomi_order_analysis", fake_analysis)

    response = authed_client_no_db.get(
        "/order-analytics/dianxiaomi-orders?start_date=2026-04-01&end_date=2026-04-30&page=2&page_size=25"
    )

    assert response.status_code == 200
    assert captured == {
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "page": 2,
        "page_size": 25,
    }
    assert response.get_json()["summary"]["total_sales"] == 1.0


def test_dianxiaomi_orders_endpoint_returns_400_for_invalid_date(authed_client_no_db, monkeypatch):
    def fake_analysis(*args, **kwargs):
        raise ValueError("end_date must be >= start_date")

    monkeypatch.setattr("web.routes.order_analytics.oa.get_dianxiaomi_order_analysis", fake_analysis)

    response = authed_client_no_db.get(
        "/order-analytics/dianxiaomi-orders?start_date=2026-04-30&end_date=2026-04-01"
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_param"


def test_country_dashboard_endpoint_returns_json(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_country_dashboard(**kwargs):
        captured.update(kwargs)
        return {
            "period": {"type": kwargs["period"], "start": "2026-04-01", "end": "2026-04-30"},
            "summary": {"total_orders": 0},
            "countries": [],
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_country_dashboard", fake_country_dashboard)

    response = authed_client_no_db.get(
        "/order-analytics/country-dashboard?period=week&year=2026&week=17"
    )

    assert response.status_code == 200
    assert captured["period"] == "week"
    assert captured["year"] == 2026
    assert captured["week"] == 17
    assert response.get_json()["period"]["type"] == "week"
```

- [ ] **Step 2: Run route tests and verify failure**

Run:

```powershell
pytest tests/test_order_analytics_dianxiaomi_analysis.py::test_dianxiaomi_orders_endpoint_returns_json tests/test_order_analytics_dianxiaomi_analysis.py::test_dianxiaomi_orders_endpoint_returns_400_for_invalid_date tests/test_order_analytics_dianxiaomi_analysis.py::test_country_dashboard_endpoint_returns_json -q
```

Expected: all three tests fail with 404 because the routes do not exist.

- [ ] **Step 3: Add routes**

In `web/routes/order_analytics.py`, add these route functions after `true_roas()`:

```python
@bp.route("/order-analytics/dianxiaomi-orders")
@login_required
@admin_required
def dianxiaomi_orders():
    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()
    if not start_date or not end_date:
        return jsonify(error="missing_date", detail="start_date and end_date are required"), 400
    try:
        return jsonify(_json_safe(oa.get_dianxiaomi_order_analysis(
            start_date,
            end_date,
            page=request.args.get("page", 1, type=int),
            page_size=request.args.get("page_size", 50, type=int),
        )))
    except ValueError as exc:
        return jsonify(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("dianxiaomi order analysis query failed: %s", exc)
        return jsonify(error="internal_error", detail=str(exc)), 500


@bp.route("/order-analytics/country-dashboard")
@login_required
@admin_required
def country_dashboard():
    period = (request.args.get("period") or "month").strip().lower()
    if period not in ("day", "week", "month"):
        return jsonify(error="invalid_period", detail="period must be one of day/week/month"), 400
    try:
        return jsonify(_json_safe(oa.get_country_dashboard(
            period=period,
            year=request.args.get("year", type=int),
            month=request.args.get("month", type=int),
            week=request.args.get("week", type=int),
            date_str=request.args.get("date") or None,
        )))
    except ValueError as exc:
        return jsonify(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("country dashboard query failed: %s", exc)
        return jsonify(error="internal_error", detail=str(exc)), 500
```

- [ ] **Step 4: Run route tests**

Run:

```powershell
pytest tests/test_order_analytics_dianxiaomi_analysis.py::test_dianxiaomi_orders_endpoint_returns_json tests/test_order_analytics_dianxiaomi_analysis.py::test_dianxiaomi_orders_endpoint_returns_400_for_invalid_date tests/test_order_analytics_dianxiaomi_analysis.py::test_country_dashboard_endpoint_returns_json -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

Run:

```powershell
git add web/routes/order_analytics.py tests/test_order_analytics_dianxiaomi_analysis.py
git commit -m "feat(order-analytics): expose dianxiaomi analysis APIs"
```

## Task 4: Template Labels and Panel Shells

**Files:**
- Modify: `tests/test_order_analytics_dianxiaomi_analysis.py`
- Modify: `web/templates/order_analytics.html`

- [ ] **Step 1: Add failing template test**

Append to `tests/test_order_analytics_dianxiaomi_analysis.py`:

```python
def test_data_analysis_page_has_shopify_and_dianxiaomi_tabs(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Shopify 订单导入" in body
    assert "Shopify 订单分析" in body
    assert "国家看板" in body
    assert 'data-tab="countryDashboard"' in body
    assert 'id="panelCountryDashboard"' in body
    assert 'data-tab="dxmOrders"' in body
    assert 'id="panelDxmOrders"' in body
    assert body.index('data-tab="countryDashboard"') < body.index('data-tab="trueRoas"')
```

- [ ] **Step 2: Run template test and verify failure**

Run:

```powershell
pytest tests/test_order_analytics_dianxiaomi_analysis.py::test_data_analysis_page_has_shopify_and_dianxiaomi_tabs -q
```

Expected: fails because labels and panels are missing.

- [ ] **Step 3: Rename tabs and add panel HTML**

In `web/templates/order_analytics.html`, replace the tab navigation with:

```html
<div class="oa-tabs">
  <button class="oa-tab active" data-tab="realtime">实时大盘</button>
  <button class="oa-tab" data-tab="dashboard">产品看板</button>
  <button class="oa-tab" data-tab="countryDashboard">国家看板</button>
  <button class="oa-tab" data-tab="trueRoas">真实 ROAS</button>
  <button class="oa-tab" data-tab="import">Shopify 订单导入</button>
  <button class="oa-tab" data-tab="analytics">Shopify 订单分析</button>
  <button class="oa-tab" data-tab="dxmOrders">订单分析</button>
  <button class="oa-tab" data-tab="ads">广告分析</button>
</div>
```

After `</section>` for `panelDashboard`, insert this new panel:

```html
<section class="oa-panel" id="panelCountryDashboard">
  <div class="oad-toolbar">
    <div class="oad-toolbar-row">
      <div class="oad-segmented" role="tablist" aria-label="国家看板时间粒度">
        <button class="oad-seg is-active" data-country-period="month">月</button>
        <button class="oad-seg" data-country-period="week">周</button>
        <button class="oad-seg" data-country-period="day">日</button>
      </div>
      <div class="oad-datepicker">
        <div data-country-period-input="month">
          <select id="countryYear"></select>
          <select id="countryMonth"></select>
        </div>
        <div data-country-period-input="week" hidden>
          <select id="countryWeekYear"></select>
          <select id="countryWeek"></select>
        </div>
        <div data-country-period-input="day" hidden>
          <input type="date" id="countryDate">
        </div>
      </div>
      <button type="button" class="oad-btn-primary" id="countryRefresh">刷新</button>
    </div>
  </div>
  <div id="countrySummary"></div>
  <div id="countryTable"></div>
</section>
```

After `panelAnalytics` and before the modal block, insert:

```html
<div class="oa-panel" id="panelDxmOrders">
  <div class="oar-time-rule">
    <div>
      <strong>日期口径：广告系统日</strong>
      <div class="oar-note">按店小秘订单的 meta_business_date 统计，与真实 ROAS 对齐。</div>
    </div>
    <span id="dxmOrderRangeLabel">本月</span>
  </div>

  <div class="oa-controls">
    <label>开始日期：</label>
    <input type="date" id="dxmStartDate">
    <label>结束日期：</label>
    <input type="date" id="dxmEndDate">
    <button type="button" class="btn btn-default btn-sm" data-dxm-range="today">今天</button>
    <button type="button" class="btn btn-default btn-sm" data-dxm-range="yesterday">昨天</button>
    <button type="button" class="btn btn-default btn-sm" data-dxm-range="thisWeek">本周</button>
    <button type="button" class="btn btn-default btn-sm" data-dxm-range="lastWeek">上周</button>
    <button type="button" class="btn btn-default btn-sm" data-dxm-range="thisMonth">本月</button>
    <button type="button" class="btn btn-default btn-sm" data-dxm-range="lastMonth">上月</button>
    <button type="button" class="btn btn-primary btn-sm" id="dxmOrderRefresh">查询</button>
  </div>

  <div class="oa-stats" id="dxmOrderSummary"></div>
  <div class="oa-table-wrap">
    <div class="oa-table-header">
      <div class="oa-table-title">店小秘订单明细</div>
      <div class="oar-note" id="dxmOrderPageInfo">第 1 页</div>
    </div>
    <div class="oa-table-scroll">
      <table class="oa-table oar-compact-table">
        <thead>
          <tr>
            <th>订单时间</th>
            <th>广告系统日</th>
            <th>店铺</th>
            <th>订单号</th>
            <th>包裹号</th>
            <th>国家</th>
            <th>商品</th>
            <th>SKU</th>
            <th>件数</th>
            <th>商品净销售额</th>
            <th>运费</th>
            <th>总销售额</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody id="dxmOrderBody"></tbody>
      </table>
    </div>
  </div>
  <div class="oa-actions">
    <button type="button" class="btn btn-default btn-sm" id="dxmPrevPage">上一页</button>
    <button type="button" class="btn btn-default btn-sm" id="dxmNextPage">下一页</button>
  </div>
</div>
```

- [ ] **Step 4: Wire tab initialization**

In the tab click handler in `web/templates/order_analytics.html`, add these branches:

```javascript
      } else if (tab.dataset.tab === 'countryDashboard') {
        initCountryDashboard();
      } else if (tab.dataset.tab === 'dxmOrders') {
        initDxmOrders();
```

Keep the existing branches for `analytics`, `ads`, `trueRoas`, and `realtime`.

- [ ] **Step 5: Run template test**

Run:

```powershell
pytest tests/test_order_analytics_dianxiaomi_analysis.py::test_data_analysis_page_has_shopify_and_dianxiaomi_tabs -q
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

Run:

```powershell
git add web/templates/order_analytics.html tests/test_order_analytics_dianxiaomi_analysis.py
git commit -m "feat(order-analytics): add dianxiaomi dashboard panels"
```

## Task 5: Frontend Data Loading and Rendering

**Files:**
- Modify: `tests/test_order_analytics_dianxiaomi_analysis.py`
- Modify: `web/templates/order_analytics.html`

- [ ] **Step 1: Add failing frontend hook test**

Append to `tests/test_order_analytics_dianxiaomi_analysis.py`:

```python
def test_data_analysis_page_fetches_dianxiaomi_and_country_apis(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "/order-analytics/dianxiaomi-orders?" in body
    assert "/order-analytics/country-dashboard?" in body
    assert "function initDxmOrders()" in body
    assert "function initCountryDashboard()" in body
    assert "setDxmRange('thisMonth')" in body
    assert "renderCountryDashboard(data)" in body
```

- [ ] **Step 2: Run frontend hook test and verify failure**

Run:

```powershell
pytest tests/test_order_analytics_dianxiaomi_analysis.py::test_data_analysis_page_fetches_dianxiaomi_and_country_apis -q
```

Expected: fails because the JavaScript functions are not present.

- [ ] **Step 3: Add shared date helpers**

In the main script block in `web/templates/order_analytics.html`, after `localDateString(value)`, add:

```javascript
  function startOfWeek(d) {
    var copy = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var day = copy.getDay() || 7;
    copy.setDate(copy.getDate() - day + 1);
    return copy;
  }

  function endOfWeek(d) {
    var start = startOfWeek(d);
    var end = new Date(start);
    end.setDate(start.getDate() + 6);
    return end;
  }

  function setSelectValue(id, value) {
    var el = document.getElementById(id);
    if (el) el.value = String(value);
  }
```

- [ ] **Step 4: Add Dianxiaomi order JavaScript**

In the main script block, before the `// ── 上传` section, insert:

```javascript
  var dxmOrdersInited = false;
  var dxmOrderState = { page: 1, page_size: 50 };

  function initDxmOrders() {
    if (dxmOrdersInited) {
      loadDxmOrders();
      return;
    }
    dxmOrdersInited = true;
    setDxmRange('thisMonth');
    document.getElementById('dxmOrderRefresh').addEventListener('click', function() {
      dxmOrderState.page = 1;
      loadDxmOrders();
    });
    document.getElementById('dxmPrevPage').addEventListener('click', function() {
      if (dxmOrderState.page > 1) {
        dxmOrderState.page -= 1;
        loadDxmOrders();
      }
    });
    document.getElementById('dxmNextPage').addEventListener('click', function() {
      dxmOrderState.page += 1;
      loadDxmOrders();
    });
    document.querySelectorAll('[data-dxm-range]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        setDxmRange(btn.dataset.dxmRange);
        dxmOrderState.page = 1;
        loadDxmOrders();
      });
    });
    loadDxmOrders();
  }

  function setDxmRange(kind) {
    var today = new Date();
    var start = new Date(today);
    var end = new Date(today);
    if (kind === 'yesterday') {
      start.setDate(today.getDate() - 1);
      end.setDate(today.getDate() - 1);
    } else if (kind === 'thisWeek') {
      start = startOfWeek(today);
      end = endOfWeek(today);
    } else if (kind === 'lastWeek') {
      start = startOfWeek(today);
      start.setDate(start.getDate() - 7);
      end = new Date(start);
      end.setDate(start.getDate() + 6);
    } else if (kind === 'lastMonth') {
      start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
      end = new Date(today.getFullYear(), today.getMonth(), 0);
    } else if (kind === 'thisMonth') {
      start = new Date(today.getFullYear(), today.getMonth(), 1);
      end = new Date(today.getFullYear(), today.getMonth() + 1, 0);
    }
    document.getElementById('dxmStartDate').value = localDateString(start);
    document.getElementById('dxmEndDate').value = localDateString(end);
    document.getElementById('dxmOrderRangeLabel').textContent =
      localDateString(start) + ' ~ ' + localDateString(end);
  }

  function loadDxmOrders() {
    var start = document.getElementById('dxmStartDate').value;
    var end = document.getElementById('dxmEndDate').value;
    var body = document.getElementById('dxmOrderBody');
    body.innerHTML = '<tr><td colspan="13">加载中...</td></tr>';
    var params = new URLSearchParams({
      start_date: start,
      end_date: end,
      page: dxmOrderState.page,
      page_size: dxmOrderState.page_size
    });
    fetch('/order-analytics/dianxiaomi-orders?' + params.toString())
      .then(function(r) {
        if (!r.ok) return r.json().then(function(data) { throw new Error(data.detail || data.error || '查询失败'); });
        return r.json();
      })
      .then(function(data) {
        renderDxmOrderAnalysis(data);
      })
      .catch(function(err) {
        body.innerHTML = '<tr><td colspan="13">' + escapeHtml(err.message) + '</td></tr>';
      });
  }

  function renderDxmOrderAnalysis(data) {
    var s = data.summary || {};
    document.getElementById('dxmOrderSummary').innerHTML =
      statCard('总销售额', fmtMoney(s.total_sales)) +
      statCard('订单总量', fmtInt(s.order_count)) +
      statCard('销售件数', fmtInt(s.units)) +
      statCard('运费', fmtMoney(s.shipping)) +
      statCard('商品净销售额', fmtMoney(s.product_net_sales));
    var page = data.pagination || {};
    document.getElementById('dxmOrderPageInfo').textContent =
      '第 ' + (page.page || 1) + ' / ' + (page.total_pages || 0) + ' 页，共 ' + fmtInt(page.total || 0) + ' 行';
    document.getElementById('dxmPrevPage').disabled = (page.page || 1) <= 1;
    document.getElementById('dxmNextPage').disabled = (page.page || 1) >= (page.total_pages || 0);
    dxmOrderState.page = page.page || dxmOrderState.page;

    var body = document.getElementById('dxmOrderBody');
    body.innerHTML = '';
    (data.rows || []).forEach(function(row) {
      var tr = document.createElement('tr');
      addTextCell(tr, formatShortDateTime(row.order_time));
      addTextCell(tr, String(row.meta_business_date || '').slice(0, 10));
      addTextCell(tr, [row.site_code, row.dxm_shop_name].filter(Boolean).join(' / ') || '-');
      addTextCell(tr, row.dxm_order_id || row.extended_order_id || row.dxm_package_id || '-', 'oar-id-cell');
      addTextCell(tr, row.package_number || '-', 'oar-id-cell');
      addTextCell(tr, (row.buyer_country_name || row.buyer_country || '-') + (row.buyer_country ? ' / ' + row.buyer_country : ''));
      addTextCell(tr, row.product_name || '-', 'oar-product-cell');
      addTextCell(tr, [row.product_sku, row.product_sub_sku, row.product_display_sku].filter(Boolean).join(' / ') || '-', 'oar-id-cell');
      addTextCell(tr, fmtInt(row.quantity || 0));
      addTextCell(tr, fmtMoney(row.line_amount));
      addTextCell(tr, fmtMoney(row.ship_amount));
      addTextCell(tr, fmtMoney(row.total_sales));
      addTextCell(tr, row.order_state || '-');
      body.appendChild(tr);
    });
    if (!body.children.length) {
      body.innerHTML = '<tr><td colspan="13">暂无订单数据</td></tr>';
    }
  }
```

- [ ] **Step 5: Add country dashboard JavaScript**

In the main script block, after the Dianxiaomi order functions, insert:

```javascript
  var countryDashboardInited = false;
  var countryState = {
    period: 'month',
    year: new Date().getFullYear(),
    month: new Date().getMonth() + 1,
    week: null,
    date: null
  };

  function initCountryDashboard() {
    if (!countryDashboardInited) {
      countryDashboardInited = true;
      fillCountryPeriodInputs();
      document.querySelectorAll('[data-country-period]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          countryState.period = btn.dataset.countryPeriod;
          document.querySelectorAll('[data-country-period]').forEach(function(item) {
            item.classList.toggle('is-active', item === btn);
          });
          document.querySelectorAll('[data-country-period-input]').forEach(function(box) {
            box.hidden = box.dataset.countryPeriodInput !== countryState.period;
          });
          loadCountryDashboard();
        });
      });
      ['countryYear','countryMonth','countryWeekYear','countryWeek','countryDate'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('change', loadCountryDashboard);
      });
      document.getElementById('countryRefresh').addEventListener('click', loadCountryDashboard);
    }
    loadCountryDashboard();
  }

  function fillCountryPeriodInputs() {
    var cur = new Date().getFullYear();
    ['countryYear', 'countryWeekYear'].forEach(function(id) {
      var select = document.getElementById(id);
      select.innerHTML = '';
      for (var y = cur - 2; y <= cur; y++) {
        select.add(new Option(y + ' 年', y));
      }
      select.value = String(cur);
    });
    var month = document.getElementById('countryMonth');
    month.innerHTML = '';
    for (var m = 1; m <= 12; m++) {
      month.add(new Option(m + ' 月', m));
    }
    month.value = String(new Date().getMonth() + 1);
    var week = document.getElementById('countryWeek');
    week.innerHTML = '';
    for (var w = 1; w <= 53; w++) {
      week.add(new Option('第 ' + w + ' 周', w));
    }
    document.getElementById('countryDate').value = localDateString(new Date());
  }

  function loadCountryDashboard() {
    var params = new URLSearchParams({period: countryState.period});
    if (countryState.period === 'month') {
      params.set('year', document.getElementById('countryYear').value);
      params.set('month', document.getElementById('countryMonth').value);
    } else if (countryState.period === 'week') {
      params.set('year', document.getElementById('countryWeekYear').value);
      params.set('week', document.getElementById('countryWeek').value);
    } else {
      params.set('date', document.getElementById('countryDate').value);
    }
    document.getElementById('countryTable').innerHTML = '<div class="oad-loading">加载中...</div>';
    fetch('/order-analytics/country-dashboard?' + params.toString())
      .then(function(r) {
        if (!r.ok) return r.json().then(function(data) { throw new Error(data.detail || data.error || '查询失败'); });
        return r.json();
      })
      .then(function(data) {
        renderCountryDashboard(data);
      })
      .catch(function(err) {
        document.getElementById('countryTable').innerHTML =
          '<div class="oad-error">加载失败：' + escapeHtml(err.message) + '</div>';
      });
  }

  function renderCountryDashboard(data) {
    var s = data.summary || {};
    document.getElementById('countrySummary').innerHTML =
      '<div class="oad-summary">' +
        '<div class="oad-summary-item"><strong>' + fmtInt(s.total_orders) + '</strong>订单</div>' +
        '<div class="oad-summary-item"><strong>' + fmtInt(s.total_units) + '</strong>件数</div>' +
        '<div class="oad-summary-item"><strong>' + fmtMoney(s.product_net_sales) + '</strong>商品净销售额</div>' +
        '<div class="oad-summary-item"><strong>' + fmtMoney(s.shipping) + '</strong>运费</div>' +
        '<div class="oad-summary-item"><strong>' + fmtMoney(s.total_sales) + '</strong>总销售额</div>' +
      '</div>' +
      '<div class="oad-period-label">时段: ' + ((data.period || {}).label || '') + ' · 日期口径: 广告系统日</div>';
    var rows = data.countries || [];
    if (!rows.length) {
      document.getElementById('countryTable').innerHTML = '<div class="oad-empty">该时段暂无国家订单数据</div>';
      return;
    }
    document.getElementById('countryTable').innerHTML =
      '<table class="oad-table"><thead><tr>' +
      '<th>国家</th><th>订单量</th><th>销售件数</th><th>商品净销售额</th><th>运费</th><th>总销售额</th>' +
      '</tr></thead><tbody>' +
      rows.map(function(row) {
        return '<tr>' +
          '<td>' + escapeHtml(row.display_name || '未知') + '</td>' +
          '<td>' + fmtInt(row.order_count) + '</td>' +
          '<td>' + fmtInt(row.units) + '</td>' +
          '<td>' + fmtMoney(row.product_net_sales) + '</td>' +
          '<td>' + fmtMoney(row.shipping) + '</td>' +
          '<td>' + fmtMoney(row.total_sales) + '</td>' +
        '</tr>';
      }).join('') +
      '</tbody></table>';
  }
```

- [ ] **Step 6: Set product dashboard default frontend sort**

In `web/templates/order_analytics.html`, change the `oad.state` default from:

```javascript
      sort_by: '',
```

to:

```javascript
      sort_by: 'orders',
```

- [ ] **Step 7: Run frontend hook test**

Run:

```powershell
pytest tests/test_order_analytics_dianxiaomi_analysis.py::test_data_analysis_page_fetches_dianxiaomi_and_country_apis -q
```

Expected: `1 passed`.

- [ ] **Step 8: Commit**

Run:

```powershell
git add web/templates/order_analytics.html tests/test_order_analytics_dianxiaomi_analysis.py
git commit -m "feat(order-analytics): load dianxiaomi dashboards"
```

## Task 6: Focused Regression Suite

**Files:**
- Modify only files needed to fix failures from this task.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
pytest tests/test_order_analytics_dianxiaomi_analysis.py tests/test_order_analytics_true_roas.py tests/test_order_analytics_dianxiaomi.py tests/test_order_analytics_ads.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run syntax check for touched Python files**

Run:

```powershell
python -m py_compile appcore/order_analytics.py web/routes/order_analytics.py
```

Expected: command exits with code 0 and prints no errors.

- [ ] **Step 3: Inspect final diff**

Run:

```powershell
git diff --stat
git diff --check
```

Expected: `git diff --check` exits with code 0. Diff should only include:

- `appcore/order_analytics.py`
- `web/routes/order_analytics.py`
- `web/templates/order_analytics.html`
- `tests/test_order_analytics_dianxiaomi_analysis.py`
- `tests/test_order_analytics_ads.py`

- [ ] **Step 4: Commit any verification fixes**

If Step 1 or Step 2 required a code fix, commit the fix:

```powershell
git add appcore/order_analytics.py web/routes/order_analytics.py web/templates/order_analytics.html tests/test_order_analytics_dianxiaomi_analysis.py tests/test_order_analytics_ads.py
git commit -m "test(order-analytics): cover dianxiaomi dashboards"
```

If no fix was required, do not create an empty commit.

## Task 7: Test Environment Verification Notes

**Files:**
- No planned file edits.

- [ ] **Step 1: Record local verification output**

Run:

```powershell
git status --short --branch
pytest tests/test_order_analytics_dianxiaomi_analysis.py tests/test_order_analytics_true_roas.py tests/test_order_analytics_dianxiaomi.py tests/test_order_analytics_ads.py -q
```

Expected:

- Branch is `codex/dianxiaomi-order-analysis`.
- Working tree is clean or only contains intentional uncommitted verification notes.
- Pytest exits with code 0.

- [ ] **Step 2: Prepare server validation checklist**

Use this checklist after code is deployed to the test environment `http://172.30.254.14:8080/`:

```text
1. Open http://172.30.254.14:8080/order-analytics as admin.
2. Confirm tabs show 国家看板, Shopify 订单导入, Shopify 订单分析, 订单分析.
3. Open 国家看板 and switch 月 / 周 / 日.
4. Confirm countries are ordered by 订单量 descending.
5. Open 订单分析 and click 今天、昨天、本周、上周、本月、上月.
6. Confirm summary cards update and order rows paginate.
7. Open 产品看板 and confirm first load is ordered by 订单 descending.
```

Do not touch production `http://172.30.254.14/` unless the user explicitly asks for a production release.
