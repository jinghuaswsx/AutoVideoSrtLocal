from __future__ import annotations

from datetime import datetime

from appcore import order_analytics as oa


def test_get_true_roas_summary_uses_total_revenue_over_ad_spend(monkeypatch):
    calls = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if "FROM dianxiaomi_order_lines" in sql:
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-01"),
                    "order_count": 2,
                    "line_count": 3,
                    "units": 4,
                    "order_revenue": 2000.0,
                    "line_revenue": 1800.0,
                    "shipping_revenue": 200.0,
                }
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-01"),
                    "ad_spend": 1000.0,
                    "meta_purchase_value": 9999.0,
                    "meta_purchases": 99,
                }
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_true_roas_summary("2026-04-01", "2026-04-01")

    assert result["summary"]["order_revenue"] == 2000.0
    assert result["summary"]["shipping_revenue"] == 200.0
    assert result["summary"]["revenue_with_shipping"] == 2200.0
    assert result["summary"]["ad_spend"] == 1000.0
    assert result["summary"]["true_roas"] == 2.2
    assert result["rows"][0]["order_revenue"] == 2000.0
    assert result["rows"][0]["shipping_revenue"] == 200.0
    assert result["rows"][0]["revenue_with_shipping"] == 2200.0
    assert result["rows"][0]["true_roas"] == 2.2
    assert result["rows"][0]["meta_purchase_value"] == 9999.0
    assert any("meta_business_date" in sql for sql, _args in calls)


