from __future__ import annotations

from datetime import date, datetime

from appcore import order_analytics as oa
from appcore.order_analytics import realtime as realtime_oa


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
        if "FROM dianxiaomi_order_lines d" in sql and "GROUP BY d.meta_business_date" in sql:
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


def test_realtime_range_summary_uses_canonical_meta_purchase_value(monkeypatch):
    """范围分支 Meta ROAS 必须用 roas_purchase 纠正平均购买价值误写。

    Docs-anchor:
    docs/superpowers/specs/2026-05-09-ads-purchase-value-order-fallback-design.md#7-2026-05-20-实时大盘-meta-roas-口径修复
    """

    def fake_query(sql, args=()):
        if "FROM dianxiaomi_order_lines d" in sql and "GROUP BY d.meta_business_date" in sql:
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-05-07"),
                    "order_count": 78,
                    "line_count": 79,
                    "units": 91,
                    "order_revenue": 1918.09,
                    "line_revenue": 1918.09,
                    "shipping_revenue": 634.29,
                    "last_order_at": datetime(2026, 5, 8, 15, 57),
                    "last_order_updated_at": datetime(2026, 5, 8, 16, 30),
                }
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" in sql:
            assert "roas_purchase" in sql
            assert "purchase_value_usd" in sql
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-05-07"),
                    "ad_spend": 1460.07,
                    "meta_purchase_value": 1752.08,
                    "meta_purchases": 71,
                    "last_ad_updated_at": datetime(2026, 5, 8, 16, 30),
                }
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-05-07",
        end_date="2026-05-08",
        now=datetime(2026, 5, 9, 12, 0),
    )

    assert result["summary"]["ad_spend"] == 1460.07
    assert result["summary"]["meta_purchase_value"] == 1752.08
    assert result["summary"]["meta_roas"] == round(1752.08 / 1460.07, 4)


