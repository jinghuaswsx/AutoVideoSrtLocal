"""核心订单 SKU 利润核算公式测试。

公式（USD）：
    revenue        = line_amount + shipping_allocated
    shopify_fee    = calculate_shopify_fee(revenue, presentment, card_country)["fee"]
    ad_cost        = (sku_daily_spend × line_units) / sku_daily_units
    purchase       = purchase_price_cny × quantity / rmb_per_usd
    shipping_cost  = packet_cost_cny × quantity / rmb_per_usd
    return_reserve = revenue × return_reserve_rate (1%)
    profit         = revenue - shopify_fee - ad_cost - purchase - shipping_cost - return_reserve
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from appcore.order_analytics.profit_calculation import (
    aggregate_order_profit,
    calculate_line_profit,
)


def _complete_line_input(**overrides):
    """构造一条完备 SKU 行的输入（默认值合理且能算 profit）。"""
    base = {
        "dxm_order_line_id": 274668,
        "product_id": 316,
        "buyer_country": "DE",
        "line_amount_usd": 29.95,
        "quantity": 1,
        "shipping_allocated_usd": 6.99,  # 摊到本行的运费
        "sku_daily_units": 10,
        "sku_daily_ad_spend_usd": 50.00,
        "product_purchase_price_cny": 15.50,
        "shipping_cost_cny": 20.50,  # 已由调用方预解析的行级总额
        "shipping_cost_source": "order_logistic_fee",
    }
    base.update(overrides)
    return base


def test_calculate_line_profit_returns_status_ok_for_complete_input():
    result = calculate_line_profit(
        _complete_line_input(),
        rmb_per_usd=Decimal("6.83"),
    )
    assert result["status"] == "ok"
    assert result["profit_usd"] is not None


def test_calculate_line_profit_revenue_is_line_amount_plus_shipping():
    result = calculate_line_profit(
        _complete_line_input(line_amount_usd=29.95, shipping_allocated_usd=6.99),
        rmb_per_usd=Decimal("6.83"),
    )
    assert result["revenue_usd"] == pytest.approx(36.94, abs=0.01)


def test_calculate_line_profit_shopify_fee_uses_buyer_country_proxy():
    """DE buyer (本地卡 + EUR 结账) → Tier D，fee = 5.0% × revenue + 0.30。"""
    result = calculate_line_profit(
        _complete_line_input(buyer_country="DE", line_amount_usd=22.13,
                              shipping_allocated_usd=0),
        rmb_per_usd=Decimal("6.83"),
    )
    # revenue = 22.13, Tier D → fee = 22.13*0.05 + 0.30 = 1.4065 → 1.41
    assert abs(result["shopify_fee_usd"] - 1.41) <= 0.02
    assert result["shopify_tier"] == "D"


def test_calculate_line_profit_ad_cost_units_proportional():
    """SKU 当日 spend=$100, units=10, 行 quantity=2 → ad_cost=$20。"""
    result = calculate_line_profit(
        _complete_line_input(
            quantity=2,
            sku_daily_units=10,
            sku_daily_ad_spend_usd=100.00,
        ),
        rmb_per_usd=Decimal("6.83"),
    )
    assert result["ad_cost_usd"] == pytest.approx(20.00)


def test_calculate_line_profit_purchase_converts_cny_to_usd():
    """采购 15.50 CNY × 6.83 → ~2.27 USD。"""
    result = calculate_line_profit(
        _complete_line_input(product_purchase_price_cny=15.50, quantity=1),
        rmb_per_usd=Decimal("6.83"),
    )
    assert result["purchase_usd"] == pytest.approx(15.50 / 6.83, abs=0.01)


def test_calculate_line_profit_shipping_cost_converts_cny():
    """小包成本已由调用方预解析为行级总额，直接用 / rmb_per_usd 换算。"""
    result = calculate_line_profit(
        _complete_line_input(shipping_cost_cny=20.50),
        rmb_per_usd=Decimal("6.83"),
    )
    assert result["shipping_cost_usd"] == pytest.approx(20.50 / 6.83, abs=0.01)


def test_calculate_line_profit_return_reserve_is_one_percent_of_revenue():
    result = calculate_line_profit(
        _complete_line_input(line_amount_usd=29.95, shipping_allocated_usd=6.99),
        rmb_per_usd=Decimal("6.83"),
        return_reserve_rate=Decimal("0.01"),
    )
    assert result["return_reserve_usd"] == pytest.approx(0.3694, abs=0.001)


def test_calculate_line_profit_aggregates_full_formula():
    """端到端：把所有项加起来跟 profit 对得上。"""
    result = calculate_line_profit(
        _complete_line_input(),
        rmb_per_usd=Decimal("6.83"),
    )
    # profit = revenue - fee - ad - purchase - shipping_cost - return_reserve
    expected = (
        result["revenue_usd"]
        - result["shopify_fee_usd"]
        - result["ad_cost_usd"]
        - result["purchase_usd"]
        - result["shipping_cost_usd"]
        - result["return_reserve_usd"]
    )
    assert result["profit_usd"] == pytest.approx(expected, abs=0.01)


def test_calculate_line_profit_returns_incomplete_when_purchase_missing():
    line = _complete_line_input(product_purchase_price_cny=None, shipping_cost_source=None)
    result = calculate_line_profit(line, rmb_per_usd=Decimal("6.83"))
    assert result["status"] == "incomplete"
    assert "purchase_price" in result["missing_fields"]
    assert result["profit_usd"] is None


def test_calculate_line_profit_returns_incomplete_when_shipping_cost_missing():
    line = _complete_line_input(shipping_cost_cny=None, shipping_cost_source=None)
    result = calculate_line_profit(line, rmb_per_usd=Decimal("6.83"))
    assert result["status"] == "incomplete"
    assert "shipping_cost" in result["missing_fields"]


def test_calculate_line_profit_single_sku_fallback_uses_line_revenue():
    """单 SKU 订单（无 order_total_revenue_usd）→ fallback 到旧行为（按本行 revenue 算 fee）。"""
    line = _complete_line_input(
        line_amount_usd=22.13, shipping_allocated_usd=0.0,
        buyer_country="DE",
    )
    line.pop("order_total_revenue_usd", None)  # 不传，触发 fallback
    result = calculate_line_profit(line, rmb_per_usd=Decimal("6.83"))
    # Tier D fee = 22.13 × 0.05 + 0.30 = 1.41
    assert abs(result["shopify_fee_usd"] - 1.41) <= 0.02


def test_calculate_line_profit_multi_sku_fee_split_by_order_amount():
    """H1 修复：订单内多 SKU 时，fee 按订单 amount 一次算 + 按 line revenue 摊回。

    订单含 2 行：line1 = $20，line2 = $10，订单运费 $0
    - 旧行为（错）：行 1 fee = $20 × 0.025 + 0.30 = 0.80, 行 2 fee = $10 × 0.025 + 0.30 = 0.55, 合计 1.35
    - 新行为（对）：订单 fee = $30 × 0.025 + 0.30 = 1.05; 行 1 摊 1.05 × 20/30 = 0.70, 行 2 摊 0.35
    - 合计 1.05（少 0.30 = 多算的固定费）
    """
    line1 = _complete_line_input(
        line_amount_usd=20.0, shipping_allocated_usd=0.0,
        buyer_country="US",  # Tier A: 2.5% + 0.30
        order_total_revenue_usd=30.0,
    )
    line2 = _complete_line_input(
        line_amount_usd=10.0, shipping_allocated_usd=0.0,
        buyer_country="US",
        order_total_revenue_usd=30.0,
    )
    r1 = calculate_line_profit(line1, rmb_per_usd=Decimal("6.83"))
    r2 = calculate_line_profit(line2, rmb_per_usd=Decimal("6.83"))
    total_fee = r1["shopify_fee_usd"] + r2["shopify_fee_usd"]
    # 整单 fee = 30 × 0.025 + 0.30 = 1.05
    assert abs(total_fee - 1.05) <= 0.02
    # 大行摊得多
    assert r1["shopify_fee_usd"] > r2["shopify_fee_usd"]


def test_get_configured_return_reserve_rate_default(monkeypatch):
    """B 修复：admin 没设置 → 默认 0.01。"""
    from appcore.order_analytics.profit_calculation import get_configured_return_reserve_rate
    monkeypatch.setattr("appcore.settings.get_setting", lambda k: None)
    assert get_configured_return_reserve_rate() == Decimal("0.01")


def test_get_configured_return_reserve_rate_admin_override(monkeypatch):
    """admin 在 system_settings 设置了非默认值 → 用 admin 值。"""
    from appcore.order_analytics.profit_calculation import get_configured_return_reserve_rate
    monkeypatch.setattr("appcore.settings.get_setting", lambda k: "0.025")
    assert get_configured_return_reserve_rate() == Decimal("0.025")


def test_get_configured_return_reserve_rate_invalid_falls_back(monkeypatch):
    """admin 设了无效值（>1 或负数）→ fallback 到 0.01。"""
    from appcore.order_analytics.profit_calculation import get_configured_return_reserve_rate
    monkeypatch.setattr("appcore.settings.get_setting", lambda k: "1.5")
    assert get_configured_return_reserve_rate() == Decimal("0.01")


def test_calculate_line_profit_records_cost_basis_snapshot():
    """cost_basis 快照应包含汇率、采购价、shipping_cost_source 等，供后续审计/对账。"""
    result = calculate_line_profit(
        _complete_line_input(shipping_cost_source="product_estimated"),
        rmb_per_usd=Decimal("6.83"),
    )
    basis = result["cost_basis"]
    assert basis["rmb_per_usd"] == 6.83
    assert basis["shipping_cost_source"] == "product_estimated"
    assert basis["return_reserve_rate"] == 0.01


# -------- aggregate_order_profit --------

def test_aggregate_order_profit_sums_complete_lines():
    line_results = [
        {"status": "ok", "profit_usd": 5.0, "revenue_usd": 30.0,
         "shopify_fee_usd": 1.0, "ad_cost_usd": 2.0,
         "purchase_usd": 10.0, "shipping_cost_usd": 3.0, "return_reserve_usd": 0.3},
        {"status": "ok", "profit_usd": -2.0, "revenue_usd": 20.0,
         "shopify_fee_usd": 0.7, "ad_cost_usd": 5.0,
         "purchase_usd": 8.0, "shipping_cost_usd": 2.0, "return_reserve_usd": 0.2},
    ]
    summary = aggregate_order_profit(line_results)
    assert summary["status"] == "ok"
    assert summary["profit_usd"] == pytest.approx(3.0)
    assert summary["revenue_usd"] == pytest.approx(50.0)
    assert summary["lines_complete"] == 2
    assert summary["lines_incomplete"] == 0


def test_aggregate_order_profit_partially_complete_when_some_lines_incomplete():
    line_results = [
        {"status": "ok", "profit_usd": 5.0, "revenue_usd": 30.0,
         "shopify_fee_usd": 1.0, "ad_cost_usd": 2.0,
         "purchase_usd": 10.0, "shipping_cost_usd": 3.0, "return_reserve_usd": 0.3},
        {"status": "incomplete", "profit_usd": None,
         "missing_fields": ["purchase_price"],
         "dxm_order_line_id": 999},
    ]
    summary = aggregate_order_profit(line_results)
    assert summary["status"] == "partially_complete"
    assert summary["profit_usd"] == pytest.approx(5.0)
    assert summary["lines_complete"] == 1
    assert summary["lines_incomplete"] == 1
    assert len(summary["incomplete_lines"]) == 1


def test_aggregate_order_profit_all_incomplete():
    line_results = [
        {"status": "incomplete", "profit_usd": None,
         "missing_fields": ["purchase_price"], "dxm_order_line_id": 1},
        {"status": "incomplete", "profit_usd": None,
         "missing_fields": ["packet_cost"], "dxm_order_line_id": 2},
    ]
    summary = aggregate_order_profit(line_results)
    assert summary["status"] == "incomplete"
    assert summary["profit_usd"] is None
    assert summary["lines_complete"] == 0
    assert summary["lines_incomplete"] == 2