def test_true_roas_endpoint_returns_json(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_summary(start_date, end_date):
        captured["args"] = (start_date, end_date)
        return {
            "period": {"start": "2026-04-01", "end": "2026-04-30"},
            "rows": [],
            "summary": {"order_revenue": 0, "ad_spend": 0, "true_roas": None},
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_true_roas_summary", fake_summary)

    response = authed_client_no_db.get(
        "/order-analytics/true-roas?start_date=2026-04-01&end_date=2026-04-30"
    )

    assert response.status_code == 200
    assert captured["args"] == ("2026-04-01", "2026-04-30")
    assert response.get_json()["period"]["start"] == "2026-04-01"


def test_get_realtime_roas_overview_summarizes_orders_and_meta_spend(monkeypatch):
    calls = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if "FROM dianxiaomi_order_lines" in sql:
            return [
                {
                    "hour": 13,
                    "order_count": 2,
                    "line_count": 3,
                    "units": 4,
                    "order_revenue": 2000.0,
                    "line_revenue": 1900.0,
                    "shipping_revenue": 100.0,
                    "first_order_at": datetime(2026, 4, 29, 13, 5),
                    "last_order_at": datetime(2026, 4, 29, 13, 40),
                }
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [
                {
                    "ad_spend": 1000.0,
                    "meta_purchase_value": 1200.0,
                    "meta_purchases": 12,
                    "last_ad_updated_at": datetime(2026, 4, 29, 14, 5),
                }
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-04-29",
        now=datetime(2026, 4, 29, 14, 10),
    )

    assert result["summary"]["order_revenue"] == 2000.0
    assert result["summary"]["shipping_revenue"] == 100.0
    assert result["summary"]["revenue_with_shipping"] == 2100.0
    assert result["summary"]["ad_spend"] == 1000.0
    assert result["summary"]["true_roas"] == 2.1
    assert result["summary"]["order_count"] == 2
    assert result["freshness"]["last_ad_updated_at"] == datetime(2026, 4, 29, 14, 5)
    assert result["hourly"][13]["order_count"] == 2
    assert result["scope"]["stores"] == ["newjoy", "omurio"]
    assert result["scope"]["hourly_ad_ready"] is False
    assert any("site_code IN ('newjoy', 'omurio')" in sql for sql, _args in calls)


def test_get_realtime_roas_overview_includes_today_product_sales_stats(monkeypatch):
    def fake_query(sql, args=()):
        if "GROUP BY product_id" in sql:
            assert "meta_business_date=%s" in sql
            assert args[0] == oa._parse_meta_date("2026-04-29")
            return [
                {
                    "product_id": 42,
                    "product_name": "Glow Set",
                    "product_code": "glow-set-rjc",
                    "order_count": 2,
                    "units": 4,
                    "product_net_sales": 2000.0,
                    "shipping": 125.0,
                }
            ]
        if "GROUP BY HOUR" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [{"ad_spend": 0, "meta_purchase_value": 0, "meta_purchases": 0}]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-04-29",
        now=datetime(2026, 4, 29, 14, 10),
    )

    assert result["product_sales_stats"] == [
        {
            "product_id": 42,
            "product_name": "Glow Set",
            "product_code": "glow-set-rjc",
            "order_count": 2,
            "units": 4,
            "product_net_sales": 2000.0,
            "shipping": 125.0,
            "total_sales": 2125.0,
        }
    ]


def test_get_realtime_roas_overview_reports_snapshot_ad_updated_at(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql:
            return []
        if "FROM roi_realtime_daily_snapshots" in sql:
            return [
                {
                    "snapshot_at": datetime(2026, 4, 29, 15, 40),
                    "order_revenue_usd": 10988.71,
                    "shipping_revenue_usd": 3811.74,
                    "ad_spend_usd": 10551.83,
                    "last_order_at": datetime(2026, 4, 29, 15, 34),
                    "order_count": 521,
                    "line_count": 578,
                    "units": 578,
                    "order_data_status": "ok",
                    "ad_data_status": "ok",
                }
            ]
        if "MAX(r.finished_at)" in sql:
            return [{"last_ad_updated_at": datetime(2026, 4, 29, 15, 38)}]
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-04-29",
        now=datetime(2026, 4, 29, 15, 45),
    )

    assert result["freshness"]["last_order_at"] == datetime(2026, 4, 29, 15, 34)
    assert result["freshness"]["last_ad_updated_at"] == datetime(2026, 4, 29, 15, 38)
    assert result["summary"]["ad_spend"] == 10551.83


def test_get_realtime_roas_overview_reports_snapshot_order_updated_at(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql:
            return []
        if "FROM roi_realtime_daily_snapshots" in sql:
            return [
                {
                    "snapshot_at": datetime(2026, 4, 29, 15, 40),
                    "order_revenue_usd": 10988.71,
                    "shipping_revenue_usd": 3811.74,
                    "ad_spend_usd": 10551.83,
                    "last_order_at": datetime(2026, 4, 29, 12, 7),
                    "source_run_id": 310,
                    "order_count": 521,
                    "line_count": 578,
                    "units": 578,
                    "order_data_status": "ok",
                    "ad_data_status": "ok",
                }
            ]
        if "FROM roi_hourly_sync_runs" in sql:
            return [{"last_order_updated_at": datetime(2026, 4, 29, 15, 37)}]
        if "MAX(r.finished_at)" in sql:
            return [{"last_ad_updated_at": datetime(2026, 4, 29, 15, 38)}]
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-04-29",
        now=datetime(2026, 4, 29, 15, 45),
    )

    assert result["freshness"]["last_order_at"] == datetime(2026, 4, 29, 12, 7)
    assert result["freshness"]["last_order_updated_at"] == datetime(2026, 4, 29, 15, 37)
    assert result["freshness"]["last_ad_updated_at"] == datetime(2026, 4, 29, 15, 38)


def test_realtime_roas_endpoint_returns_json(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_overview(date_text=None, *, start_date=None, end_date=None, include_details=False, now=None):
        captured["date"] = date_text
        captured["include_details"] = include_details
        return {
            "period": {"date": "2026-04-29"},
            "summary": {"order_revenue": 2000.0, "ad_spend": 1000.0, "true_roas": 2.0},
            "hourly": [],
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_realtime_roas_overview", fake_overview)

    response = authed_client_no_db.get("/order-analytics/realtime-overview?date=2026-04-29")

    assert response.status_code == 200
    assert captured["date"] == "2026-04-29"
    assert captured["include_details"] is False
    assert response.get_json()["summary"]["true_roas"] == 2.0


def test_data_analysis_page_has_true_roas_tab(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "真实 ROAS" in body
    assert 'data-tab="trueRoas"' in body
    assert 'id="panelTrueRoas"' in body


def test_true_roas_tab_displays_revenue_shipping_and_total_sales(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "statCard('商品销售额', fmtMoney(s.order_revenue))" in body
    assert "statCard('运费', fmtMoney(s.shipping_revenue))" in body
    assert "statCard('总销售额', fmtMoney(s.revenue_with_shipping))" in body
    assert "fmtMoney(row.shipping_revenue)" in body
    assert "fmtMoney(row.revenue_with_shipping)" in body


def test_true_roas_tab_places_meta_result_after_order_count(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)

    header_start = body.index('<div class="oa-table-title">真实 ROAS 日报</div>')
    header_end = body.index('<tbody id="trueRoasTableBody"></tbody>', header_start)
    header = body[header_start:header_end]
    assert (
        header.index("<th>订单数</th>")
        < header.index("<th>Meta 成效</th>")
        < header.index("<th>商品销售额</th>")
    )

    render_start = body.index("function loadTrueRoas()")
    render_end = body.index("function fmtRoasValue", render_start)
    render = body[render_start:render_end]
    assert (
        render.index("row.order_count || 0")
        < render.index("row.meta_purchases || 0")
        < render.index("fmtMoney(row.order_revenue)")
    )


def test_data_analysis_tabs_put_order_and_ads_after_realtime(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "实时大盘" in body
    assert 'data-tab="realtime"' in body
    assert 'id="panelRealtime"' in body
    tab_order = [
        body.index('data-tab="realtime"'),
        body.index('data-tab="dxmOrders"'),
        body.index('data-tab="ads"'),
        body.index('data-tab="dashboard"'),
    ]
    assert tab_order == sorted(tab_order)


def test_realtime_tab_displays_ad_data_update_time(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "广告数据更新时间" in body
    assert 'id="realtimeAdFreshness"' in body
    assert "last_ad_updated_at" in body


def test_realtime_tab_displays_order_data_update_time(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "订单数据更新时间" in body
    assert 'id="realtimeOrderDataUpdatedAt"' in body
    assert "last_order_updated_at" in body


# ───────────────────────────────────────────────────────────
# 实时大盘改版（2026-05-02）：顶部支持时间范围聚合
# ───────────────────────────────────────────────────────────


def test_get_realtime_roas_overview_aggregates_date_range(monkeypatch):
    """范围分支：start_date != end_date 时复用 true_roas 的逐日聚合，只回 summary+freshness+period。"""

    def fake_query(sql, args=()):
        if "FROM dianxiaomi_order_lines" in sql and "GROUP BY meta_business_date" in sql:
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-29"),
                    "order_count": 2, "line_count": 3, "units": 4,
                    "order_revenue": 1000.0, "line_revenue": 900.0, "shipping_revenue": 100.0,
                },
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-30"),
                    "order_count": 3, "line_count": 5, "units": 6,
                    "order_revenue": 2000.0, "line_revenue": 1800.0, "shipping_revenue": 200.0,
                },
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" in sql:
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-29"),
                    "ad_spend": 500.0, "meta_purchase_value": 800.0, "meta_purchases": 5,
                },
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-30"),
                    "ad_spend": 700.0, "meta_purchase_value": 1500.0, "meta_purchases": 9,
                },
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-04-29", end_date="2026-04-30",
        now=datetime(2026, 5, 1, 12, 0),
    )

    assert result["summary"]["order_count"] == 5
    assert result["summary"]["units"] == 10
    assert result["summary"]["order_revenue"] == 3000.0
    assert result["summary"]["shipping_revenue"] == 300.0
    assert result["summary"]["revenue_with_shipping"] == 3300.0
    assert result["summary"]["ad_spend"] == 1200.0
    assert result["summary"]["meta_purchase_value"] == 2300.0
    assert result["summary"]["meta_purchases"] == 14
    assert round(result["summary"]["true_roas"], 4) == round(3300.0 / 1200.0, 4)
    assert round(result["summary"]["meta_roas"], 4) == round(2300.0 / 1200.0, 4)
    # 范围分支不返回逐小时/广告明细
    assert result["hourly"] == []
    assert result["campaigns"] == []
    assert result["roas_points"] == []
    # period 字段使用 start/end 而非 date
    assert result["period"]["start_date"].isoformat() == "2026-04-29"
    assert result["period"]["end_date"].isoformat() == "2026-04-30"
    assert result["period"]["day_definition"] == "meta_ad_platform_business_day_range"


def test_get_realtime_roas_overview_range_includes_empty_order_profit_details(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM dianxiaomi_order_lines" in sql and "GROUP BY meta_business_date" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-04-29",
        end_date="2026-04-30",
        now=datetime(2026, 5, 1, 12, 0),
    )

    assert result["order_profit_details"] == []


def test_get_realtime_roas_overview_range_includes_order_details(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM dianxiaomi_order_lines" in sql and "GROUP BY meta_business_date, site_code" in sql:
            assert args == (oa._parse_meta_date("2026-04-01"), oa._parse_meta_date("2026-04-30"))
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-30"),
                    "site_code": "newjoy",
                    "dxm_package_id": "PKG-RANGE",
                    "dxm_order_id": "DXM-RANGE",
                    "package_number": "PN-RANGE",
                    "order_state": "paid",
                    "buyer_country": "US",
                    "buyer_country_name": "United States",
                    "order_time": datetime(2026, 5, 1, 15, 30),
                    "line_count": 1,
                    "units": 2,
                    "product_revenue": 80.0,
                    "shipping_revenue": 6.0,
                    "total_revenue": 86.0,
                    "skus": "SKU-R",
                    "product_names": "Range Product",
                }
            ]
        if "FROM dianxiaomi_order_lines" in sql and "GROUP BY meta_business_date" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-04-01",
        end_date="2026-04-30",
        include_details=True,
        now=datetime(2026, 5, 1, 12, 0),
    )

    detail = result["order_details"][0]
    assert detail["dxm_package_id"] == "PKG-RANGE"
    assert detail["business_hour"] == 23
    assert detail["total_revenue"] == 86.0


def test_get_realtime_roas_overview_range_includes_order_profit_details(monkeypatch):
    profit_query_args = []

    def fake_query(sql, args=()):
        if "FROM dianxiaomi_order_lines" in sql and "GROUP BY meta_business_date" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" in sql:
            return []
        if "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id" in sql:
            assert "d.meta_business_date >= %s AND d.meta_business_date <= %s" in sql
            assert args[:2] == (oa._parse_meta_date("2026-04-01"), oa._parse_meta_date("2026-04-30"))
            if len(args) == 4:
                assert args[2:] == (100, 0)
            profit_query_args.append(args)
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-30"),
                    "site_code": "newjoy",
                    "dxm_package_id": "PKG-PROFIT-RANGE",
                    "dxm_order_id": "DXM-PROFIT-RANGE",
                    "package_number": "PN-PROFIT-RANGE",
                    "order_state": "paid",
                    "buyer_country": "DE",
                    "buyer_country_name": "Germany",
                    "order_time": datetime(2026, 5, 1, 15, 30),
                    "line_count": 1,
                    "profit_line_count": 1,
                    "profit_ok_count": 1,
                    "profit_incomplete_count": 0,
                    "units": 1,
                    "product_revenue": 100.0,
                    "shipping_revenue": 10.0,
                    "total_revenue": 110.0,
                    "refund_amount_usd": 12.0,
                    "purchase_cost": 30.0,
                    "logistics_cost": 8.0,
                    "ad_cost": 11.0,
                    "stored_shopify_fee_total": 5.8,
                    "skus": "SKU-DE",
                    "product_names": "Range Profit Product",
                }
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-04-01",
        end_date="2026-04-30",
        include_details=True,
        now=datetime(2026, 5, 1, 12, 0),
    )

    detail = result["order_profit_details"][0]
    assert detail["dxm_package_id"] == "PKG-PROFIT-RANGE"
    assert detail["business_hour"] == 23
    assert detail["order_profit_usd"] == 43.2
    assert result["order_profit_details_page"] == {"page": 1, "page_size": 100, "total": 1, "pages": 1}
    assert len(profit_query_args) == 2