def test_realtime_range_summary_uses_order_fallback_when_meta_purchase_columns_missing(monkeypatch):
    """Meta 日终行缺购买价值/ROAS 列时，实时大盘 Meta ROAS 分子用既有订单兜底。

    Docs-anchor:
    docs/superpowers/specs/2026-05-09-ads-purchase-value-order-fallback-design.md#7-2026-05-20-实时大盘-meta-roas-口径修复
    """

    target = oa._parse_meta_date("2026-05-07")

    def fake_query(sql, args=()):
        if "FROM dianxiaomi_order_lines d" in sql and "GROUP BY d.meta_business_date" in sql:
            return [
                {
                    "meta_business_date": target,
                    "order_count": 10,
                    "line_count": 10,
                    "units": 10,
                    "order_revenue": 300.0,
                    "line_revenue": 300.0,
                    "shipping_revenue": 0.0,
                    "last_order_at": datetime(2026, 5, 8, 10, 0),
                    "last_order_updated_at": datetime(2026, 5, 8, 10, 5),
                }
            ]
        if "SELECT meta_business_date, ad_account_id, matched_product_code" in sql:
            return [
                {
                    "meta_business_date": target,
                    "ad_account_id": "1861285821213497",
                    "matched_product_code": "sonic-lens-refresher-rjc",
                    "product_id": 316,
                    "spend_usd": 100.0,
                    "purchase_value_usd": 0.0,
                    "result_count": 10,
                    "updated_at": datetime(2026, 5, 8, 16, 30),
                }
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" in sql:
            return [
                {
                    "meta_business_date": target,
                    "ad_spend": 100.0,
                    "meta_purchase_value": 0.0,
                    "meta_purchases": 10,
                    "last_ad_updated_at": datetime(2026, 5, 8, 16, 30),
                }
            ]
        return []

    def fake_fill_purchase_value_from_orders(rows, *, level, start_date, end_date, accounts_loader=None):
        assert level == "campaign"
        assert start_date == target
        assert end_date == target
        assert len(rows) == 1
        rows[0]["purchase_value_usd"] = 300.0
        return {"fallback_row_count": 1, "fallback_revenue_total_usd": 300.0}

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(oa, "fill_purchase_value_from_orders", fake_fill_purchase_value_from_orders)

    result = oa.get_realtime_roas_overview(
        start_date="2026-05-07",
        end_date="2026-05-08",
        now=datetime(2026, 5, 10, 12, 0),
    )

    assert result["summary"]["ad_spend"] == 100.0
    assert result["summary"]["meta_purchase_value"] == 300.0
    assert result["summary"]["meta_roas"] == 3.0
    assert result["summary"]["meta_purchase_fallback_row_count"] == 1


def test_realtime_single_day_summary_includes_meta_roas(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql or "FROM roi_realtime_daily_snapshots" in sql:
            return []
        if "GROUP BY HOUR" in sql:
            return []
        if "SELECT meta_business_date, ad_account_id, matched_product_code" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [
                {
                    "ad_spend": 200.0,
                    "meta_purchase_value": 300.0,
                    "meta_purchases": 6,
                    "last_ad_updated_at": datetime(2026, 5, 8, 16, 30),
                }
            ]
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-05-07",
        now=datetime(2026, 5, 10, 12, 0),
    )

    assert result["summary"]["meta_roas"] == 1.5


def test_realtime_range_summary_uses_realtime_ads_for_current_business_day(monkeypatch):
    """本周/本月范围包含当前业务日时，广告费必须补实时表兜底。"""
    current_business_date = oa._parse_meta_date("2026-05-19")
    latest_at = datetime(2026, 5, 20, 11, 40)
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if "FROM dianxiaomi_order_lines d" in sql and "GROUP BY d.meta_business_date" in sql:
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-05-18"),
                    "order_count": 185,
                    "line_count": 192,
                    "units": 208,
                    "order_revenue": 3519.74,
                    "line_revenue": 3519.74,
                    "shipping_revenue": 1332.94,
                    "last_order_at": datetime(2026, 5, 19, 15, 58, 7),
                    "last_order_updated_at": datetime(2026, 5, 20, 11, 40, 6),
                },
                {
                    "meta_business_date": current_business_date,
                    "order_count": 155,
                    "line_count": 157,
                    "units": 165,
                    "order_revenue": 2777.13,
                    "line_revenue": 2777.13,
                    "shipping_revenue": 1087.91,
                    "last_order_at": datetime(2026, 5, 20, 11, 23, 57),
                    "last_order_updated_at": datetime(2026, 5, 20, 11, 40, 12),
                },
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" in sql:
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-05-18"),
                    "ad_spend": 3569.70,
                    "meta_purchase_value": 4389.72,
                    "meta_purchases": 169,
                    "last_ad_updated_at": datetime(2026, 5, 19, 16, 30, 19),
                }
            ]
        if (
            "FROM meta_ad_realtime_daily_campaign_metrics" in sql
            and "MAX(snapshot_at) AS latest_at" in sql
        ):
            assert args[:2] == (current_business_date, latest_at)
            return [{"ad_account_id": "1861285821213497", "latest_at": latest_at}]
        if (
            "FROM meta_ad_realtime_daily_campaign_metrics" in sql
            and "campaign_id, campaign_name" in sql
        ):
            return [
                {
                    "ad_account_id": "1861285821213497",
                    "ad_account_name": "Newjoyloo",
                    "campaign_id": "cmp-current",
                    "campaign_name": "current-day-campaign",
                    "normalized_campaign_code": "current-day-campaign",
                    "result_count": 149,
                    "spend_usd": 3014.44,
                    "purchase_value_usd": 3714.57,
                    "impressions": 1000,
                    "clicks": 42,
                }
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-05-18",
        end_date="2026-05-24",
        now=latest_at,
    )

    assert result["summary"]["ad_spend"] == 6584.14
    assert result["summary"]["meta_purchase_value"] == 8104.29
    assert result["summary"]["meta_purchases"] == 318
    assert result["summary"]["revenue_with_shipping"] == 8717.72
    assert result["summary"]["true_roas"] == round(8717.72 / 6584.14, 4)
    assert result["summary"]["meta_roas"] == round(8104.29 / 6584.14, 4)
    assert any("FROM meta_ad_realtime_daily_campaign_metrics" in sql for sql, _ in calls)


def test_realtime_range_summary_uses_canonical_profit_revenue(monkeypatch):
    """历史范围实时大盘收入必须与订单利润核算共用 order_profit_lines 口径。"""

    def fake_query(sql, args=()):
        if "FROM dianxiaomi_order_lines d" in sql and "LEFT JOIN order_profit_lines p" in sql and "GROUP BY d.meta_business_date" in sql:
            assert "COALESCE(p.line_amount_usd, d.line_amount, 0)" in sql
            assert "COALESCE(p.shipping_allocated_usd, d.ship_amount, 0)" in sql
            assert args == (oa._parse_meta_date("2026-04-01"), oa._parse_meta_date("2026-04-30"))
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-30"),
                    "order_count": 1,
                    "line_count": 1,
                    "units": 1,
                    "order_revenue": 236175.27,
                    "line_revenue": 236175.27,
                    "shipping_revenue": 72383.13,
                    "last_order_at": None,
                    "last_order_updated_at": None,
                }
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" in sql:
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-30"),
                    "ad_spend": 194528.80,
                    "meta_purchase_value": 289969.53,
                    "meta_purchases": 10299,
                    "last_ad_updated_at": None,
                }
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-04-01",
        end_date="2026-04-30",
        now=datetime(2026, 5, 1, 12, 0),
    )

    assert result["summary"]["order_revenue"] == 236175.27
    assert result["summary"]["shipping_revenue"] == 72383.13
    assert result["summary"]["revenue_with_shipping"] == 308558.4


def test_get_realtime_roas_overview_range_includes_empty_order_profit_details(monkeypatch):
    def fake_query(sql, args=()):
        if (
            "FROM dianxiaomi_order_lines d" in sql
            and "GROUP BY d.meta_business_date" in sql
            and "profit_line_count" not in sql
        ):
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


