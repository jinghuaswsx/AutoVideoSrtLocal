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


def test_realtime_roas_endpoint_returns_json(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_overview(date_text=None, *, start_date=None, end_date=None, now=None):
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
    # 范围分支不返回逐小时/订单/广告明细
    assert result["hourly"] == []
    assert result["order_details"] == []
    assert result["campaigns"] == []
    assert result["roas_points"] == []
    # period 字段使用 start/end 而非 date
    assert result["period"]["start_date"].isoformat() == "2026-04-29"
    assert result["period"]["end_date"].isoformat() == "2026-04-30"
    assert result["period"]["day_definition"] == "meta_ad_platform_business_day_range"


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

    def fake_overview(date_text=None, *, start_date=None, end_date=None, now=None):
        captured["date"] = date_text
        captured["start_date"] = start_date
        captured["end_date"] = end_date
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
    assert response.get_json()["summary"]["true_roas"] == 2.5


# ── 前端模板回归 ─────────────────────────────────────────


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
    assert "renderRealtimeProductSales(data.product_sales_stats || [])" in body
