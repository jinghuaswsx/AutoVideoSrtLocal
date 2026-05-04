"""buyerCountry → Shopify Fee 集成测试。

把店小秘订单的 buyerCountry 字段当作 card_country 代理（首版假设：
客户用本地卡），并按国家推断 presentment_currency。

业务假设见 docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md §10。
"""
from __future__ import annotations

import pytest

from appcore.order_analytics.shopify_fee import (
    calculate_shopify_fee,
    estimate_fee_for_buyer_country,
)


def test_eu_buyer_assumed_local_card_returns_tier_d():
    """德国 buyer (DE) → presentment EUR + card_country DE → Tier D。"""
    result = estimate_fee_for_buyer_country(amount=22.13, buyer_country="DE")
    assert result["tier"] == "D"
    assert abs(result["fee"] - 1.41) <= 0.02


def test_uk_buyer_returns_tier_b():
    """英国 buyer → presentment GBP（与 USD 不同）+ card 跨境 → Tier D。"""
    result = estimate_fee_for_buyer_country(amount=30.94, buyer_country="GB")
    # 注：buyer GB 推断 presentment=GBP，与 settlement USD 不同 → 需要换汇
    # card_country=GB 跟 store=US 不同 → 跨境
    # 所以是 Tier D（不是 B），fee 应是 5.0% × 30.94 + 0.30 = 1.85
    assert result["tier"] == "D"
    assert abs(result["fee"] - 1.85) <= 0.02


def test_us_buyer_returns_tier_a():
    """美国 buyer → 本土卡 + USD 结账 → Tier A。"""
    result = estimate_fee_for_buyer_country(amount=19.94, buyer_country="US")
    assert result["tier"] == "A"
    assert abs(result["fee"] - 0.80) <= 0.02


def test_unknown_country_returns_estimated_tier():
    """未知 buyer → 视为国际卡保守估算 + USD 结账 → B_estimated（card_country=None 触发）。"""
    result = estimate_fee_for_buyer_country(amount=30.94, buyer_country=None)
    # 未知国家时不知道卡是否跨境，取保守估算 → Tier B_estimated
    assert result["tier"] == "B_estimated"


def test_dxm_order_line_dict_integration():
    """模拟从 dianxiaomi_order_lines 读出的行 dict，端到端跑 fee 估算。"""
    line = {
        "id": 274668,
        "buyer_country": "GB",
        "line_amount": 29.95,
        "ship_amount": 6.99,  # 客户付的运费
        "order_currency": "USD",
    }
    # 营收 = line_amount + 摊到的运费收入（这里只有一行所以拿全部 ship_amount）
    revenue = line["line_amount"] + line["ship_amount"]
    result = estimate_fee_for_buyer_country(
        amount=revenue, buyer_country=line["buyer_country"]
    )
    # GB buyer → presentment=GBP, card=GB → Tier D
    expected_fee = revenue * 0.05 + 0.30
    assert abs(result["fee"] - expected_fee) <= 0.02
    assert result["tier"] == "D"


def test_estimate_fee_consistent_with_explicit_call():
    """便利函数的结果应与显式调用 calculate_shopify_fee 等价。"""
    via_helper = estimate_fee_for_buyer_country(amount=22.13, buyer_country="DE")
    via_direct = calculate_shopify_fee(
        amount=22.13, presentment_currency="EUR", card_country="DE"
    )
    assert via_helper["fee"] == via_direct["fee"]
    assert via_helper["tier"] == via_direct["tier"]