def test_get_realtime_roas_overview_single_day_includes_order_profit_details(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql:
            return []
        if "FROM roi_realtime_daily_snapshots" in sql:
            return []
        if "GROUP BY HOUR" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [{"ad_spend": 0, "meta_purchase_value": 0, "meta_purchases": 0, "last_ad_updated_at": None}]
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-04-29",
        now=datetime(2026, 4, 29, 14, 0),
    )

    assert "order_profit_details" in result
    assert result["order_profit_details"] == []


def test_get_realtime_roas_overview_snapshot_includes_order_profit_details(monkeypatch):
    def profit_detail_row():
        return {
            "site_code": "newjoy",
            "dxm_package_id": "PKG-DE",
            "dxm_order_id": "DXM-DE",
            "package_number": "PN-DE",
            "order_state": "paid",
            "buyer_country": "DE",
            "buyer_country_name": "Germany",
            "order_time": datetime(2026, 5, 7, 18, 30),
            "line_count": 2,
            "profit_line_count": 1,
            "profit_ok_count": 1,
            "profit_incomplete_count": 0,
            "units": 3,
            "product_revenue": 100.0,
            "shipping_revenue": 10.0,
            "total_revenue": 110.0,
            "refund_amount_usd": 12.0,
            "purchase_cost": 30.0,
            "logistics_cost": 8.0,
            "ad_cost": 11.0,
            "stored_shopify_fee_total": 5.75,
            "skus": "SKU-A / SKU-B",
            "product_names": "Product A / Product B",
        }

    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql:
            return []
        if "FROM roi_realtime_daily_snapshots" in sql:
            return [
                {
                    "snapshot_at": datetime(2026, 5, 7, 19, 40),
                    "source_run_id": 505,
                    "order_count": 1,
                    "line_count": 2,
                    "units": 3,
                    "order_revenue": 100.0,
                    "line_revenue": 100.0,
                    "shipping_revenue": 10.0,
                    "order_revenue_usd": 100.0,
                    "shipping_revenue_usd": 10.0,
                    "ad_spend_usd": 11.0,
                    "last_order_at": datetime(2026, 5, 7, 18, 30),
                    "order_data_status": "ok",
                    "ad_data_status": "ok",
                }
            ]
        if "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id" in sql:
            return [profit_detail_row()]
        if "FROM roi_hourly_sync_runs" in sql:
            return [{"last_order_updated_at": None}]
        if "MAX(r.finished_at)" in sql:
            return [{"last_ad_updated_at": None}]
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-05-07",
        now=datetime(2026, 5, 7, 20, 0),
    )

    detail = result["order_profit_details"][0]
    assert detail["dxm_package_id"] == "PKG-DE"
    assert detail["order_profit_usd"] == 43.2
    assert detail["refund_status"] == "partial_refund"