def test_get_realtime_roas_overview_range_can_return_profit_summary_without_details(monkeypatch):
    def fake_query(sql, args=()):
        if (
            "FROM dianxiaomi_order_lines d" in sql
            and "GROUP BY d.meta_business_date" in sql
            and "profit_line_count" not in sql
        ):
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" in sql:
            return []
        return []

    profit_calls = []

    def fake_profit_rows(start, end, *, product_id=None, page=None, page_size=None, site_codes=None):
        profit_calls.append(
            {
                "start": start,
                "end": end,
                "product_id": product_id,
                "page": page,
                "page_size": page_size,
            }
        )
        return [
            {
                "total_revenue": 100.0,
                "refund_deduction_usd": 5.0,
                "purchase_cost_usd": 20.0,
                "purchase_estimate_usd": 0.0,
                "logistics_cost_usd": 10.0,
                "logistics_estimate_usd": 0.0,
                "shopify_fee_total_usd": 3.0,
                "ad_cost_usd": 12.0,
                "purchase_cost_missing": False,
                "logistics_cost_missing": False,
            }
        ]

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(
        realtime_oa,
        "_get_realtime_order_profit_details_for_range",
        fake_profit_rows,
    )

    result = oa.get_realtime_roas_overview(
        start_date="2026-04-29",
        end_date="2026-04-30",
        include_profit_summary=True,
        now=datetime(2026, 5, 1, 12, 0),
    )

    assert result["order_profit_details"] == []
    assert result["order_profit_details_page"] == {
        "page": 1,
        "page_size": 100,
        "total": 1,
        "pages": 1,
    }
    assert result["order_profit_summary"]["profit_with_estimate_usd"] == 50.0
    assert profit_calls == [
        {
            "start": oa._parse_meta_date("2026-04-29"),
            "end": oa._parse_meta_date("2026-04-30"),
            "product_id": None,
            "page": None,
            "page_size": None,
        }
    ]


def test_get_realtime_roas_overview_range_subtracts_unallocated_ad_spend(monkeypatch):
    """range 模式：summary.ad_spend 大于已分摊 → 利润扣未分摊。"""
    def fake_query(sql, args=()):
        if "FROM dianxiaomi_order_lines d" in sql and "GROUP BY d.meta_business_date" in sql:
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-29"),
                    "order_count": 1,
                    "line_count": 1,
                    "units": 1,
                    "order_revenue": 100.0,
                    "line_revenue": 100.0,
                    "shipping_revenue": 0.0,
                    "last_order_at": None,
                    "last_order_updated_at": None,
                }
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" in sql:
            return [
                {
                    "meta_business_date": oa._parse_meta_date("2026-04-29"),
                    "ad_spend": 80.0,
                    "meta_purchase_value": 0.0,
                    "meta_purchases": 0,
                    "last_ad_updated_at": None,
                }
            ]
        return []

    def fake_profit_rows(start, end, *, product_id=None, page=None, page_size=None, site_codes=None):
        return [
            {
                "total_revenue": 100.0,
                "refund_deduction_usd": 0.0,
                "purchase_cost_usd": 10.0,
                "purchase_estimate_usd": 0.0,
                "logistics_cost_usd": 5.0,
                "logistics_estimate_usd": 0.0,
                "shopify_fee_total_usd": 3.0,
                "ad_cost_usd": 20.0,
                "purchase_cost_missing": False,
                "logistics_cost_missing": False,
            }
        ]

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(
        realtime_oa,
        "_get_realtime_order_profit_details_for_range",
        fake_profit_rows,
    )

    result = oa.get_realtime_roas_overview(
        start_date="2026-04-29",
        end_date="2026-04-30",
        include_profit_summary=True,
        now=datetime(2026, 5, 1, 12, 0),
    )

    profit_summary = result["order_profit_summary"]
    # ad_spend 总 80，已分摊 20 → 未分摊 60；profit = 100 - 10 - 5 - 3 - 20 - 60 = 2
    assert profit_summary["ad_cost_usd"] == 20.0
    assert profit_summary["unallocated_ad_spend_usd"] == 60.0
    assert profit_summary["total_ad_spend_usd"] == 80.0
    assert profit_summary["profit_with_estimate_usd"] == 2.0
    # 同时保证利润 ≤ 销售额 − 总广告 spend，即不会出现"利润 > 销售额 − ad_spend"
    revenue_minus_ad = (
        result["summary"]["revenue_with_shipping"] - result["summary"]["ad_spend"]
    )
    assert profit_summary["profit_with_estimate_usd"] <= revenue_minus_ad + 1e-9


