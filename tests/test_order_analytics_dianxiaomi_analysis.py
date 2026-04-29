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
            10,
            10,
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
        "page_size": 10,
        "total": 3,
        "total_pages": 1,
    }
    assert result["rows"][0]["total_sales"] == 65.0
    assert result["rows"][0]["order_time"] == datetime(2026, 4, 20, 18, 30)
    assert any("FROM dianxiaomi_order_lines" in sql for _kind, sql, _args in calls)


def test_get_dianxiaomi_order_analysis_filters_by_store(monkeypatch):
    calls = []

    def fake_query_one(sql, args=()):
        calls.append(("one", sql, args))
        if "COUNT(DISTINCT dxm_package_id)" in sql:
            return {
                "order_count": 1,
                "units": 2,
                "product_net_sales": 30.0,
                "shipping": 4.0,
            }
        if "COUNT(*) AS total" in sql:
            return {"total": 1}
        return {}

    def fake_query(sql, args=()):
        calls.append(("many", sql, args))
        return []

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_dianxiaomi_order_analysis(
        "2026-04-01",
        "2026-04-30",
        store="newjoy",
    )

    assert result["filters"]["store"] == "newjoy"
    assert all("site_code = %s" in sql for _kind, sql, _args in calls)
    assert calls[0][2] == (
        oa._parse_meta_date("2026-04-01"),
        oa._parse_meta_date("2026-04-30"),
        "newjoy",
    )


def test_get_dianxiaomi_order_analysis_rejects_reversed_range():
    try:
        oa.get_dianxiaomi_order_analysis("2026-04-30", "2026-04-01", page=1, page_size=50)
    except ValueError as exc:
        assert "end_date must be >= start_date" in str(exc)
    else:
        raise AssertionError("expected ValueError")


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
        "month",
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


def test_get_country_dashboard_rejects_invalid_period():
    for period in ("", "year"):
        try:
            oa.get_country_dashboard(period, today=oa._parse_meta_date("2026-04-29"))
        except ValueError as exc:
            assert "period must be one of day/week/month" in str(exc)
        else:
            raise AssertionError(f"expected ValueError for period={period!r}")


