"""订单利润回填口径测试。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from tools import order_profit_backfill as opb


def test_line_query_filters_by_meta_business_date():
    """回填窗口必须使用 Meta 业务日，而不是付款自然日。"""
    assert "d.meta_business_date" in opb._LINE_QUERY
    assert "WHERE d.meta_business_date BETWEEN %s AND %s" in opb._LINE_QUERY
    assert "WHERE DATE(d.order_paid_at) BETWEEN %s AND %s" not in opb._LINE_QUERY


def test_process_line_uses_meta_business_date_for_ad_allocation(monkeypatch):
    """16:00 切日后的订单行，广告分摊也必须按同一个 Meta 业务日查 spend/units。"""
    seen: dict[str, date] = {}

    def fake_units(*, product_id, business_date):
        seen["units_date"] = business_date
        return 2

    def fake_spend(*, product_id, business_date):
        seen["spend_date"] = business_date
        return 20.0

    monkeypatch.setattr(opb, "get_sku_daily_units", fake_units)
    monkeypatch.setattr(opb, "get_sku_daily_ad_spend", fake_spend)

    line = {
        "dxm_order_line_id": 101,
        "product_id": 7,
        "quantity": 1,
        "line_amount": Decimal("100.00"),
        "order_amount": Decimal("100.00"),
        "ship_amount": Decimal("0.00"),
        "buyer_country": "US",
        "order_paid_at": datetime(2026, 5, 1, 8, 30, 0),
        "paid_at": datetime(2026, 5, 1, 8, 30, 0),
        "meta_business_date": date(2026, 4, 30),
        "dxm_package_id": "PKG-101",
        "logistic_fee": None,
        "purchase_price": Decimal("10.00"),
        "packet_cost_actual": Decimal("5.00"),
        "packet_cost_estimated": None,
    }

    _result, business_date = opb._process_line(
        line,
        order_total_amount=100.0,
        order_shipping=0.0,
        sku_units_cache={},
        sku_spend_cache={},
        rmb_per_usd=Decimal("1"),
        return_reserve_rate=Decimal("0.01"),
    )

    assert business_date == date(2026, 4, 30)
    assert seen == {
        "units_date": date(2026, 4, 30),
        "spend_date": date(2026, 4, 30),
    }
