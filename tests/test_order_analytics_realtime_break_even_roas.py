"""Tests for global break-even ROAS in realtime overview summary.

Spec: docs/superpowers/specs/2026-05-17-realtime-dashboard-global-break-even-roas-design.md
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
    profit_deduction: float = 0.0,
    ad_cost: float = 0.0,
) -> dict:
    return {
        "total_revenue": total_revenue,
        "refund_deduction_usd": 0.0,
        "return_reserve_usd": 0.0,
        "profit_deduction_usd": profit_deduction,
        "purchase_cost_usd": purchase,
        "purchase_estimate_usd": 0.0,
        "logistics_cost_usd": logistics,
        "logistics_estimate_usd": 0.0,
        "shopify_fee_total_usd": shopify_fee,
        "ad_cost_usd": ad_cost,
        "purchase_cost_missing": False,
        "logistics_cost_missing": False,
    }


def test_empty_order_profit_summary_has_global_break_even_roas_default_none():
    summary = _empty_order_profit_summary()

    assert summary["global_break_even_roas"] is None


def test_global_break_even_roas_rounds_up_to_three_decimals():
    summary = _build_order_profit_summary(
        [_row(total_revenue=100, purchase=30, logistics=0, shopify_fee=4.94)],
        total_ad_spend_usd=0,
    )

    assert summary["global_break_even_roas"] == 1.538


def test_global_break_even_roas_keeps_exact_third_decimal():
    summary = _build_order_profit_summary(
        [_row(total_revenue=1537, purchase=537, logistics=0, shopify_fee=0)],
        total_ad_spend_usd=0,
    )

    assert summary["global_break_even_roas"] == 1.537


def test_global_break_even_roas_ignores_actual_ad_spend():
    summary = _build_order_profit_summary(
        [_row(total_revenue=100, purchase=30, logistics=0, shopify_fee=4.94, ad_cost=99)],
        total_ad_spend_usd=120,
    )

    assert summary["global_break_even_roas"] == 1.538


def test_global_break_even_roas_returns_none_when_available_ad_spend_not_positive():
    summary = _build_order_profit_summary(
        [_row(total_revenue=100, purchase=100, logistics=0, shopify_fee=0)],
        total_ad_spend_usd=0,
    )

    assert summary["global_break_even_roas"] is None


def test_global_break_even_roas_from_status_summary():
    status = {
        "total_revenue_usd": 100.0,
        "purchase_cost_with_estimate_usd": 30.0,
        "shipping_cost_with_estimate_usd": 0.0,
        "unallocated_ad_spend_usd": 0.0,
        "overview": {"line_count": 1, "total_profit_usd": 50.0},
        "summary": {
            "ok": {"shopify_fee": 4.94},
            "incomplete": {},
        },
        "estimated": {"lines": 0},
    }

    summary = _build_order_profit_summary_from_status(status, order_count=1)

    assert summary["global_break_even_roas"] == 1.538
