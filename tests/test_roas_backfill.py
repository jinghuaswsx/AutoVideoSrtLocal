from datetime import datetime
from decimal import Decimal

import pytest

from appcore import roas_backfill as mod


def test_shopify_pricing_modes_picks_top_frequency(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [
        {"product_id": 100, "lineitem_price": Decimal("29.95"), "shipping": Decimal("6.99"), "freq": 502},
        {"product_id": 100, "lineitem_price": Decimal("29.95"), "shipping": Decimal("10.99"), "freq": 90},
        {"product_id": 100, "lineitem_price": Decimal("31.10"), "shipping": Decimal("6.99"), "freq": 8},
        {"product_id": 200, "lineitem_price": Decimal("19.99"), "shipping": None, "freq": 5},
    ])
    out = mod._shopify_pricing_modes()
    by_pid = {r["product_id"]: r for r in out}
    assert by_pid[100]["price"] == Decimal("29.95")
    assert by_pid[100]["shipping"] == Decimal("6.99")
    assert by_pid[100]["sample_size"] == 502 + 90 + 8
    assert by_pid[200]["price"] == Decimal("19.99")
    assert by_pid[200]["shipping"] is None


def test_backfill_shopify_default_uses_coalesce(monkeypatch):
    captured = []
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [
        {"product_id": 5, "lineitem_price": Decimal("10"), "shipping": Decimal("3"), "freq": 100},
    ])
    monkeypatch.setattr(mod, "execute", lambda sql, params: captured.append((sql, params)))
    result = mod.backfill_shopify_fields()
    assert result == {"candidates": 1, "updated": 1}
    sql, params = captured[0]
    assert "COALESCE" in sql
    assert params == (Decimal("10"), Decimal("3"), 5)


def test_backfill_shopify_force_overwrites(monkeypatch):
    captured = []
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [
        {"product_id": 7, "lineitem_price": Decimal("12"), "shipping": Decimal("4"), "freq": 5},
    ])
    monkeypatch.setattr(mod, "execute", lambda sql, params: captured.append((sql, params)))
    mod.backfill_shopify_fields(force=True)
    sql, params = captured[0]
    assert "COALESCE" not in sql
    assert "standalone_price=%s" in sql
    assert params == (Decimal("12"), Decimal("4"), 7)


def test_backfill_shopify_dry_run_does_not_write(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [
        {"product_id": 1, "lineitem_price": Decimal("9"), "shipping": Decimal("2"), "freq": 3},
    ])

    def must_not_call(*a, **kw):
        raise AssertionError("execute should not be called in dry-run")

    monkeypatch.setattr(mod, "execute", must_not_call)
    result = mod.backfill_shopify_fields(dry_run=True)
    assert result == {"candidates": 1, "updated": 0}


def test_query_logistic_fees_by_pid(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"product_id": 1, "logistic_fee": 61.6},
        {"product_id": 1, "logistic_fee": 62.1},
        {"product_id": 2, "logistic_fee": 80.0},
        {"product_id": 3, "logistic_fee": 12.5},
    ])
    from datetime import datetime
    fees = mod._query_logistic_fees_by_pid(
        {1, 2, 3},
        datetime(2026, 4, 2),
        datetime(2026, 5, 2),
    )
    assert sorted(fees[1]) == [61.6, 62.1]
    assert fees[2] == [80.0]
    assert fees[3] == [12.5]
    assert 4 not in fees


def test_dianxiaomi_shop_groups_picks_dominant_shop(monkeypatch):
    rows = {
        "pids": [{"id": 100}, {"id": 200}],
        "pairs": [
            {"product_id": 100, "dxm_shop_id": "shopA", "n": 30},
            {"product_id": 100, "dxm_shop_id": "shopB", "n": 5},
            {"product_id": 200, "dxm_shop_id": "shopA", "n": 50},
        ],
    }
    call = {"i": 0}

    def fake_query(sql, params=None):
        call["i"] += 1
        if "FROM media_products" in sql:
            return rows["pids"]
        return rows["pairs"]

    monkeypatch.setattr(mod, "query", fake_query)
    pid_to_shop, shop_to_pids = mod._dianxiaomi_shop_groups(force=False)
    assert pid_to_shop[100] == "shopA"
    assert pid_to_shop[200] == "shopA"
    assert shop_to_pids["shopA"] == {100, 200}


