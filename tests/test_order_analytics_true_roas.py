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


def test_realtime_roas_endpoint_returns_json(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_overview(date_text):
        captured["date"] = date_text
        return {
            "period": {"date": "2026-04-29"},
            "summary": {"order_revenue": 2000.0, "ad_spend": 1000.0, "true_roas": 2.0},
            "hourly": [],
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_realtime_roas_overview", fake_overview)

    response = authed_client_no_db.get("/order-analytics/realtime-overview?date=2026-04-29")

    assert response.status_code == 200
    assert captured["date"] == "2026-04-29"
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


def test_data_analysis_page_has_realtime_tab_first(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "实时大盘" in body
    assert 'data-tab="realtime"' in body
    assert 'id="panelRealtime"' in body
    assert body.index('data-tab="realtime"') < body.index('data-tab="dashboard"')


def test_realtime_tab_displays_ad_data_update_time(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "广告数据更新时间" in body
    assert 'id="realtimeAdFreshness"' in body
    assert "last_ad_updated_at" in body
