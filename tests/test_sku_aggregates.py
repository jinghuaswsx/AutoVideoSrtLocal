from datetime import datetime
from decimal import Decimal

import pytest

from appcore import sku_aggregates as mod


def test_shopify_modes_by_sku_picks_top_freq(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [
        {"lineitem_sku": "115-1", "lineitem_price": Decimal("29.95"), "shipping": Decimal("6.99"), "freq": 502},
        {"lineitem_sku": "115-1", "lineitem_price": Decimal("31.10"), "shipping": Decimal("6.99"), "freq": 8},
        {"lineitem_sku": "115-1", "lineitem_price": Decimal("29.95"), "shipping": Decimal("10.99"), "freq": 90},
        {"lineitem_sku": "115-2", "lineitem_price": Decimal("53.91"), "shipping": Decimal("10.99"), "freq": 50},
    ])
    out = mod.shopify_modes_by_sku()
    by_sku = {r["sku"]: r for r in out}
    assert by_sku["115-1"]["price"] == Decimal("29.95")
    assert by_sku["115-1"]["shipping"] == Decimal("6.99")
    assert by_sku["115-2"]["price"] == Decimal("53.91")


def test_update_xmyc_sku_shopify_default_uses_coalesce(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"lineitem_sku": "sku-A", "lineitem_price": Decimal("10"), "shipping": Decimal("3"), "freq": 5},
        {"lineitem_sku": "sku-NotInXmyc", "lineitem_price": Decimal("99"), "shipping": Decimal("1"), "freq": 5},
    ] if "shopify_orders" in sql else [{"sku": "sku-A"}])
    captured = []
    monkeypatch.setattr(mod, "execute", lambda sql, params: captured.append((sql, params)))
    out = mod.update_xmyc_sku_shopify_aggregates()
    assert out == {"shopify_modes": 2, "xmyc_matched": 1, "updated": 1}
    sql, params = captured[0]
    assert "COALESCE" in sql
    assert params == (Decimal("10"), Decimal("3"), "sku-A")


def test_update_xmyc_sku_shopify_force_overwrites(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"lineitem_sku": "sku-A", "lineitem_price": Decimal("10"), "shipping": Decimal("3"), "freq": 5},
    ] if "shopify_orders" in sql else [{"sku": "sku-A"}])
    captured = []
    monkeypatch.setattr(mod, "execute", lambda sql, params: captured.append((sql, params)))
    mod.update_xmyc_sku_shopify_aggregates(force=True)
    sql, _ = captured[0]
    assert "COALESCE" not in sql
    assert "standalone_price_sku=%s" in sql


def test_compute_sku_roas_returns_can_compute_false_when_missing():
    sku = {"unit_price": Decimal("10"), "packet_cost_actual_sku": None,
           "standalone_price_sku": Decimal("30"), "standalone_shipping_fee_sku": Decimal("5")}
    out = mod.compute_sku_roas(sku)
    assert out["can_compute"] is False


def test_compute_sku_roas_calculates_when_complete():
    sku = {"unit_price": Decimal("10"), "packet_cost_actual_sku": Decimal("60"),
           "standalone_price_sku": Decimal("30"), "standalone_shipping_fee_sku": Decimal("7")}
    out = mod.compute_sku_roas(sku)
    assert out["can_compute"] is True
    assert out["effective_roas"] is not None
    assert out["effective_roas"] > 0


def test_enrich_skus_with_roas_appends_field():
    rows = [
        {"sku": "x", "unit_price": Decimal("5"), "packet_cost_actual_sku": Decimal("30"),
         "standalone_price_sku": Decimal("20"), "standalone_shipping_fee_sku": Decimal("5")},
    ]
    out = mod.enrich_skus_with_roas(rows)
    assert "roas" in out[0]
    assert out[0]["roas"]["can_compute"] is True


def test_query_logistic_fees_by_sku(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"product_display_sku": "sku-A", "logistic_fee": 60.0},
        {"product_display_sku": "sku-A", "logistic_fee": 62.0},
        {"product_display_sku": "sku-A", "logistic_fee": 61.5},
        {"product_display_sku": "sku-B", "logistic_fee": 80.0},
    ])
    from datetime import datetime
    out = mod._query_logistic_fees_by_sku(
        {"sku-A", "sku-B"},
        datetime(2026, 4, 2),
        datetime(2026, 5, 2),
    )
    assert sorted(out["sku-A"]) == [60.0, 61.5, 62.0]
    assert out["sku-B"] == [80.0]
    assert "skip" not in out


def test_update_xmyc_sku_parcel_costs_end_to_end(monkeypatch):
    monkeypatch.setattr(mod, "_xmyc_skus_with_shop", lambda: (
        {"sku-A": "shopA", "sku-B": "shopA"},
        {"shopA": {"sku-A", "sku-B"}},
    ))
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"product_display_sku": "sku-A", "logistic_fee": 60.0},
        {"product_display_sku": "sku-A", "logistic_fee": 62.0},
        {"product_display_sku": "sku-B", "logistic_fee": 80.0},
    ])

    captured_writes = []
    monkeypatch.setattr(mod, "execute", lambda sql, params: captured_writes.append((sql, params)))

    result = mod.update_xmyc_sku_parcel_costs(
        days=30, now_func=lambda: datetime(2026, 5, 4),
    )
    assert result["candidates"] == 2
    assert result["with_fees"] == 2
    write_a = next(p for _, p in captured_writes if p[1] == "sku-A")
    write_b = next(p for _, p in captured_writes if p[1] == "sku-B")
    assert write_a[0] == pytest.approx(61.0)
    assert write_b[0] == pytest.approx(80.0)


def test_update_xmyc_sku_order_counts(monkeypatch):
    monkeypatch.setattr(mod, "order_counts_by_sku", lambda: {"sku-A": 100, "sku-B": 5})
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [{"sku": "sku-A"}, {"sku": "sku-C"}])
    writes = []
    monkeypatch.setattr(mod, "execute", lambda sql, params: writes.append(params))
    n = mod.update_xmyc_sku_order_counts()
    assert n == 1
    # sku-A gets 100, sku-C is in xmyc but no orders -> SQL hard-codes 0
    assert (100, "sku-A") in writes
    assert ("sku-C",) in writes
