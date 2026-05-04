"""Shopify Payments 手续费 4 档判定纯函数测试。

规则文档：docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md
"""
from __future__ import annotations

import pytest

from appcore.order_analytics.shopify_fee import (
    calculate_shopify_fee,
    classify_tier,
    estimate_net_income,
    verify_fee,
)


# 来自规则文档 §6 真实 Shopify Payments 数据
# (amount, presentment_currency, card_country, expected_fee, expected_tier)
REAL_DATA_CASES = [
    (19.94, "USD", "US", 0.80, "A"),
    (30.94, "USD", "GB", 1.38, "B"),
    (22.13, "EUR", "US", 1.19, "C"),
    (22.13, "EUR", "DE", 1.41, "D"),
    (20.94, "EUR", "DE", 1.35, "D"),
    (47.79, "EUR", "US", 2.21, "C"),
]


@pytest.mark.parametrize(
    "amount,presentment_currency,card_country,expected_fee,expected_tier",
    REAL_DATA_CASES,
)
def test_calculate_shopify_fee_matches_real_data(
    amount, presentment_currency, card_country, expected_fee, expected_tier
):
    """规则文档第 6 节 6 条真实数据用例必须在 ±$0.02 容差内通过。"""
    result = calculate_shopify_fee(
        amount=amount,
        presentment_currency=presentment_currency,
        card_country=card_country,
    )
    assert abs(result["fee"] - expected_fee) <= 0.02, (
        f"fee {result['fee']} deviates from expected {expected_fee} beyond tolerance"
    )
    assert result["tier"] == expected_tier
    assert result["amount"] == pytest.approx(amount)
    assert abs(result["net"] - (amount - expected_fee)) <= 0.02


def test_calculate_shopify_fee_returns_rate_breakdown_for_tier_b():
    result = calculate_shopify_fee(
        amount=30.94, presentment_currency="USD", card_country="GB"
    )
    breakdown = result["rate_breakdown"]
    assert breakdown["base_rate"] == 0.025
    assert breakdown["cross_border_rate"] == 0.010
    assert breakdown["currency_conversion_rate"] == 0.0
    assert breakdown["total_percentage_rate"] == pytest.approx(0.035)
    assert breakdown["fixed_fee"] == 0.30


def test_calculate_shopify_fee_returns_rate_breakdown_for_tier_d():
    result = calculate_shopify_fee(
        amount=22.13, presentment_currency="EUR", card_country="DE"
    )
    breakdown = result["rate_breakdown"]
    assert breakdown["cross_border_rate"] == 0.010
    assert breakdown["currency_conversion_rate"] == 0.015
    assert breakdown["total_percentage_rate"] == pytest.approx(0.050)


def test_calculate_shopify_fee_unknown_country_uses_estimated_tier():
    """card_country=None 按保守估算（视作国际卡），tier 后缀 _estimated。"""
    result = calculate_shopify_fee(
        amount=30.94, presentment_currency="USD", card_country=None
    )
    assert result["tier"] == "B_estimated"
    assert abs(result["fee"] - 1.38) <= 0.02


def test_calculate_shopify_fee_unknown_country_eur_is_d_estimated():
    result = calculate_shopify_fee(
        amount=22.13, presentment_currency="EUR", card_country=None
    )
    assert result["tier"] == "D_estimated"
    assert abs(result["fee"] - 1.41) <= 0.02


def test_classify_tier_for_all_four_combinations():
    assert classify_tier(presentment_currency="USD", card_country="US") == "A"
    assert classify_tier(presentment_currency="USD", card_country="GB") == "B"
    assert classify_tier(presentment_currency="EUR", card_country="US") == "C"
    assert classify_tier(presentment_currency="EUR", card_country="DE") == "D"


def test_classify_tier_is_case_insensitive():
    assert classify_tier(presentment_currency="usd", card_country="us") == "A"
    assert classify_tier(presentment_currency="eur", card_country="de") == "D"


def test_estimate_net_income_returns_amount_minus_fee():
    net = estimate_net_income(
        amount=19.94, presentment_currency="USD", card_country="US"
    )
    assert abs(net - (19.94 - 0.80)) <= 0.02


def test_verify_fee_recognizes_domestic_match():
    """实际 fee = $1.19 (Tier C)，应识别为 domestic + matches_standard。"""
    result = verify_fee(amount=22.13, actual_fee=1.19, presentment_currency="EUR")
    assert result["card_origin"] == "domestic"
    assert result["matches_standard"] is True


def test_verify_fee_recognizes_international_match():
    """实际 fee = $1.41 (Tier D)，应识别为 international + matches_standard。"""
    result = verify_fee(amount=22.13, actual_fee=1.41, presentment_currency="EUR")
    assert result["card_origin"] == "international"
    assert result["matches_standard"] is True


def test_verify_fee_flags_anomaly_when_neither_match():
    """完全不在容差内 → matches_standard=False。"""
    result = verify_fee(amount=22.13, actual_fee=5.00, presentment_currency="EUR")
    assert result["matches_standard"] is False
    assert result["card_origin"] == "unknown"
    assert "expected_domestic" in result
    assert "expected_international" in result


def test_verify_fee_handles_usd_presentment_currency():
    """USD 订单 (Tier A 预期 0.80, Tier B 预期 1.38)。"""
    domestic = verify_fee(amount=19.94, actual_fee=0.80, presentment_currency="USD")
    assert domestic["card_origin"] == "domestic"
    assert domestic["matches_standard"] is True

    intl = verify_fee(amount=30.94, actual_fee=1.38, presentment_currency="USD")
    assert intl["card_origin"] == "international"
    assert intl["matches_standard"] is True


def test_verify_fee_within_tolerance():
    """±$0.02 容差内应仍判定 matches_standard。"""
    result = verify_fee(amount=22.13, actual_fee=1.20, presentment_currency="EUR")
    assert result["matches_standard"] is True
    assert result["card_origin"] == "domestic"
