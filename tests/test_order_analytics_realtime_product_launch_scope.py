"""Realtime overview product launch scope tests.

Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from flask import Flask

from appcore import order_analytics as oa
from appcore.order_analytics import data_quality as dq
from appcore.order_analytics import realtime as realtime_oa
from web.routes import order_analytics as order_analytics_route


def _call_realtime_overview(query_string: str = ""):
    app = Flask(__name__)
    view = order_analytics_route.realtime_overview
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    with app.test_request_context("/order-analytics/realtime-overview" + query_string):
        return view()


def test_new_launch_scope_limits_order_and_daily_ad_queries(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    monkeypatch.setattr(oa, "get_product_ids_for_launch_scope", lambda scope, **kwargs: (101, 102))
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
    assert not any("FROM roi_daily_roas_nodes" in sql for sql, _ in calls)


def test_launch_window_days_passes_to_product_scope_and_response(monkeypatch):
    captured: dict = {}

    def fake_scope(scope, **kwargs):
        captured["scope"] = scope
        captured.update(kwargs)
        return (101,)

    monkeypatch.setattr(oa, "get_product_ids_for_launch_scope", fake_scope)
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])

    result = oa.get_realtime_roas_overview(
        "2026-05-09",
        now=datetime(2026, 5, 10, 12, 0),
        product_launch_scope="new",
        product_launch_window_days=15,
    )

    assert captured["scope"] == "new"
    assert captured["window_days"] == 15
    assert result["scope"]["product_launch_window_days"] == 15


def test_new_launch_scope_roas_points_use_scoped_realtime_campaigns(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    monkeypatch.setattr(oa, "get_product_ids_for_launch_scope", lambda scope, **kwargs: (101,))
    monkeypatch.setattr(
        realtime_oa,
        "resolve_ad_product_match",
        lambda code: {"id": 101, "product_code": "sku-101", "name": "Scoped"} if code == "sku-101" else None,
    )

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if "SELECT ad_account_id, MAX(snapshot_at) AS latest_at" in sql:
            return [{"ad_account_id": "act_1", "latest_at": datetime(2026, 5, 10, 10, 0)}]
        if (
            "FROM meta_ad_realtime_daily_campaign_metrics" in sql
            and "campaign_id, campaign_name" in sql
        ):
                return [
                    {
                        "snapshot_at": datetime(2026, 5, 10, 10, 0),
                        "ad_account_id": "act_1",
                        "ad_account_name": "Meta",
                    "campaign_id": "cmp_1",
                    "campaign_name": "SKU-101 launch",
                    "normalized_campaign_code": "sku-101",
                    "result_count": 1,
                    "spend_usd": 12.5,
                    "purchase_value_usd": 20,
                    "impressions": 100,
                    "clicks": 5,
                    },
                    {
                        "snapshot_at": datetime(2026, 5, 10, 10, 0),
                        "ad_account_id": "act_1",
                        "ad_account_name": "Meta",
                    "campaign_id": "cmp_2",
                    "campaign_name": "other",
                    "normalized_campaign_code": "other",
                    "result_count": 1,
                    "spend_usd": 99,
                    "purchase_value_usd": 0,
                    "impressions": 100,
                    "clicks": 5,
                },
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-05-09",
        now=datetime(2026, 5, 10, 12, 0),
        product_launch_scope="new",
    )

    assert not any("FROM roi_daily_roas_nodes" in sql for sql, _ in calls)
    assert any(point["ad_spend"] == 12.5 for point in result["roas_points"])
    assert all(point["ad_spend"] != 99 for point in result["roas_points"])


def test_unmatched_launch_scope_includes_null_product_orders_and_unmatched_ads(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if "FROM dianxiaomi_order_lines d" in sql and "HOUR(" in sql:
            assert "d.product_id IS NULL" in sql
            return [{
                "hour": 2,
                "order_count": 2,
                "line_count": 3,
                "units": 4,
                "order_revenue": 41.5,
                "line_revenue": 41.5,
                "shipping_revenue": 6.5,
                "first_order_at": datetime(2026, 5, 9, 18, 5),
                "last_order_at": datetime(2026, 5, 9, 18, 30),
                "last_order_updated_at": datetime(2026, 5, 9, 18, 40),
            }]
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
                    "spend_usd": 25.5,
                    "purchase_value": 0,
                    "purchase_value_usd": 0,
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
    assert result["summary"]["order_count"] == 2
    assert result["summary"]["line_count"] == 3
    assert result["summary"]["units"] == 4
    assert result["summary"]["order_revenue"] == 41.5
    assert result["summary"]["shipping_revenue"] == 6.5
    assert result["summary"]["revenue_with_shipping"] == 48.0
    assert result["summary"]["ad_spend"] == 25.5
    assert result["summary"]["true_roas"] == 1.8824
    assert result["campaigns"]
    assert result["product_sales_stats"] == []
    assert any("d.product_id IS NULL" in sql for sql, _ in calls)
    assert any("product_id IS NULL" in sql for sql, _ in calls)


def test_empty_new_scope_does_not_fall_back_to_all_products(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(oa, "get_product_ids_for_launch_scope", lambda scope, **kwargs: ())
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


def test_product_id_and_launch_scope_take_intersection(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(oa, "get_product_ids_for_launch_scope", lambda scope, **kwargs: (101, 102))
    monkeypatch.setattr(oa, "query", lambda sql, args=(): calls.append(sql) or [])

    result = oa.get_realtime_roas_overview(
        "2026-05-09",
        now=datetime(2026, 5, 10, 12, 0),
        product_id=999,
        product_launch_scope="new",
    )

    assert result["scope"]["product_id"] == 999
    assert result["scope"]["product_launch_scope"] == "new"
    assert any("1=0" in sql for sql in calls)


def test_product_sales_stats_uses_product_id_and_scope_intersection(monkeypatch):
    captured: list[list[int] | None] = []

    monkeypatch.setattr(oa, "get_product_ids_for_launch_scope", lambda scope, **kwargs: (101, 102))
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])
    monkeypatch.setattr(
        realtime_oa,
        "get_dianxiaomi_product_sales_stats",
        lambda *args, **kwargs: captured.append(kwargs.get("product_ids")) or [],
    )

    result = oa.get_realtime_roas_overview(
        "2026-05-09",
        now=datetime(2026, 5, 10, 12, 0),
        include_details=True,
        product_id=999,
        product_launch_scope="new",
    )

    assert result["product_sales_stats"] == []
    assert captured[-1] == []


def test_unmatched_daily_purchase_rows_exclude_resolvable_campaign(monkeypatch):
    monkeypatch.setattr(
        realtime_oa,
        "resolve_ad_product_match",
        lambda code: {"id": 101, "product_code": "matched"} if code == "matched" else None,
    )

    def fake_query(sql, args=()):
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [
                {
                    "meta_business_date": date(2026, 5, 9),
                    "ad_account_id": "act_1",
                    "campaign_name": "matched campaign",
                    "normalized_campaign_code": "matched",
                    "matched_product_code": None,
                    "product_id": None,
                    "spend_usd": 100,
                    "purchase_value_usd": 200,
                    "result_count": 2,
                    "updated_at": datetime(2026, 5, 10, 10, 0),
                },
                {
                    "meta_business_date": date(2026, 5, 9),
                    "ad_account_id": "act_1",
                    "campaign_name": "unmatched campaign",
                    "normalized_campaign_code": "unmatched",
                    "matched_product_code": None,
                    "product_id": None,
                    "spend_usd": 5,
                    "purchase_value_usd": 0,
                    "result_count": 1,
                    "updated_at": datetime(2026, 5, 10, 11, 0),
                },
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    summary = realtime_oa._summarize_daily_campaign_purchase_rows(
        date(2026, 5, 9),
        date(2026, 5, 9),
        unmatched_ads=True,
    )

    assert summary["ad_spend"] == 5
    assert summary["meta_purchases"] == 1


def test_scoped_roas_points_read_realtime_campaign_rows_once(monkeypatch):
    day_start = datetime(2026, 5, 9, 16, 0)
    query_counts = {"campaign_rows": 0}

    monkeypatch.setattr(
        realtime_oa,
        "resolve_ad_product_match",
        lambda code: {"id": 101, "product_code": "sku-101"} if code == "sku-101" else None,
    )

    def fake_query(sql, args=()):
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql and "campaign_id, campaign_name" in sql:
            query_counts["campaign_rows"] += 1
            return [
                {
                    "snapshot_at": day_start + timedelta(hours=1),
                    "ad_account_id": "act_1",
                    "ad_account_name": "Meta",
                    "campaign_id": "cmp_1",
                    "campaign_name": "SKU-101 launch",
                    "normalized_campaign_code": "sku-101",
                    "result_count": 1,
                    "spend_usd": 12.5,
                    "purchase_value_usd": 20,
                    "impressions": 100,
                    "clicks": 5,
                }
            ]
        if "SELECT ad_account_id, MAX(snapshot_at) AS latest_at" in sql:
            return [{"ad_account_id": "act_1", "latest_at": day_start + timedelta(hours=1)}]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    points = realtime_oa._build_scoped_roas_points(
        target=date(2026, 5, 9),
        day_start=day_start,
        data_until=day_start + timedelta(hours=2),
        orders_by_hour={},
        product_ids=(101,),
        site_codes=("newjoy", "omurio"),
    )

    assert query_counts["campaign_rows"] == 1
    assert any(point["ad_spend"] == 12.5 for point in points)


def test_range_launch_scope_applies_product_filters(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    monkeypatch.setattr(oa, "get_product_ids_for_launch_scope", lambda scope, **kwargs: (101,))
    monkeypatch.setattr(oa, "query", lambda sql, args=(): calls.append((sql, args)) or [])

    result = oa.get_realtime_roas_overview(
        now=datetime(2026, 5, 10, 12, 0),
        start_date="2026-05-07",
        end_date="2026-05-09",
        product_launch_scope="old",
    )

    assert result["scope"]["product_launch_scope"] == "old"
    assert result["scope"]["product_launch_product_count"] == 1
    assert any("d.product_id IN" in sql and 101 in args for sql, args in calls)
    assert any("product_id IN" in sql and 101 in args for sql, args in calls)


def test_range_unmatched_scope_returns_campaign_details(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if "FROM dianxiaomi_order_lines d" in sql and "GROUP BY d.meta_business_date" in sql:
            assert "d.product_id IS NULL" in sql
            return [
                {
                    "meta_business_date": date(2026, 5, 9),
                    "order_count": 3,
                    "line_count": 4,
                    "units": 5,
                    "order_revenue": 80,
                    "line_revenue": 80,
                    "shipping_revenue": 12,
                    "last_order_at": datetime(2026, 5, 9, 19, 0),
                    "last_order_updated_at": datetime(2026, 5, 9, 19, 30),
                }
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql and "SUM(spend_usd)" in sql:
            if "GROUP BY ad_account_id" in sql:
                return [
                    {
                        "ad_account_id": "act_1",
                        "ad_account_name": "Meta",
                        "campaign_name": "unmatched-campaign",
                        "normalized_campaign_code": "unmatched-campaign",
                        "result_count": 1,
                        "spend": 25.5,
                        "purchase_value": 0,
                    }
                ]
            return [
                {
                    "meta_business_date": date(2026, 5, 9),
                    "ad_spend": 25.5,
                    "meta_purchase_value": 0,
                    "meta_purchases": 1,
                        "last_ad_updated_at": datetime(2026, 5, 10, 10, 0),
                    }
                ]
        if "SELECT meta_business_date, ad_account_id, matched_product_code" in sql:
            return [
                {
                    "meta_business_date": date(2026, 5, 9),
                    "ad_account_id": "act_1",
                    "campaign_name": "unmatched-campaign",
                    "normalized_campaign_code": "unmatched-campaign",
                    "matched_product_code": None,
                    "product_id": None,
                    "spend_usd": 25.5,
                    "purchase_value_usd": 0,
                    "result_count": 1,
                    "updated_at": datetime(2026, 5, 10, 10, 0),
                }
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        now=datetime(2026, 5, 10, 12, 0),
        start_date="2026-05-08",
        end_date="2026-05-09",
        include_details=True,
        product_launch_scope="unmatched",
    )

    assert result["summary"]["order_count"] == 3
    assert result["summary"]["revenue_with_shipping"] == 92.0
    assert result["summary"]["ad_spend"] == 25.5
    assert result["summary"]["true_roas"] == 3.6078
    assert result["campaigns"][0]["normalized_campaign_code"] == "unmatched-campaign"
    assert any("d.product_id IS NULL" in sql for sql, _ in calls)


def test_route_rejects_invalid_product_launch_scope():
    response, status = _call_realtime_overview("?product_launch_scope=maybe")

    assert status == 400
    assert response.get_json()["error"] == "invalid_param"


def test_route_rejects_invalid_product_launch_window_days(monkeypatch):
    monkeypatch.setattr(
        "web.routes.order_analytics.oa.get_realtime_roas_overview",
        lambda *args, **kwargs: pytest.fail("invalid launch window should be rejected before query"),
    )

    response, status = _call_realtime_overview("?product_launch_scope=new&product_launch_window_days=10")

    assert status == 400
    assert response.get_json()["error"] == "invalid_param"


def test_route_passes_product_launch_scope_to_overview(monkeypatch):
    captured: dict = {}

    def fake_overview(date_text, **kwargs):
        captured.update(kwargs)
        return {
            "period": {"date": date(2026, 5, 9)},
            "scope": {
                "product_launch_scope": kwargs.get("product_launch_scope"),
                "product_launch_window_days": kwargs.get("product_launch_window_days"),
                "ad_source": "meta_ad_daily_campaign_metrics",
            },
            "summary": {},
            "freshness": {},
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_realtime_roas_overview", fake_overview)
    monkeypatch.setattr("web.routes.order_analytics._attach_realtime_data_quality", lambda result: result)

    response = _call_realtime_overview("?product_launch_scope=old&product_launch_window_days=15")

    assert response.status_code == 200
    assert captured["product_launch_scope"] == "old"
    assert captured["product_launch_window_days"] == 15


def test_data_quality_includes_product_launch_scope(monkeypatch):
    monkeypatch.setattr(
        dq,
        "build_for_realtime_overview",
        lambda **kwargs: {
            "status": dq.STATUS_OK,
            "source_mode": kwargs["source_mode"],
            "checks": [],
            "warnings": [],
            "errors": [],
            "watermarks": {},
        },
    )

    result = order_analytics_route._attach_realtime_data_quality({
        "period": {"date": date(2026, 5, 9)},
        "scope": {
            "ad_source": "meta_ad_daily_campaign_metrics",
            "product_launch_scope": "unmatched",
            "product_launch_product_count": 0,
        },
        "summary": {},
        "freshness": {},
    })

    assert result["data_quality"]["product_launch_scope"] == "unmatched"
    assert result["data_quality"]["status"] == dq.STATUS_WARNING
    assert any(
        check["code"] == "product_launch_scope" and check["status"] == dq.STATUS_WARNING
        for check in result["data_quality"]["checks"]
    )
