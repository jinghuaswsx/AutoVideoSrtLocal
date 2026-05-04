from datetime import datetime

import pytest

from appcore import parcel_cost_suggest as mod


def _make_query_rows(*row_sets):
    """返回一个 fake query(sql, params) callable，按调用顺序返回不同行集。

    row_sets: 每个元素是 list[dict]，对应每次 query() 调用的返回值。
    """
    calls = [list(rows) for rows in row_sets]
    idx = 0

    def fake_query(sql, params=None):
        nonlocal idx
        if idx >= len(calls):
            return []
        result = calls[idx]
        idx += 1
        return result

    return fake_query


def test_pick_primary_sku_and_shop_returns_top_frequency(monkeypatch):
    monkeypatch.setattr(mod, "query", _make_query_rows([
        {"product_sku": "45697043366061", "dxm_shop_id": "8477915", "cnt": 1340},
    ]))
    sku, shop = mod.pick_primary_sku_and_shop(317)
    assert sku == "45697043366061"
    assert shop == "8477915"


def test_pick_primary_sku_and_shop_raises_when_no_orders(monkeypatch):
    monkeypatch.setattr(mod, "query", _make_query_rows([]))
    with pytest.raises(mod.ParcelCostSuggestError, match="no_orders"):
        mod.pick_primary_sku_and_shop(317)


def test_compute_suggestion_basic_stats():
    out = mod.compute_suggestion([61.606, 61.606, 51.78, 61.606, 70.0])
    assert out["sample_size"] == 5
    assert out["median"] == 61.61
    assert out["min"] == 51.78
    assert out["max"] == 70.0
    assert out["mean"] == pytest.approx(round((61.606 * 3 + 51.78 + 70.0) / 5, 2))


def test_compute_suggestion_empty_list():
    out = mod.compute_suggestion([])
    assert out == {"sample_size": 0, "median": None, "mean": None, "min": None, "max": None}


def test_suggest_parcel_cost_end_to_end(monkeypatch):
    """端到端：pick_primary_sku_and_shop + SQL 聚合 logistic_fee。"""
    monkeypatch.setattr(mod, "query", _make_query_rows(
        # 第一个 query: pick_primary_sku_and_shop
        [{"product_sku": "45697043366061", "dxm_shop_id": "8477915", "cnt": 1340}],
        # 第二个 query: suggest_parcel_cost 的 logistic_fee 聚合
        [{"logistic_fee": 61.606} for _ in range(10)] + [{"logistic_fee": 51.78}],
    ))

    fixed_now = datetime(2026, 5, 4, 12, 0, 0)
    out = mod.suggest_parcel_cost(317, days=30, now_func=lambda: fixed_now)

    assert out["product_id"] == 317
    assert out["sku"] == "45697043366061"
    assert out["dxm_shop_id"] == "8477915"
    assert out["lookback_days"] == 30
    assert out["window_end"] == "2026-05-02"
    assert out["window_start"] == "2026-04-02"
    assert out["orders_pulled"] == 11
    assert out["sample_size"] == 11
    assert out["median"] == 61.61
    assert out["min"] == 51.78
    assert out["max"] == 61.61