def test_get_realtime_roas_overview_fallback_includes_order_profit_details(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql:
            return []
        if "FROM roi_realtime_daily_snapshots" in sql:
            return []
        if "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id" in sql:
            return [
                {
                    "site_code": "omurio",
                    "dxm_package_id": "PKG-US",
                    "dxm_order_id": "DXM-US",
                    "package_number": "PN-US",
                    "order_state": "refunded",
                    "buyer_country": "US",
                    "buyer_country_name": "United States",
                    "order_time": datetime(2026, 5, 7, 17, 15),
                    "line_count": 1,
                    "profit_line_count": 1,
                    "profit_ok_count": 1,
                    "profit_incomplete_count": 0,
                    "units": 1,
                    "product_revenue": 50.0,
                    "shipping_revenue": 5.0,
                    "total_revenue": 55.0,
                    "refund_amount_usd": 0.0,
                    "purchase_cost": 10.0,
                    "logistics_cost": 5.0,
                    "ad_cost": 2.0,
                    "stored_shopify_fee_total": 1.68,
                    "skus": "SKU-US",
                    "product_names": "Product US",
                }
            ]
        if "GROUP BY HOUR" in sql:
            return [
                {
                    "hour": 1,
                    "order_count": 1,
                    "line_count": 1,
                    "units": 1,
                    "order_revenue": 50.0,
                    "line_revenue": 50.0,
                    "shipping_revenue": 5.0,
                    "first_order_at": datetime(2026, 5, 7, 17, 15),
                    "last_order_at": datetime(2026, 5, 7, 17, 15),
                    "last_order_updated_at": datetime(2026, 5, 7, 17, 20),
                }
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return []
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-05-07",
        now=datetime(2026, 5, 7, 20, 0),
    )

    detail = result["order_profit_details"][0]
    assert detail["dxm_package_id"] == "PKG-US"
    assert detail["order_profit_usd"] == -18.68
    assert detail["refund_status"] == "full_refund"


def test_get_realtime_roas_overview_same_day_range_equals_single_day(monkeypatch):
    """start_date == end_date 时走原单日逻辑（保留 hourly / order_details / campaigns）。"""
    captured = {"single_day_called": False}

    def fake_query(sql, args=()):
        captured["single_day_called"] = True
        if "GROUP BY HOUR" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" not in sql:
            return [{"ad_spend": 0, "meta_purchase_value": 0, "meta_purchases": 0, "last_ad_updated_at": None}]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-04-29", end_date="2026-04-29",
        now=datetime(2026, 4, 29, 14, 0),
    )

    # 单日分支会返回 hourly 24 项
    assert isinstance(result.get("hourly"), list)
    assert len(result["hourly"]) == 24
    assert result["period"].get("date") is not None or result["period"].get("start_date") == result["period"].get("end_date")


