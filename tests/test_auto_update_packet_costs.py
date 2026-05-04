from datetime import datetime, timedelta

import pytest

from tools.auto_update_packet_costs import (
    _compute_stats,
    _fetch_fees_by_product,
    update_packet_costs,
)


def _make_query_rows(rows):
    def fake_query(sql, params=None):
        return list(rows)
    return fake_query


def _fake_execute():
    """返回 (calls, execute_fn)，execute_fn 记录每次调用参数。"""
    calls = []

    def fn(sql, params=None):
        calls.append({"sql": sql, "params": params})

    return calls, fn


def test_compute_stats_basic():
    mean, median, n = _compute_stats([10.0, 20.0, 30.0, 40.0, 50.0])
    assert mean == 30.0
    assert median == 30.0
    assert n == 5


def test_compute_stats_odd_count():
    mean, median, n = _compute_stats([10.0, 20.0, 70.0])
    assert mean == pytest.approx(33.33, abs=0.01)
    assert median == 20.0
    assert n == 3


def test_fetch_fees_by_product_groups_by_pid(monkeypatch):
    monkeypatch.setattr(
        "tools.auto_update_packet_costs.query",
        _make_query_rows([
            {"product_id": 1, "logistic_fee": 10.0},
            {"product_id": 1, "logistic_fee": 20.0},
            {"product_id": 2, "logistic_fee": 30.0},
        ]),
    )
    now = datetime(2026, 5, 4, 12, 0, 0)
    end = now - timedelta(days=2)
    start = end - timedelta(days=30)
    by_pid = _fetch_fees_by_product(start, end)
    assert by_pid == {1: [10.0, 20.0], 2: [30.0]}


def test_fetch_fees_by_product_excludes_null_and_zero(monkeypatch):
    """SQL 层已过滤 NULL 和 ≤0 的值，返回的只有正值。"""
    monkeypatch.setattr(
        "tools.auto_update_packet_costs.query",
        _make_query_rows([
            {"product_id": 1, "logistic_fee": 15.0},
        ]),
    )
    now = datetime(2026, 5, 4)
    end = now - timedelta(days=2)
    start = end - timedelta(days=30)
    by_pid = _fetch_fees_by_product(start, end)
    assert by_pid == {1: [15.0]}


def test_update_packet_costs_skips_below_min_sample(monkeypatch):
    """样本 < 5 → 不更新。"""
    monkeypatch.setattr(
        "tools.auto_update_packet_costs.query",
        _make_query_rows([{"product_id": 1, "logistic_fee": 10.0}]),
    )
    calls, fake_exec = _fake_execute()
    monkeypatch.setattr("tools.auto_update_packet_costs.execute", fake_exec)

    result = update_packet_costs(lookback_days=30)
    assert result["products_total"] == 1
    assert result["products_updated"] == 0
    assert result["products_skipped"] == 1
    assert len(calls) == 0


def test_update_packet_costs_updates_products_with_enough_samples(monkeypatch):
    """样本 ≥ 5 → 更新均值和中位数。"""
    fees = [10.0, 20.0, 30.0, 40.0, 50.0]
    monkeypatch.setattr(
        "tools.auto_update_packet_costs.query",
        _make_query_rows([{"product_id": 316, "logistic_fee": f} for f in fees]),
    )
    calls, fake_exec = _fake_execute()
    monkeypatch.setattr("tools.auto_update_packet_costs.execute", fake_exec)

    result = update_packet_costs(lookback_days=30)
    assert result["products_total"] == 1
    assert result["products_updated"] == 1
    assert result["products_skipped"] == 0
    assert len(calls) == 1
    mean, median, pid = calls[0]["params"]
    assert pid == 316
    assert mean == 30.0
    assert median == 30.0