def test_get_country_dashboard_accepts_explicit_date_range(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["args"] = args
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_country_dashboard(
        "range",
        start_date="2026-04-01",
        end_date="2026-04-18",
    )

    assert captured["args"] == (
        oa._parse_meta_date("2026-04-01"),
        oa._parse_meta_date("2026-04-18"),
    )
    assert result["period"]["type"] == "range"
    assert result["period"]["start"] == oa._parse_meta_date("2026-04-01")
    assert result["period"]["end"] == oa._parse_meta_date("2026-04-18")
    assert result["period"]["label"] == "2026-04-01 ~ 2026-04-18"


def test_get_country_dashboard_accepts_full_positional_args_and_unknown_country(monkeypatch):
    monkeypatch.setattr(
        oa,
        "query",
        lambda sql, args=(): [
            {
                "buyer_country": "",
                "buyer_country_name": "",
                "order_count": 1,
                "units": 2,
                "product_net_sales": 20.0,
                "shipping": 3.0,
            },
        ],
    )

    result = oa.get_country_dashboard(
        "month",
        2026,
        4,
        None,
        None,
        oa._parse_meta_date("2026-04-29"),
    )

    assert result["period"]["type"] == "month"
    assert result["countries"][0]["display_name"] == "未知"


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


def test_dianxiaomi_orders_endpoint_passes_store_filter(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_analysis(start_date, end_date, page=1, page_size=50, store=None):
        captured.update({
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "page_size": page_size,
            "store": store,
        })
        return {
            "period": {"start_date": start_date, "end_date": end_date},
            "summary": {"total_sales": 0},
            "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0},
            "rows": [],
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_dianxiaomi_order_analysis", fake_analysis)

    response = authed_client_no_db.get(
        "/order-analytics/dianxiaomi-orders?start_date=2026-04-01&end_date=2026-04-30&store=omurio"
    )

    assert response.status_code == 200
    assert captured["store"] == "omurio"


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


def test_country_dashboard_endpoint_accepts_date_range(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_country_dashboard(**kwargs):
        captured.update(kwargs)
        return {
            "period": {"type": kwargs["period"], "start": "2026-04-01", "end": "2026-04-18"},
            "summary": {"total_orders": 0},
            "countries": [],
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_country_dashboard", fake_country_dashboard)

    response = authed_client_no_db.get(
        "/order-analytics/country-dashboard?start_date=2026-04-01&end_date=2026-04-18"
    )

    assert response.status_code == 200
    assert captured["period"] == "range"
    assert captured["start_date"] == "2026-04-01"
    assert captured["end_date"] == "2026-04-18"
    assert "year" not in captured
    assert response.get_json()["period"]["type"] == "range"


def test_dianxiaomi_orders_endpoint_returns_400_for_non_integer_page(authed_client_no_db, monkeypatch):
    called = False

    def fake_analysis(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr("web.routes.order_analytics.oa.get_dianxiaomi_order_analysis", fake_analysis)

    response = authed_client_no_db.get(
        "/order-analytics/dianxiaomi-orders?start_date=2026-04-01&end_date=2026-04-30&page=abc"
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_param"
    assert called is False


def test_dianxiaomi_orders_endpoint_returns_400_for_non_integer_page_size(
    authed_client_no_db,
    monkeypatch,
):
    def fake_analysis(*args, **kwargs):
        raise AssertionError("DAO should not be called")

    monkeypatch.setattr("web.routes.order_analytics.oa.get_dianxiaomi_order_analysis", fake_analysis)

    response = authed_client_no_db.get(
        "/order-analytics/dianxiaomi-orders?start_date=2026-04-01&end_date=2026-04-30&page_size=abc"
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_param"


def test_dianxiaomi_orders_endpoint_500_does_not_leak_exception_detail(
    authed_client_no_db,
    monkeypatch,
):
    def fake_analysis(*args, **kwargs):
        raise RuntimeError("secret db detail")

    monkeypatch.setattr("web.routes.order_analytics.oa.get_dianxiaomi_order_analysis", fake_analysis)

    response = authed_client_no_db.get(
        "/order-analytics/dianxiaomi-orders?start_date=2026-04-01&end_date=2026-04-30"
    )

    assert response.status_code == 500
    data = response.get_json()
    assert data["error"] == "internal_error"
    assert "secret db detail" not in data["detail"]


def test_country_dashboard_endpoint_500_does_not_leak_exception_detail(
    authed_client_no_db,
    monkeypatch,
):
    def fake_country_dashboard(**kwargs):
        raise RuntimeError("secret db detail")

    monkeypatch.setattr("web.routes.order_analytics.oa.get_country_dashboard", fake_country_dashboard)

    response = authed_client_no_db.get(
        "/order-analytics/country-dashboard?period=week&year=2026&week=17"
    )

    assert response.status_code == 500
    data = response.get_json()
    assert data["error"] == "internal_error"
    assert "secret db detail" not in data["detail"]


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
    assert "querySelectorAll('.oad-seg')" not in body
    assert "querySelectorAll('[data-dashboard-range]')" in body
    assert "querySelectorAll('[data-country-range]')" in body


def test_data_analysis_page_fetches_dianxiaomi_and_country_apis(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '"/order-analytics/dianxiaomi-orders?"' in body
    assert '"/order-analytics/country-dashboard?"' in body
    assert "function initDxmOrders()" in body
    assert "function initCountryDashboard()" in body
    assert "function setDxmRange(range)" in body
    assert "function loadDxmOrders(page)" in body
    assert "function renderDxmOrderAnalysis(data)" in body
    assert "function loadCountryDashboard()" in body
    assert "setDxmRange('thisMonth')" in body
    assert "renderCountryDashboard(data)" in body
    assert "sort_by: 'orders'" in body
    assert 'data-country-range="today"' in body
    assert 'data-country-range="yesterday"' in body
    assert 'data-country-range="thisWeek"' in body
    assert 'data-country-range="lastWeek"' in body
    assert 'data-country-range="thisMonth"' in body
    assert 'data-country-range="lastMonth"' in body
    assert 'id="countryStartDate"' in body
    assert 'id="countryEndDate"' in body
    assert "setCountryRange('today'" in body
    assert "start_date: start" in body
    assert "end_date: end" in body


def test_data_analysis_page_has_dianxiaomi_store_filter_below_dates(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="dxmDateFilterRow"' in body
    assert 'id="dxmStoreFilterRow"' in body
    assert 'id="dxmStoreFilter"' in body
    assert 'value="newjoy"' in body
    assert 'value="omurio"' in body
    assert body.index('id="dxmDateFilterRow"') < body.index('id="dxmStoreFilterRow"')
    assert body.index('id="dxmStoreFilter"') < body.index('id="dxmOrderRefresh"')
    assert "var store = document.getElementById('dxmStoreFilter').value;" in body
    assert "params.set('store', store);" in body


def test_data_analysis_page_hardens_dashboard_rendering_and_pagination(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "function escapeOadHtml(value)" in body
    assert "escapeOadHtml(msg || '未知错误')" in body
    assert "escapeOadHtml(data.period.label)" in body
    assert "escapeOadHtml(data.compare_period.label)" in body
    assert "escapeOadHtml(data.country)" in body
    assert "escapeOadHtml(p.product_name || '(no name)')" in body
    assert "escapeOadHtml(p.product_code || '')" in body
    assert "escapeOadHtml(p.product_id)" in body
    assert "escapeOadHtml(String(entry[0] || '').toUpperCase())" in body
    assert "escapeOadHtml(entry[1])" in body
    assert "+ (p.product_name ||" not in body
    assert "+ (p.product_code ||" not in body
    assert "function setDxmPaginationDisabled(disabled)" in body

    load_start = body.index("function loadDxmOrders(page)")
    params_start = body.index("var params = new URLSearchParams", load_start)
    render_start = body.index("function renderDxmOrderAnalysis(data)", load_start)
    catch_start = body.index(".catch(function(err)", load_start)
    pagination_helper_start = body.index("function setDxmPaginationDisabled(disabled)", render_start)

    loading_segment = body[load_start:params_start]
    catch_segment = body[catch_start:render_start]
    render_segment = body[render_start:pagination_helper_start]

    assert "setDxmPaginationDisabled(true);" in loading_segment
    assert "setDxmPaginationDisabled(true);" in catch_segment
    assert "setDxmPaginationDisabled(false);" in render_segment
    assert "prev.disabled = dxmOrderState.page <= 1" in render_segment
    assert "next.disabled = dxmOrderState.page >= dxmOrderState.totalPages" in render_segment
