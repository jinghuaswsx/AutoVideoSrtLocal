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
        assert kwargs["site_codes"] == ("newjoy", "omurio", "cozywint")
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
        site_codes=("newjoy", "omurio", "cozywint"),
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


def test_build_yesterday_same_time_comparison_profit_uses_absolute_negative_baseline(monkeypatch):
    """Docs-anchor: docs/superpowers/specs/2026-06-06-realtime-dashboard-yesterday-same-time-comparison-design.md#API设计"""
    target = date(2026, 6, 5)
    current_until = datetime(2026, 6, 6, 10, 20)

    monkeypatch.setattr(
        realtime_oa,
        "_get_realtime_order_summary",
        lambda *_args, **_kwargs: {
            "revenue_with_shipping": 1000.0,
            "order_count": 50,
        },
    )
    monkeypatch.setattr(
        realtime_oa,
        "_build_order_profit_summary_until",
        lambda *_args, **_kwargs: {"profit_with_estimate_usd": -100.0},
    )

    comparison = realtime_oa._build_yesterday_same_time_comparison(
        {
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
                "profit_with_estimate_usd": 200.0,
            },
        },
        target=target,
        now=datetime(2026, 6, 6, 10, 25),
        product_id=None,
        product_ids=None,
        unmatched_ads=False,
        product_launch_scope=None,
        site_codes=("newjoy", "omurio", "cozywint"),
    )

    assert comparison["summary"]["profit_with_estimate_usd"] == {
        "current": 200.0,
        "previous": -100.0,
        "pct": 300.0,
    }