def test_dianxiaomi_shop_groups_force_skips_null_filter(monkeypatch):
    captured = {}

    def fake_query(sql, params=None):
        captured.setdefault("sqls", []).append(sql)
        if "FROM media_products" in sql:
            return [{"id": 1}]
        return []

    monkeypatch.setattr(mod, "query", fake_query)
    mod._dianxiaomi_shop_groups(force=True)
    media_products_sql = next(s for s in captured["sqls"] if "FROM media_products" in s)
    assert "packet_cost_actual IS NULL" not in media_products_sql
    assert "1 = 1" in media_products_sql


def test_sku_to_pid_map_builds_lookup(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"product_id": 1, "product_sku": "sku-1box", "product_display_sku": "sku-1box", "n": 100},
        {"product_id": 1, "product_sku": "sku-1box", "product_display_sku": "115-1", "n": 5},
        {"product_id": 2, "product_sku": "WW-1", "product_display_sku": "WW-1", "n": 10},
    ])
    mapping = mod._sku_to_pid_map({1, 2})
    assert mapping["sku-1box"] == 1
    assert mapping["115-1"] == 1
    assert mapping["WW-1"] == 2


def test_sku_to_pid_map_empty_returns_empty(monkeypatch):
    assert mod._sku_to_pid_map(set()) == {}


def test_backfill_parcel_costs_end_to_end(monkeypatch):
    monkeypatch.setattr(mod, "_dianxiaomi_shop_groups", lambda force: (
        {1: "shopA", 2: "shopA"},
        {"shopA": {1, 2}},
    ))
    # _query_logistic_fees_by_pid → SQL 聚合 logistic_fee
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"product_id": 1, "logistic_fee": 61.6},
        {"product_id": 1, "logistic_fee": 62.1},
        {"product_id": 1, "logistic_fee": 80.0},
        {"product_id": 2, "logistic_fee": 30.0},
        {"product_id": 2, "logistic_fee": 32.0},
    ])

    writes = []
    monkeypatch.setattr(mod, "execute", lambda sql, params: writes.append(params))

    fixed_now = datetime(2026, 5, 4, 12, 0, 0)
    result = mod.backfill_parcel_costs_via_dxm(
        days=30,
        now_func=lambda: fixed_now,
    )
    assert result["candidates"] == 2
    assert result["shops"] == 1
    assert result["with_fees"] == 2
    assert result["updated"] == 2
    assert result["window_start"] == "2026-04-02"
    assert result["window_end"] == "2026-05-02"
    pid1 = next(p for p in writes if p[2] == 1)
    pid2 = next(p for p in writes if p[2] == 2)
    # product 1 fees: 61.6, 62.1, 80.0 -> median 62.1
    assert pid1[0] == pytest.approx(62.1)
    assert pid1[1] == pytest.approx(62.1)
    # product 2 fees: 30, 32 -> median 31.0
    assert pid2[0] == pytest.approx(31.0)


def test_backfill_parcel_costs_force_uses_overwrite_sql(monkeypatch):
    monkeypatch.setattr(mod, "_dianxiaomi_shop_groups", lambda force: (
        {1: "shopA"}, {"shopA": {1}},
    ))
    monkeypatch.setattr(mod, "query", lambda sql, params=None: [
        {"product_id": 1, "logistic_fee": 50.0},
    ])

    captured_sqls = []
    monkeypatch.setattr(mod, "execute", lambda sql, params: captured_sqls.append(sql))
    mod.backfill_parcel_costs_via_dxm(
        force=True,
        now_func=lambda: datetime(2026, 5, 4),
    )
    assert any("packet_cost_estimated=%s" in s and "COALESCE" not in s for s in captured_sqls)


def test_backfill_parcel_costs_no_candidates_returns_zero(monkeypatch):
    monkeypatch.setattr(mod, "_dianxiaomi_shop_groups", lambda force: ({}, {}))
    result = mod.backfill_parcel_costs_via_dxm()
    assert result == {"candidates": 0, "shops": 0, "with_fees": 0, "updated": 0}