def test_get_realtime_roas_overview_range_includes_order_details(monkeypatch):
    def fake_query(sql, args=()):
        if (
            "FROM dianxiaomi_order_lines d" in sql
            and "GROUP BY d.meta_business_date, d.site_code" in sql
            and "profit_line_count" not in sql
        ):
            assert args[:2] == (oa._parse_meta_date("2026-04-01"), oa._parse_meta_date("2026-04-30"))
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
        if "FROM dianxiaomi_order_lines d" in sql and "GROUP BY d.meta_business_date" in sql:
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
        if (
            "FROM dianxiaomi_order_lines d" in sql
            and "GROUP BY d.meta_business_date" in sql
            and "profit_line_count" not in sql
        ):
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql and "GROUP BY meta_business_date" in sql:
            return []
        if "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id" in sql and "profit_line_count" in sql:
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
                    "return_reserve_usd": 1.1,
                    "purchase_cost": 30.0,
                    "purchase_estimate": 0.0,
                    "logistics_cost": 8.0,
                    "logistics_estimate": 0.0,
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
    assert detail["order_profit_usd"] == 54.1
    assert result["order_profit_details_page"] == {"page": 1, "page_size": 100, "total": 1, "pages": 1}
    assert len(profit_query_args) == 2


def test_realtime_order_profit_missing_field_like_patterns_escape_pymysql_wildcards(monkeypatch):
    captured_sql = []

    def fake_query(sql, args=()):
        if "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id" in sql:
            captured_sql.append(sql)
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    target = oa._parse_meta_date("2026-04-29")
    realtime_oa._get_realtime_order_profit_details(
        target,
        datetime(2026, 4, 29, 16, 0),
        datetime(2026, 4, 30, 15, 59),
        page=1,
        page_size=100,
    )
    realtime_oa._get_realtime_order_profit_details_for_range(
        oa._parse_meta_date("2026-04-01"),
        oa._parse_meta_date("2026-04-30"),
        page=1,
        page_size=100,
    )

    assert len(captured_sql) == 2
    for sql in captured_sql:
        assert "LIKE '%%purchase_price%%'" in sql
        assert "LIKE '%%shipping_cost%%'" in sql
        assert "LIKE '%%packet_cost%%'" in sql


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


def test_get_realtime_roas_overview_current_day_ignores_future_roas_nodes(monkeypatch):
    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql:
            rows = [
                {
                    "node_hour": 8,
                    "node_at": datetime(2026, 5, 10, 0, 20),
                    "order_count": 12,
                    "units": 18,
                    "order_revenue_usd": 240.0,
                    "shipping_revenue_usd": 20.0,
                    "ad_spend_usd": 130.0,
                    "true_roas": 2.0,
                    "order_data_status": "ok",
                    "ad_data_status": "ok",
                },
                {
                    "node_hour": 23,
                    "node_at": datetime(2026, 5, 10, 15, 59),
                    "order_count": 99,
                    "units": 120,
                    "order_revenue_usd": 3000.0,
                    "shipping_revenue_usd": 99.0,
                    "ad_spend_usd": 100.0,
                    "true_roas": 30.99,
                    "order_data_status": "ok",
                    "ad_data_status": "ok",
                },
            ]
            if len(args) >= 2:
                return [row for row in rows if row["node_at"] <= args[1]]
            return rows
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
        "2026-05-09",
        now=datetime(2026, 5, 10, 1, 23),
    )

    assert result["roas_points"][8]["true_roas"] == 2.0
    assert result["roas_points"][23]["true_roas"] is None


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
            "return_reserve_usd": 1.1,
            "purchase_cost": 30.0,
            "purchase_estimate": 0.0,
            "logistics_cost": 8.0,
            "logistics_estimate": 0.0,
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
    assert detail["order_profit_usd"] == 54.15
    assert detail["refund_status"] == "partial_refund"


def test_get_realtime_roas_overview_prefers_latest_order_snapshot_when_ad_pending(monkeypatch):
    old_ad_ok_snapshot = {
        "id": 631,
        "snapshot_at": datetime(2026, 5, 7, 22, 40),
        "source_run_id": 646,
        "order_count": 9,
        "line_count": 10,
        "units": 15,
        "order_revenue_usd": 500.60,
        "shipping_revenue_usd": 103.19,
        "ad_spend_usd": 106.10,
        "last_order_at": datetime(2026, 5, 7, 22, 30, 7),
        "order_data_status": "ok",
        "ad_data_status": "ok",
    }
    latest_order_snapshot = {
        "id": 633,
        "snapshot_at": datetime(2026, 5, 8, 11, 0),
        "source_run_id": 684,
        "order_count": 57,
        "line_count": 58,
        "units": 69,
        "order_revenue_usd": 1570.64,
        "shipping_revenue_usd": 481.78,
        "ad_spend_usd": 0,
        "last_order_at": datetime(2026, 5, 8, 10, 28, 24),
        "order_data_status": "ok",
        "ad_data_status": "pending_source",
    }

    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql:
            return []
        if "FROM roi_realtime_daily_snapshots" in sql:
            if "ORDER BY CASE WHEN ad_data_status='ok'" in sql:
                return [old_ad_ok_snapshot, latest_order_snapshot]
            return [latest_order_snapshot, old_ad_ok_snapshot]
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
        now=datetime(2026, 5, 8, 11, 5),
    )

    assert result["summary"]["order_count"] == 57
    assert result["summary"]["units"] == 69
    assert result["summary"]["ad_data_status"] == "pending_source"
    assert result["period"]["data_until_at"] == latest_order_snapshot["snapshot_at"]
    assert result["snapshots"][0]["id"] == 633


