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
