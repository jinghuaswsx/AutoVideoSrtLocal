"""Tests for profit_with_estimate_margin_pct in realtime overview summary.

Spec: docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md
"""

from __future__ import annotations

from appcore.order_analytics.realtime import (
    _build_order_profit_summary,
    _build_order_profit_summary_from_status,
    _empty_order_profit_summary,
)


def _row(
    *,
    total_revenue: float,
    purchase: float = 0.0,
    logistics: float = 0.0,
    shopify_fee: float = 0.0,
    ad_cost: float = 0.0,
) -> dict:
    return {
        "total_revenue": total_revenue,
        "refund_deduction_usd": 0.0,
        "return_reserve_usd": 0.0,
        "profit_deduction_usd": 0.0,
        "purchase_cost_usd": purchase,
        "purchase_estimate_usd": 0.0,
        "logistics_cost_usd": logistics,
        "logistics_estimate_usd": 0.0,
        "shopify_fee_total_usd": shopify_fee,
        "ad_cost_usd": ad_cost,
        "purchase_cost_missing": False,
        "logistics_cost_missing": False,
    }


def test_empty_order_profit_summary_has_margin_key_default_none():
    summary = _empty_order_profit_summary()
    assert "profit_with_estimate_margin_pct" in summary
    assert summary["profit_with_estimate_margin_pct"] is None


def test_build_order_profit_summary_positive_margin():
    rows = [_row(total_revenue=100.0, purchase=30.0, logistics=10.0, shopify_fee=5.0, ad_cost=15.0)]
    summary = _build_order_profit_summary(rows, total_ad_spend_usd=15.0)
    assert summary["total_revenue_usd"] == 100.0
    assert summary["profit_with_estimate_usd"] == 40.0
    assert summary["profit_with_estimate_margin_pct"] == 40.0


def test_build_order_profit_summary_zero_revenue_returns_none():
    summary = _build_order_profit_summary([], total_ad_spend_usd=0.0)
    assert summary["total_revenue_usd"] == 0.0
    assert summary["profit_with_estimate_margin_pct"] is None


def test_build_order_profit_summary_negative_profit_negative_margin():
    rows = [_row(total_revenue=50.0, purchase=40.0, logistics=10.0, shopify_fee=5.0, ad_cost=20.0)]
    summary = _build_order_profit_summary(rows, total_ad_spend_usd=20.0)
    assert summary["profit_with_estimate_usd"] == -25.0
    assert summary["profit_with_estimate_margin_pct"] == -50.0


def test_build_order_profit_summary_two_decimal_rounding():
    rows = [_row(total_revenue=300.0, purchase=99.999, logistics=0.0, shopify_fee=0.0, ad_cost=0.0)]
    summary = _build_order_profit_summary(rows, total_ad_spend_usd=0.0)
    margin = summary["profit_with_estimate_margin_pct"]
    assert isinstance(margin, float)
    assert margin == round(margin, 2)


def test_build_order_profit_summary_includes_cost_ratio_fields():
    rows = [_row(total_revenue=200.0, purchase=80.0, logistics=20.0, shopify_fee=5.0, ad_cost=30.0)]
    summary = _build_order_profit_summary(rows, total_ad_spend_usd=40.0)

    assert summary["total_ad_spend_ratio_pct"] == 20.0
    assert summary["purchase_cost_ratio_pct"] == 40.0
    assert summary["logistics_cost_ratio_pct"] == 10.0
    assert summary["shopify_fee_ratio_pct"] == 2.5


def test_build_order_profit_summary_cost_ratios_are_none_without_revenue():
    summary = _build_order_profit_summary([], total_ad_spend_usd=40.0)

    assert summary["total_ad_spend_ratio_pct"] is None
    assert summary["purchase_cost_ratio_pct"] is None
    assert summary["logistics_cost_ratio_pct"] is None
    assert summary["shopify_fee_ratio_pct"] is None


def test_build_order_profit_summary_from_status_includes_margin():
    status = {
        "total_revenue_usd": 200.0,
        "purchase_cost_with_estimate_usd": 80.0,
        "shipping_cost_with_estimate_usd": 20.0,
        "unallocated_ad_spend_usd": 0.0,
        "overview": {"line_count": 3, "total_profit_usd": 50.0},
        "summary": {"ok": {}, "incomplete": {}},
        "estimated": {"lines": 0},
    }
    summary = _build_order_profit_summary_from_status(status, order_count=3)
    assert summary["total_revenue_usd"] == 200.0
    assert summary["profit_with_estimate_usd"] == 50.0
    assert summary["profit_with_estimate_margin_pct"] == 25.0


def test_build_order_profit_summary_from_status_includes_cost_ratios():
    status = {
        "total_revenue_usd": 200.0,
        "purchase_cost_with_estimate_usd": 80.0,
        "shipping_cost_with_estimate_usd": 20.0,
        "unallocated_ad_spend_usd": 10.0,
        "overview": {"line_count": 3, "total_profit_usd": 50.0},
        "summary": {
            "ok": {"ad_cost": 30.0, "shopify_fee": 4.0},
            "incomplete": {"ad_cost": 0.0, "shopify_fee": 1.0},
        },
        "estimated": {"lines": 0},
    }

    summary = _build_order_profit_summary_from_status(status, order_count=3)

    assert summary["total_ad_spend_ratio_pct"] == 20.0
    assert summary["purchase_cost_ratio_pct"] == 40.0
    assert summary["logistics_cost_ratio_pct"] == 10.0
    assert summary["shopify_fee_ratio_pct"] == 2.5


def test_build_order_profit_summary_from_status_zero_revenue_returns_none():
    status = {
        "total_revenue_usd": 0.0,
        "purchase_cost_with_estimate_usd": 0.0,
        "shipping_cost_with_estimate_usd": 0.0,
        "unallocated_ad_spend_usd": 0.0,
        "overview": {"line_count": 1, "total_profit_usd": 0.0},
        "summary": {"ok": {}, "incomplete": {}},
        "estimated": {"lines": 0},
    }
    summary = _build_order_profit_summary_from_status(status, order_count=1)
    assert summary["profit_with_estimate_margin_pct"] is None