def test_realtime_current_business_day_product_filter_uses_realtime_campaign_snapshot(monkeypatch):
    target = oa._parse_meta_date("2026-05-07")
    snapshot_at = datetime(2026, 5, 8, 13, 20)

    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql:
            return []
        if "FROM roi_realtime_daily_snapshots" in sql:
            return [
                {
                    "id": 673,
                    "snapshot_at": snapshot_at,
                    "source_run_id": 690,
                    "order_count": 61,
                    "line_count": 62,
                    "units": 73,
                    "order_revenue_usd": 1635.17,
                    "shipping_revenue_usd": 514.17,
                    "ad_spend_usd": 1173.81,
                    "last_order_at": datetime(2026, 5, 8, 12, 58),
                    "order_data_status": "ok",
                    "ad_data_status": "ok",
                }
            ]
        if "GROUP BY ad_account_id" in sql and "FROM meta_ad_realtime_daily_campaign_metrics" in sql:
            return [{"ad_account_id": "1861285821213497", "latest_at": snapshot_at}]
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql:
            return [
                {
                    "ad_account_id": "1861285821213497",
                    "ad_account_name": "Newjoyloo",
                    "campaign_id": "cmp-42",
                    "campaign_name": "glow-go-insect-set-rjc",
                    "normalized_campaign_code": "glow-go-insect-set-rjc",
                    "result_count": 4,
                    "spend_usd": 50.0,
                    "purchase_value_usd": 80.0,
                    "impressions": 1000,
                    "clicks": 20,
                },
                {
                    "ad_account_id": "1861285821213497",
                    "ad_account_name": "Newjoyloo",
                    "campaign_id": "cmp-99",
                    "campaign_name": "other-product-rjc",
                    "normalized_campaign_code": "other-product-rjc",
                    "result_count": 7,
                    "spend_usd": 70.0,
                    "purchase_value_usd": 120.0,
                    "impressions": 2000,
                    "clicks": 30,
                },
            ]
        if "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS order_revenue" in sql and "FROM dianxiaomi_order_lines d" in sql:
            assert "d.meta_business_date=%s" in sql
            assert args[0] == target
            assert args[1] == snapshot_at
            assert args[2] == 42
            return [
                {
                    "order_count": 1,
                    "line_count": 1,
                    "units": 2,
                    "order_revenue": 100.0,
                    "line_revenue": 100.0,
                    "shipping_revenue": 10.0,
                    "first_order_at": datetime(2026, 5, 8, 12, 10),
                    "last_order_at": datetime(2026, 5, 8, 12, 10),
                    "last_order_updated_at": datetime(2026, 5, 8, 12, 12),
                }
            ]
        if "FROM roi_hourly_sync_runs" in sql:
            return [{"last_order_updated_at": datetime(2026, 5, 8, 13, 18)}]
        if "MAX(r.finished_at)" in sql:
            return [{"last_ad_updated_at": datetime(2026, 5, 8, 13, 19)}]
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [{"ad_spend": 0, "meta_purchase_value": 0, "meta_purchases": 0, "last_ad_updated_at": None}]
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        return []

    def fake_resolve(code):
        if code == "glow-go-insect-set-rjc":
            return {"id": 42, "product_code": "glow-go-insect-set-rjc"}
        if code == "other-product-rjc":
            return {"id": 99, "product_code": "other-product-rjc"}
        return None

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(realtime_oa, "resolve_ad_product_match", fake_resolve, raising=False)

    result = oa.get_realtime_roas_overview(
        start_date="2026-05-07",
        end_date="2026-05-07",
        product_id=42,
        now=datetime(2026, 5, 8, 13, 25),
    )

    assert result["scope"]["ad_source"] == "meta_ad_realtime_daily_campaign_metrics"
    assert result["summary"]["order_count"] == 1
    assert result["summary"]["ad_spend"] == 50.0
    assert result["summary"]["meta_purchase_value"] == 80.0
    assert result["summary"]["meta_purchases"] == 4
    assert result["summary"]["true_roas"] == 2.2
    assert [campaign["normalized_campaign_code"] for campaign in result["campaigns"]] == [
        "glow-go-insect-set-rjc"
    ]