def test_get_realtime_roas_overview_rejects_inverted_range(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda *a, **kw: [])
    try:
        oa.get_realtime_roas_overview(
            start_date="2026-04-30", end_date="2026-04-29",
            now=datetime(2026, 5, 1, 12, 0),
        )
    except ValueError:
        return
    raise AssertionError("expected ValueError for end_date < start_date")


def test_realtime_overview_endpoint_accepts_start_end_params(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_overview(date_text=None, *, start_date=None, end_date=None, include_details=False, now=None):
        captured["date"] = date_text
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        captured["include_details"] = include_details
        return {
            "period": {"start_date": start_date, "end_date": end_date},
            "summary": {"order_revenue": 3000.0, "ad_spend": 1200.0, "true_roas": 2.5},
            "freshness": {"last_order_at": None, "last_ad_updated_at": None},
            "hourly": [], "order_details": [], "campaigns": [], "roas_points": [],
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_realtime_roas_overview", fake_overview)

    response = authed_client_no_db.get(
        "/order-analytics/realtime-overview?start_date=2026-04-29&end_date=2026-04-30"
    )

    assert response.status_code == 200
    assert captured["start_date"] == "2026-04-29"
    assert captured["end_date"] == "2026-04-30"
    assert captured["include_details"] is False
    assert response.get_json()["summary"]["true_roas"] == 2.5


def test_realtime_overview_endpoint_accepts_include_details(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_overview(date_text=None, *, start_date=None, end_date=None, include_details=False, now=None):
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        captured["include_details"] = include_details
        return {
            "period": {"start_date": start_date, "end_date": end_date},
            "summary": {"order_revenue": 0, "ad_spend": 0, "true_roas": None},
            "freshness": {"last_order_at": None, "last_ad_updated_at": None},
            "hourly": [], "order_details": [], "order_profit_details": [], "campaigns": [], "roas_points": [],
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_realtime_roas_overview", fake_overview)

    response = authed_client_no_db.get(
        "/order-analytics/realtime-overview?start_date=2026-04-29&end_date=2026-04-30&include_details=1"
    )

    assert response.status_code == 200
    assert captured["start_date"] == "2026-04-29"
    assert captured["end_date"] == "2026-04-30"
    assert captured["include_details"] is True


def test_realtime_overview_endpoint_forwards_product_and_pagination(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_overview(
        date_text=None,
        *,
        start_date=None,
        end_date=None,
        include_details=False,
        product_id=None,
        page=1,
        page_size=100,
        now=None,
    ):
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        captured["include_details"] = include_details
        captured["product_id"] = product_id
        captured["page"] = page
        captured["page_size"] = page_size
        return {
            "period": {"start_date": start_date, "end_date": end_date},
            "summary": {"order_revenue": 0, "ad_spend": 0, "true_roas": None},
            "freshness": {"last_order_at": None, "last_ad_updated_at": None},
            "hourly": [],
            "order_details": [],
            "order_profit_details": [],
            "order_profit_details_page": {"page": page, "page_size": page_size, "total": 0, "pages": 0},
            "order_profit_summary": {"order_count": 0},
            "campaigns": [],
            "roas_points": [],
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_realtime_roas_overview", fake_overview)

    response = authed_client_no_db.get(
        "/order-analytics/realtime-overview"
        "?start_date=2026-04-29&end_date=2026-04-30"
        "&include_details=1&product_id=42&page=2&page_size=100"
    )

    assert response.status_code == 200
    assert captured["product_id"] == 42
    assert captured["page"] == 2
    assert captured["page_size"] == 100


# ── 前端模板回归 ─────────────────────────────────────────


def _extract_realtime_panel(body):
    panel_start = body.index('id="panelRealtime"')
    if 'id="panelDashboard"' in body[panel_start:]:
        panel_end = body.index('id="panelDashboard"', panel_start)
    elif 'id="panelDxmOrders"' in body[panel_start:]:
        panel_end = body.index('id="panelDxmOrders"', panel_start)
    else:
        panel_end = len(body)
    return body[panel_start:panel_end]


def test_realtime_tab_has_country_style_time_picker(authed_client_no_db):
    """实时大盘工具栏应含 6 个时间预设 + 自定义日期范围 + 刷新按钮（仿国家看板）。"""
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    panel_start = body.index('id="panelRealtime"')
    panel_end = body.index('id="panelDxmOrders"', panel_start) if 'id="panelDxmOrders"' in body[panel_start:] else len(body)
    panel = body[panel_start:panel_end]

    for preset in ("today", "yesterday", "thisWeek", "lastWeek", "thisMonth", "lastMonth"):
        assert f'data-realtime-range="{preset}"' in panel, f"missing realtime preset {preset}"
    assert 'id="realtimeStartDate"' in panel
    assert 'id="realtimeEndDate"' in panel
    assert 'id="realtimeRefresh"' in panel


def test_realtime_toolbar_uses_query_button_and_product_search(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    panel = _extract_realtime_panel(body)

    assert 'id="realtimeRefresh">查询</button>' in panel
    assert 'id="realtimeProductSearchInput"' in panel
    assert 'id="realtimeProductSearchResults"' in panel
    assert 'id="realtimeProductPicker"' in panel
    assert 'id="realtimeProductClear"' in panel


def test_realtime_tab_defaults_to_meta_business_date(authed_client_no_db):
    """实时大盘的“今天”应按 Meta 广告系统日，而不是北京时间自然日。"""
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    panel_start = body.index('id="panelRealtime"')
    panel_end = body.index('id="panelDxmOrders"', panel_start) if 'id="panelDxmOrders"' in body[panel_start:] else len(body)
    panel = body[panel_start:panel_end]

    assert "北京时间 16:00 切日" in panel
    assert "window.orderAnalyticsMetaCalendar" in body
    assert "function resolveRealtimeRange(range) {\n    return window.orderAnalyticsMetaCalendar.resolveRange(range);\n  }" in body


def test_realtime_tab_drops_blue_primary_card_class(authed_client_no_db):
    """实时大盘 panel 内不应再使用 .oar-card.is-primary（浅蓝底大卡）。"""
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    panel_start = body.index('id="panelRealtime"')
    panel_end = body.index('id="panelDxmOrders"', panel_start) if 'id="panelDxmOrders"' in body[panel_start:] else len(body)
    panel = body[panel_start:panel_end]
    assert "is-primary" not in panel, "实时大盘内仍有蓝底 is-primary 卡片"
    assert "oar-time-rule" not in panel, "实时大盘内仍有蓝底 oar-time-rule 提示框"


def test_realtime_tab_has_product_sales_subtab(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    panel_start = body.index('id="panelRealtime"')
    panel_end = body.index('id="panelDxmOrders"', panel_start) if 'id="panelDxmOrders"' in body[panel_start:] else len(body)
    panel = body[panel_start:panel_end]

    assert 'data-realtime-subtab="products"' in panel
    assert 'id="realtimeSubProducts"' in panel
    assert 'id="realtimeProductSalesBody"' in panel
    assert "<th>订单数</th>" in panel
    assert "<th>销售件数</th>" in panel
    assert "fmtInt(row.order_count)" in body
    assert "fmtInt(row.units)" in body
    assert "renderRealtimeProductSales(data.product_sales_stats || [])" in body


def test_realtime_tab_has_order_profit_detail_subtab(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    panel = _extract_realtime_panel(body)

    assert 'data-realtime-subtab="profitDetails"' in panel
    assert 'id="realtimeSubProfitDetails"' in panel
    assert 'id="realtimeOrderProfitBody"' in panel


def test_realtime_order_profit_table_shows_every_fee_column(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    panel = _extract_realtime_panel(body)

    for column in (
        "订单时间",
        "广告日小时",
        "店铺",
        "订单号",
        "国家",
        "商品",
        "件数",
        "总销售额",
        "退款扣减",
        "采购成本",
        "物流成本",
        "Shopify平台手续费",
        "国际信用卡费",
        "货币转换费",
        "合计手续费",
        "广告费分摊",
        "订单利润",
        "状态",
    ):
        assert f"<th>{column}</th>" in panel


def test_realtime_order_profit_renderer_is_wired(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "function renderRealtimeOrderProfitDetails(rows)" in body
    assert "renderRealtimeOrderProfitDetails(data.order_profit_details || [])" in body


def test_realtime_order_profit_has_summary_and_pagination_controls(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    panel = _extract_realtime_panel(body)

    for element_id in (
        "realtimeProfitSummary",
        "realtimeProfitTotalRevenue",
        "realtimeProfitPurchase",
        "realtimeProfitLogistics",
        "realtimeProfitFee",
        "realtimeProfitAd",
        "realtimeProfitTotal",
        "realtimeProfitPrev",
        "realtimeProfitPageInfo",
        "realtimeProfitNext",
    ):
        assert f'id="{element_id}"' in panel


def test_realtime_subtabs_fetch_current_range(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    subtab_start = body.index("function loadRealtimeSubTabs()")
    subtab_end = body.index("function renderRealtimeOrders(rows)", subtab_start)
    subtab_js = body[subtab_start:subtab_end]

    assert "getRealtimeDateRange()" in subtab_js
    assert "params.set('start_date', range.start)" in subtab_js
    assert "params.set('end_date', range.end)" in subtab_js
    assert "params.set('include_details', '1')" in subtab_js


def test_order_analytics_daily_detail_escapes_country_headers(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    detail_start = body.index("function renderDailyDetail(rows, year, month)")
    detail_end = body.index("// ── 周度视图", detail_start)
    detail_js = body[detail_start:detail_end]

    assert "' + escHtml(c) + '" in detail_js
    assert "' + c + '" not in detail_js


def test_product_profit_report_product_load_error_uses_text_content(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    products_start = body.index("function loadProducts()")
    products_end = body.index("// 按钮 → 打开 dialog", products_start)
    products_js = body[products_start:products_end]

    assert "productSelect.innerHTML = '<option value=\"\">加载失败：' + err + '</option>'" not in products_js
    assert "errorOpt.textContent = '加载失败：' +" in products_js
    assert "productSelect.appendChild(errorOpt)" in products_js


def test_realtime_subtabs_request_product_and_pagination_params(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    subtab_start = body.index("function loadRealtimeSubTabs()")
    subtab_end = body.index("function renderRealtimeOrders(rows)", subtab_start)
    subtab_js = body[subtab_start:subtab_end]

    assert "product_id" in subtab_js
    assert "page" in subtab_js
    assert "page_size" in subtab_js
    assert "100" in subtab_js
