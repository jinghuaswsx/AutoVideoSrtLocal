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
        assert kwargs["site_codes"] == ("newjoy", "omurio")
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
        site_codes=("newjoy", "omurio"),
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


@pytest.mark.parametrize(
    "target, product_id, product_ids, unmatched_ads, product_launch_scope, site_codes",
    [
        (date(2026, 6, 4), None, None, False, None, ("newjoy", "omurio")),
        (date(2026, 6, 5), 42, None, False, None, ("newjoy", "omurio")),
        (date(2026, 6, 5), None, (42,), False, "new", ("newjoy", "omurio")),
        (date(2026, 6, 5), None, None, True, "unmatched", ("newjoy", "omurio")),
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
            assert args == (target, snapshot_until)
            return [{"business_date": target, "product_id": 42, "units": 3}]
        if "SELECT d.dxm_package_id" in sql and "p.ad_cost_usd" in sql:
            assert "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at) <= %s" in sql
            assert args == (target, snapshot_until)
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
        site_codes=("newjoy", "omurio"),
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
            assert "d.site_code IN ('newjoy')" in sql
            assert args == (target, snapshot_until)
            return [{"business_date": target, "product_id": 42, "units": 3}]
        if "SELECT d.dxm_package_id" in sql and "p.ad_cost_usd" in sql:
            order_sqls.append(sql)
            assert "d.site_code IN ('newjoy')" in sql
            assert args == (target, snapshot_until)
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