def test_realtime_recent_closed_business_day_uses_snapshot_when_daily_ads_missing(monkeypatch):
    target = oa._parse_meta_date("2026-05-07")
    snapshot_at = datetime(2026, 5, 8, 15, 40)

    def fake_query(sql, args=()):
        if "COUNT(*) AS n" in sql and "FROM meta_ad_daily_campaign_metrics" in sql:
            assert args == (target,)
            return [{"n": 0}]
        if "FROM roi_daily_roas_nodes" in sql:
            return []
        if "FROM roi_realtime_daily_snapshots" in sql:
            return [
                {
                    "id": 777,
                    "snapshot_at": snapshot_at,
                    "source_run_id": 701,
                    "order_count": 78,
                    "line_count": 79,
                    "units": 91,
                    "order_revenue_usd": 1918.09,
                    "shipping_revenue_usd": 634.29,
                    "ad_spend_usd": 12680.47,
                    "last_order_at": datetime(2026, 5, 8, 15, 45),
                    "order_data_status": "ok",
                    "ad_data_status": "ok",
                }
            ]
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql:
            return [
                {
                    "ad_account_id": "1861285821213497",
                    "ad_account_name": "Newjoyloo",
                    "campaign_id": "cmp-1",
                    "campaign_name": "newjoyloo-rjc",
                    "normalized_campaign_code": "newjoyloo-rjc",
                    "result_count": 585,
                    "spend_usd": 12680.47,
                    "purchase_value_usd": 0,
                    "impressions": 1000,
                    "clicks": 20,
                }
            ]
        if "FROM roi_hourly_sync_runs" in sql:
            return [{"last_order_updated_at": datetime(2026, 5, 8, 16, 7)}]
        if "MAX(r.finished_at)" in sql:
            return [{"last_ad_updated_at": datetime(2026, 5, 8, 15, 40)}]
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date="2026-05-07",
        end_date="2026-05-07",
        now=datetime(2026, 5, 8, 16, 12),
    )

    assert result["scope"]["ad_source"] == "roi_realtime_daily_snapshots"
    assert result["summary"]["ad_spend"] == 12680.47
    assert result["summary"]["order_count"] == 78
    assert result["snapshots"][0]["id"] == 777


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
                    "return_reserve_usd": 0.55,
                    "purchase_cost": 10.0,
                    "purchase_estimate": 0.0,
                    "logistics_cost": 5.0,
                    "logistics_estimate": 0.0,
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
    assert detail["order_profit_usd"] == 35.77
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