@pytest.mark.parametrize(
    "target, product_id, product_ids, unmatched_ads, product_launch_scope, site_codes",
    [
        (date(2026, 6, 4), None, None, False, None, ("newjoy", "omurio", "cozywint")),
        (date(2026, 6, 5), 42, None, False, None, ("newjoy", "omurio", "cozywint")),
        (date(2026, 6, 5), None, (42,), False, "new", ("newjoy", "omurio", "cozywint")),
        (date(2026, 6, 5), None, None, True, "unmatched", ("newjoy", "omurio", "cozywint")),
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


def test_metric_comparison_absolute_negative_baseline_tracks_profit_direction():
    assert realtime_oa._metric_comparison(
        200.0,
        -100.0,
        use_abs_previous_denominator=True,
    ) == {
        "current": 200.0,
        "previous": -100.0,
        "pct": 300.0,
    }
    assert realtime_oa._metric_comparison(
        -200.0,
        -100.0,
        use_abs_previous_denominator=True,
    ) == {
        "current": -200.0,
        "previous": -100.0,
        "pct": -100.0,
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
            assert args[-2:] == (target, snapshot_until)
            return [{"business_date": target, "product_id": 42, "units": 3}]
        if "SELECT d.dxm_package_id" in sql and "p.ad_cost_usd" in sql:
            assert "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at) <= %s" in sql
            assert args[-2:] == (target, snapshot_until)
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
        site_codes=("newjoy", "omurio", "cozywint"),
    )

    assert result["package_deltas"] == {"PKG-1": 8.0, "PKG-2": 16.0}
    assert result["total_delta"] == 24.0
    assert result["unallocated_spend"] == 0.0
    assert result["has_realtime_ad_watermark"] is True
    assert any("snapshot_at<=%s" in sql for sql, _args in calls)


def test_load_realtime_ad_cost_adjustments_until_filters_order_units_by_site(monkeypatch):
    target = date(2026, 6, 4)
    snapshot_until = datetime(2026, 6, 5, 10, 20)
    account_snapshot = datetime(2026, 6, 5, 10, 0)
    order_sqls = []

    monkeypatch.setattr(
        realtime_oa,
        "_resolve_ad_account_ids_for_sites",
        lambda sites: ["act_newjoy"] if sites == ("newjoy",) else None,
    )

    def fake_query(sql, args=()):
        if "SELECT business_date, ad_account_id, MAX(snapshot_at) AS snapshot_at" in sql:
            assert "ad_account_id IN (%s)" in sql
            assert args == (target, snapshot_until, "act_newjoy")
            return [
                {
                    "business_date": target,
                    "ad_account_id": "act_newjoy",
                    "snapshot_at": account_snapshot,
                }
            ]
        if "SELECT business_date, campaign_name, normalized_campaign_code, spend_usd" in sql:
            assert args == (target, "act_newjoy", account_snapshot)
            return [
                {
                    "business_date": target,
                    "campaign_name": "demo-product-rjc",
                    "normalized_campaign_code": "demo-product-rjc",
                    "spend_usd": 30.0,
                }
            ]
        if "COALESCE(SUM(d.quantity), 0) AS units" in sql:
            order_sqls.append(sql)
            assert "d.site_code IN (%s)" in sql
            assert args[0] == "newjoy"
            assert args[-2:] == (target, snapshot_until)
            return [{"business_date": target, "product_id": 42, "units": 3}]
        if "SELECT d.dxm_package_id" in sql and "p.ad_cost_usd" in sql:
            order_sqls.append(sql)
            assert "d.site_code IN (%s)" in sql
            assert args[0] == "newjoy"
            assert args[-2:] == (target, snapshot_until)
            return [
                {
                    "dxm_package_id": "PKG-1",
                    "business_date": target,
                    "status": "ok",
                    "product_id": 42,
                    "quantity": 3,
                    "ad_cost_usd": 0.0,
                }
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
        site_codes=("newjoy",),
    )

    assert result["package_deltas"] == {"PKG-1": 30.0}
    assert len(order_sqls) == 2


def test_apply_realtime_ad_cost_adjustments_until_soft_fails_loader_errors(monkeypatch):
    def raise_loader(*args, **kwargs):
        raise RuntimeError("ad snapshot unavailable")

    monkeypatch.setattr(realtime_oa, "_load_realtime_ad_cost_adjustments_until", raise_loader)
    details = [
        {
            "dxm_package_id": "PKG-1",
            "ad_cost_usd": 1.0,
            "order_profit_usd": 9.0,
            "order_profit_with_estimate_usd": 8.0,
        }
    ]

    result = realtime_oa._apply_realtime_ad_cost_adjustments_until(
        details,
        target=date(2026, 6, 4),
        snapshot_until=datetime(2026, 6, 5, 10, 20),
        product_id=None,
        site_codes=("newjoy", "omurio"),
    )

    assert result == {
        "package_deltas": {},
        "status_deltas": {},
        "total_delta": 0.0,
        "unallocated_spend": 0.0,
        "has_realtime_ad_watermark": False,
    }
    assert details == [
        {
            "dxm_package_id": "PKG-1",
            "ad_cost_usd": 1.0,
            "order_profit_usd": 9.0,
            "order_profit_with_estimate_usd": 8.0,
        }
    ]


def test_get_realtime_roas_overview_attaches_disabled_comparison_for_range(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [])

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
        if "MAX(r.finished_at)" in sql:
            return [{"last_ad_updated_at": datetime(2026, 6, 6, 10, 18)}]
        if "SELECT MAX(snapshot_at) AS latest_at" in sql:
            return [{"latest_at": snapshot_at}]
        if "SELECT ad_account_id, MAX(snapshot_at) AS latest_at" in sql:
            return [{"ad_account_id": "act_1", "latest_at": snapshot_at}]
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
            if args and date(2026, 6, 4) in args:
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


def test_get_realtime_roas_overview_product_snapshot_comparison_is_disabled(monkeypatch):
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
        if "SELECT ad_account_id, MAX(snapshot_at) AS latest_at" in sql:
            return [{"ad_account_id": "act_1", "latest_at": snapshot_at}]
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
            return [
                {
                    "order_count": 60,
                    "line_count": 66,
                    "units": 80,
                    "order_revenue": 1100.0,
                    "line_revenue": 1100.0,
                    "shipping_revenue": 100.0,
                    "first_order_at": datetime(2026, 6, 5, 16, 30),
                    "last_order_at": datetime(2026, 6, 6, 10, 5),
                    "last_order_updated_at": datetime(2026, 6, 6, 10, 21),
                }
            ]
        if "FROM roi_hourly_sync_runs" in sql:
            return [{"last_order_updated_at": datetime(2026, 6, 6, 10, 21)}]
        if "MAX(r.finished_at)" in sql:
            return [{"last_ad_updated_at": datetime(2026, 6, 6, 10, 18)}]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        start_date=target.isoformat(),
        end_date=target.isoformat(),
        now=datetime(2026, 6, 6, 10, 25),
        product_id=42,
    )

    assert result["scope"]["product_id"] == 42
    assert result["comparison"]["yesterday_same_time"] == {
        "enabled": False,
        "label": "较昨天同刻",
        "basis": None,
        "summary": {},
    }


def test_get_realtime_roas_overview_fallback_attaches_current_day_global_comparison(monkeypatch):
    target = date(2026, 6, 5)

    def fake_query(sql, args=()):
        if "FROM roi_daily_roas_nodes" in sql:
            return []
        if "FROM roi_realtime_daily_snapshots" in sql:
            return []
        if "GROUP BY hour" in sql:
            return [
                {
                    "hour": 18,
                    "order_count": 60,
                    "line_count": 66,
                    "units": 80,
                    "order_revenue": 1100.0,
                    "line_revenue": 1100.0,
                    "shipping_revenue": 100.0,
                    "first_order_at": datetime(2026, 6, 6, 10, 0),
                    "last_order_at": datetime(2026, 6, 6, 10, 5),
                    "last_order_updated_at": datetime(2026, 6, 6, 10, 21),
                }
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [{"ad_spend": 300.0, "meta_purchase_value": 400.0, "meta_purchases": 5}]
        if "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS order_revenue" in sql:
            if args and date(2026, 6, 4) in args:
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
        if "SELECT ad_account_id, MAX(snapshot_at) AS latest_at" in sql:
            return [{"ad_account_id": "act_1", "latest_at": datetime(2026, 6, 5, 10, 0)}]
        if "SELECT MAX(snapshot_at) AS latest_at" in sql:
            return [{"latest_at": datetime(2026, 6, 5, 10, 0)}]
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
                    "spend_usd": 100.0,
                    "purchase_value_usd": 120.0,
                    "impressions": 1000,
                    "clicks": 50,
                }
            ]
        if "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id" in sql:
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
