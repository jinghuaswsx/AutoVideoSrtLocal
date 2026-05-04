"""完备性看板查询测试（数据装配 + 排序）。"""
from __future__ import annotations

from datetime import date

import pytest

from appcore import order_analytics as oa
from appcore.order_analytics.cost_completeness import get_completeness_overview


def _fake_query_factory(products: list[dict], stats: list[dict]):
    """构造 fake query：第一次调用返回 products 行，第二次返回 stats 行。"""
    state = {"call": 0}

    def fake_query(sql, args=()):
        state["call"] += 1
        if "FROM media_products" in sql:
            return products
        if "FROM dianxiaomi_order_lines" in sql:
            return stats
        return []

    return fake_query


def test_overview_includes_all_products_even_without_orders(monkeypatch):
    products = [
        {"id": 1, "product_code": "abc-rjc", "name": "ABC", "purchase_price": 15.50,
         "packet_cost_actual": 20.50, "packet_cost_estimated": None},
        {"id": 2, "product_code": "xyz-rjc", "name": "XYZ", "purchase_price": None,
         "packet_cost_actual": None, "packet_cost_estimated": None},
    ]
    stats = [
        {"product_id": 1, "order_lines": 100, "gmv": 5000.0},
        # product 2 没订单
    ]
    monkeypatch.setattr(oa, "query", _fake_query_factory(products, stats))

    overview = get_completeness_overview(lookback_days=30)
    pids = [row["product_id"] for row in overview]
    assert 1 in pids
    assert 2 in pids


def test_overview_sorted_incomplete_first_then_by_gmv_desc(monkeypatch):
    products = [
        {"id": 1, "product_code": "a", "name": "A", "purchase_price": 15.50,
         "packet_cost_actual": 20.50, "packet_cost_estimated": None},  # 完备
        {"id": 2, "product_code": "b", "name": "B", "purchase_price": None,
         "packet_cost_actual": None, "packet_cost_estimated": None},  # 不完备 / 高 GMV
        {"id": 3, "product_code": "c", "name": "C", "purchase_price": None,
         "packet_cost_actual": 5.00, "packet_cost_estimated": None},  # 不完备 / 低 GMV
    ]
    stats = [
        {"product_id": 1, "order_lines": 100, "gmv": 9000.0},
        {"product_id": 2, "order_lines": 50, "gmv": 5000.0},
        {"product_id": 3, "order_lines": 20, "gmv": 1000.0},
    ]
    monkeypatch.setattr(oa, "query", _fake_query_factory(products, stats))

    overview = get_completeness_overview(lookback_days=30)
    # 期望顺序：incomplete 按 GMV 降序在前 → 2, 3，然后 complete → 1
    assert [row["product_id"] for row in overview] == [2, 3, 1]


def test_overview_marks_missing_fields(monkeypatch):
    products = [
        {"id": 1, "product_code": "a", "name": "A", "purchase_price": None,
         "packet_cost_actual": None, "packet_cost_estimated": None},
    ]
    stats = []
    monkeypatch.setattr(oa, "query", _fake_query_factory(products, stats))

    overview = get_completeness_overview(lookback_days=30)
    assert len(overview) == 1
    assert overview[0]["completeness"]["ok"] is False
    assert "purchase_price" in overview[0]["completeness"]["missing"]
    assert "packet_cost" in overview[0]["completeness"]["missing"]


def test_overview_returns_recent_order_stats(monkeypatch):
    products = [
        {"id": 1, "product_code": "a", "name": "A", "purchase_price": 15.50,
         "packet_cost_actual": 20.50, "packet_cost_estimated": None},
    ]
    stats = [{"product_id": 1, "order_lines": 100, "gmv": 5000.0}]
    monkeypatch.setattr(oa, "query", _fake_query_factory(products, stats))

    overview = get_completeness_overview(lookback_days=30)
    assert overview[0]["order_lines"] == 100
    assert overview[0]["gmv_usd"] == 5000.0
    assert overview[0]["lookback_days"] == 30


def test_overview_handles_zero_orders_safely(monkeypatch):
    products = [
        {"id": 1, "product_code": "a", "name": "A", "purchase_price": None,
         "packet_cost_actual": None, "packet_cost_estimated": None},
    ]
    stats = []
    monkeypatch.setattr(oa, "query", _fake_query_factory(products, stats))

    overview = get_completeness_overview(lookback_days=30)
    assert overview[0]["order_lines"] == 0
    assert overview[0]["gmv_usd"] == 0.0