def test_realtime_overview_endpoint_attaches_data_quality(authed_client_no_db, monkeypatch):
    """Docs-anchor: docs/analytics-data-quality-guardrails.md"""

    def fake_overview(date_text=None, *, start_date=None, end_date=None, include_details=False, now=None):
        return {
            "period": {"date": "2026-04-29"},
            "scope": {"ad_source": "meta_ad_realtime_daily_campaign_metrics"},
            "summary": {"order_revenue": 0, "ad_spend": 0, "true_roas": None},
            "freshness": {"last_order_at": None, "last_ad_updated_at": None},
            "hourly": [], "order_details": [], "campaigns": [], "roas_points": [],
        }

    captured_kwargs = {}

    def fake_build_for_realtime_overview(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "warning",
            "source_mode": "realtime_snapshot",
            "business_date_from": "2026-04-29",
            "business_date_to": "2026-04-29",
            "checks": [],
            "warnings": [],
            "errors": [],
            "watermarks": {},
            "generated_at": "2026-04-29T18:00:00",
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_realtime_roas_overview", fake_overview)
    monkeypatch.setattr(
        "web.routes.order_analytics.dq.build_for_realtime_overview",
        fake_build_for_realtime_overview,
    )

    response = authed_client_no_db.get("/order-analytics/realtime-overview?date=2026-04-29")
    payload = response.get_json()
    assert payload["data_quality"]["source_mode"] == "realtime_snapshot"
    assert captured_kwargs["source_mode"] == "realtime_snapshot"
    # business_date 来自 period.date
    from datetime import date as _date
    assert captured_kwargs["business_date"] == _date(2026, 4, 29)


def test_realtime_overview_endpoint_marks_meta_purchase_fallback_warning(
    authed_client_no_db,
    monkeypatch,
):
    """Meta purchase 订单兜底不能被 data_quality 静默标为 ok。"""

    def fake_overview(date_text=None, *, start_date=None, end_date=None, include_details=False, now=None):
        return {
            "period": {"date": "2026-05-07"},
            "scope": {"ad_source": "meta_ad_daily_campaign_metrics"},
            "summary": {
                "order_revenue": 0,
                "ad_spend": 100,
                "meta_purchase_value": 300,
                "true_roas": None,
                "meta_purchase_fallback_row_count": 1,
                "meta_purchase_fallback_revenue_total_usd": 300.0,
            },
            "freshness": {"last_order_at": None, "last_ad_updated_at": None},
            "hourly": [],
            "order_details": [],
            "campaigns": [],
            "roas_points": [],
        }

    def fake_build_for_realtime_overview(**kwargs):
        return {
            "status": "ok",
            "source_mode": "daily_final",
            "business_date_from": "2026-05-07",
            "business_date_to": "2026-05-07",
            "checks": [],
            "warnings": [],
            "errors": [],
            "watermarks": {},
            "generated_at": "2026-05-08T18:00:00",
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_realtime_roas_overview", fake_overview)
    monkeypatch.setattr(
        "web.routes.order_analytics.dq.build_for_realtime_overview",
        fake_build_for_realtime_overview,
    )

    response = authed_client_no_db.get("/order-analytics/realtime-overview?date=2026-05-07")
    payload = response.get_json()

    assert payload["data_quality"]["status"] == "warning"
    assert payload["data_quality"]["warnings"][0]["code"] == "meta_purchase_value_order_fallback"


def test_realtime_overview_endpoint_attaches_range_data_quality(
    authed_client_no_db,
    monkeypatch,
):
    """范围模式的 data_quality 需要覆盖完整起止业务日和 mixed 源模式。"""

    def fake_overview(date_text=None, *, start_date=None, end_date=None, include_details=False, now=None):
        return {
            "period": {"start_date": "2026-05-18", "end_date": "2026-05-24"},
            "scope": {"ad_source": "mixed"},
            "summary": {"order_revenue": 0, "ad_spend": 0, "true_roas": None},
            "freshness": {"last_order_at": None, "last_ad_updated_at": None},
            "hourly": [],
            "order_details": [],
            "campaigns": [],
            "roas_points": [],
        }

    captured_kwargs = {}

    def fake_build_for_realtime_overview(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "warning",
            "source_mode": "mixed",
            "business_date_from": "2026-05-18",
            "business_date_to": "2026-05-24",
            "checks": [],
            "warnings": [],
            "errors": [],
            "watermarks": {},
            "generated_at": "2026-05-20T12:00:00",
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_realtime_roas_overview", fake_overview)
    monkeypatch.setattr(
        "web.routes.order_analytics.dq.build_for_realtime_overview",
        fake_build_for_realtime_overview,
    )

    response = authed_client_no_db.get(
        "/order-analytics/realtime-overview?start_date=2026-05-18&end_date=2026-05-24"
    )

    from datetime import date as _date

    assert response.status_code == 200
    assert captured_kwargs["business_date"] == _date(2026, 5, 18)
    assert captured_kwargs["business_date_to"] == _date(2026, 5, 24)
    assert captured_kwargs["source_mode"] == "mixed"


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


def test_realtime_overview_endpoint_accepts_include_profit_summary(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_overview(
        date_text=None,
        *,
        start_date=None,
        end_date=None,
        include_details=False,
        include_profit_summary=False,
        now=None,
    ):
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        captured["include_details"] = include_details
        captured["include_profit_summary"] = include_profit_summary
        return {
            "period": {"start_date": start_date, "end_date": end_date},
            "summary": {"order_revenue": 0, "ad_spend": 0, "true_roas": None},
            "freshness": {"last_order_at": None, "last_ad_updated_at": None},
            "hourly": [],
            "order_details": [],
            "order_profit_details": [],
            "order_profit_summary": {"profit_with_estimate_usd": 0.0},
            "campaigns": [],
            "roas_points": [],
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_realtime_roas_overview", fake_overview)

    response = authed_client_no_db.get(
        "/order-analytics/realtime-overview"
        "?start_date=2026-04-29&end_date=2026-04-30&include_profit_summary=1"
    )

    assert response.status_code == 200
    assert captured["start_date"] == "2026-04-29"
    assert captured["end_date"] == "2026-04-30"
    assert captured["include_details"] is False
    assert captured["include_profit_summary"] is True


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


def test_embedded_product_profit_report_defaults_to_meta_business_date(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "var today = window.orderAnalyticsMetaCalendar.today();" in body
    assert "var today = new Date();\n    var from = new Date(today.getTime() - 30 * 24 * 3600 * 1000);" not in body


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


def test_realtime_summary_has_profit_card_and_time_row(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    panel = _extract_realtime_panel(body)

    summary_start = panel.index('id="realtimeSummary"')
    summary_end = panel.index('id="realtimeSubOrders"', summary_start)
    summary = panel[summary_start:summary_end]

    assert 'class="oar-summary-row oar-summary-row-main"' in summary
    assert 'class="oar-summary-row oar-summary-row-time"' in summary
    assert 'id="realtimeProfit"' in summary
    assert 'id="realtimeProfitSub"' in summary

    main_row_start = summary.index('class="oar-summary-row oar-summary-row-main"')
    time_row_start = summary.index('class="oar-summary-row oar-summary-row-time"')
    main_row = summary[main_row_start:time_row_start]
    time_row = summary[time_row_start:]

    assert main_row.index("商品件数") < main_row.index("利润")
    assert "订单最新时间" not in main_row
    assert "订单最新时间" in time_row
    assert "订单数据更新时间" in time_row
    assert "广告数据更新时间" in time_row
    assert "数据快照时间" in time_row

    top_cards_start = body.index("function loadRealtimeTopCards()")
    top_cards_end = body.index("// ── 子 tab：跟随当前选择的广告系统日范围", top_cards_start)
    top_cards_js = body[top_cards_start:top_cards_end]
    assert "params.set('include_profit_summary', '1')" in top_cards_js
    assert "document.getElementById('realtimeProfit')" in top_cards_js
    assert "profitEl.textContent" in top_cards_js


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
        "realtimeProfitUnallocatedAd",
        "realtimeProfitTotal",
        "realtimeProfitReconcile",
        "realtimeProfitPrev",
        "realtimeProfitPageInfo",
        "realtimeProfitNext",
    ):
        assert f'id="{element_id}"' in panel
    # 对账提示 JS 必须实际写入 reconcile 元素，避免摆好坑位却没逻辑。
    body = response.get_data(as_text=True)
    assert "realtimeProfitReconcile" in body
    assert "SUM(逐行利润)" in body


def test_realtime_unallocated_ad_card_is_clickable_and_campaign_filter_is_wired(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    panel = _extract_realtime_panel(body)

    assert 'id="realtimeProfitUnallocatedAdCard"' in panel
    assert 'data-realtime-campaign-filter="unallocated"' in panel
    assert "function showRealtimeUnallocatedCampaigns()" in body
    assert "setRealtimeSubtab('campaigns')" in body
    assert "realtimeState.campaignFilter = 'unallocated'" in body
    assert "renderRealtimeCampaigns(realtimeLastCampaignRows)" in body


def test_realtime_campaign_table_has_allocation_status_column(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    panel = _extract_realtime_panel(body)

    assert "<th>分摊状态</th>" in panel
    assert "function formatCampaignAllocationStatus(row)" in body
    assert "formatCampaignAllocationStatus(row)" in body


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


# ───────────────────────────────────────────────────────────
# 多账户实时汇总（Docs-anchor:
#   docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md「实时表 fallback 读取」）
# ───────────────────────────────────────────────────────────


def test_today_realtime_meta_totals_sums_across_accounts_with_per_account_max(monkeypatch):
    """每账户最新 snapshot 时间不一致时，应按账户分别取最新 snapshot 后再合并，不能用全局 MAX。"""
    target = date(2026, 5, 8)
    omurio_snapshot = datetime(2026, 5, 8, 17, 0)
    newjoyloo_snapshot = datetime(2026, 5, 8, 16, 40)
    seen: dict[str, tuple] = {}

    def fake_query(sql, args=()):
        if (
            "FROM meta_ad_realtime_daily_campaign_metrics" in sql
            and "MAX(snapshot_at)" in sql
            and "GROUP BY ad_account_id" in sql
        ):
            assert args == (target,)
            return [
                {
                    "ad_account_id": "1253003326160754",
                    "snapshot_at": omurio_snapshot,
                },
                {
                    "ad_account_id": "1861285821213497",
                    "snapshot_at": newjoyloo_snapshot,
                },
            ]
        if (
            "FROM meta_ad_realtime_daily_campaign_metrics" in sql
            and "SUM(spend_usd)" in sql
            and "ad_account_id=%s" in sql
        ):
            _, ad_account_id, snapshot_at = args
            seen[ad_account_id] = (target, snapshot_at)
            if ad_account_id == "1253003326160754":
                return [{"ad_spend": 11.12, "meta_purchase_value": 0.0, "meta_purchases": 0}]
            if ad_account_id == "1861285821213497":
                return [{"ad_spend": 246.36, "meta_purchase_value": 320.0, "meta_purchases": 4}]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = realtime_oa._get_today_realtime_meta_totals(target)

    assert result is not None
    # 必须把两个账户的最新 snapshot spend 加起来（11.12 + 246.36），不能只算其中一个
    assert result["ad_spend"] == 257.48
    assert result["meta_purchase_value"] == 320.0
    assert result["meta_purchases"] == 4
    # snapshot_at 取所有账户里最新的那一个，便于显示数据新鲜度
    assert result["snapshot_at"] == omurio_snapshot
    # 每个账户都用了它自己的最新 snapshot
    assert seen == {
        "1253003326160754": (target, omurio_snapshot),
        "1861285821213497": (target, newjoyloo_snapshot),
    }


def test_today_realtime_meta_totals_returns_none_when_no_snapshots(monkeypatch):
    target = date(2026, 5, 8)

    def fake_query(sql, args=()):
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    assert realtime_oa._get_today_realtime_meta_totals(target) is None


def test_today_realtime_meta_totals_handles_legacy_null_account_id(monkeypatch):
    target = date(2026, 5, 8)
    snapshot_at = datetime(2026, 5, 8, 16, 40)

    def fake_query(sql, args=()):
        if (
            "FROM meta_ad_realtime_daily_campaign_metrics" in sql
            and "MAX(snapshot_at)" in sql
            and "GROUP BY ad_account_id" in sql
        ):
            return [{"ad_account_id": None, "snapshot_at": snapshot_at}]
        if (
            "FROM meta_ad_realtime_daily_campaign_metrics" in sql
            and "SUM(spend_usd)" in sql
            and "ad_account_id IS NULL" in sql
        ):
            return [{"ad_spend": 99.0, "meta_purchase_value": 100.0, "meta_purchases": 1}]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = realtime_oa._get_today_realtime_meta_totals(target)
    assert result is not None
    assert result["ad_spend"] == 99.0
    assert result["snapshot_at"] == snapshot_at
