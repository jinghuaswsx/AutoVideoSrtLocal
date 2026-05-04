"""order_profit_lines / order_profit_runs 持久化测试。"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from appcore import order_analytics as oa
from appcore.order_analytics.profit_repository import (
    finish_profit_run,
    start_profit_run,
    upsert_profit_line,
)


def test_upsert_profit_line_ok_row(monkeypatch):
    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(oa, "execute", fake_execute)

    line_result = {
        "status": "ok",
        "dxm_order_line_id": 274668,
        "product_id": 316,
        "buyer_country": "DE",
        "shopify_tier": "D",
        "line_amount_usd": 29.95,
        "shipping_allocated_usd": 6.99,
        "revenue_usd": 36.94,
        "shopify_fee_usd": 2.15,
        "ad_cost_usd": 5.0,
        "purchase_usd": 2.27,
        "shipping_cost_usd": 3.00,
        "return_reserve_usd": 0.37,
        "profit_usd": 24.15,
        "missing_fields": [],
        "cost_basis": {"rmb_per_usd": 6.83},
    }

    upsert_profit_line(
        line_result,
        business_date=date(2026, 5, 4),
        paid_at=datetime(2026, 5, 4, 9, 44, 59),
        source_run_id=42,
    )

    assert "INSERT INTO order_profit_lines" in captured["sql"]
    assert "ON DUPLICATE KEY UPDATE" in captured["sql"]
    # args 包含 dxm_order_line_id 和 status
    assert 274668 in captured["args"]
    assert "ok" in captured["args"]


def test_upsert_profit_line_incomplete_row(monkeypatch):
    captured = {}
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): captured.setdefault("args", args))

    incomplete_result = {
        "status": "incomplete",
        "dxm_order_line_id": 999,
        "missing_fields": ["purchase_price"],
        "profit_usd": None,
    }

    upsert_profit_line(
        incomplete_result,
        business_date=date(2026, 5, 4),
        paid_at=None,
        source_run_id=42,
    )
    # incomplete 行也应该写入（profit_usd=None，status=incomplete）
    assert "incomplete" in captured["args"]
    assert 999 in captured["args"]


def test_start_profit_run_returns_run_id(monkeypatch):
    captured = {}

    class FakeCursor:
        def __init__(self):
            self.lastrowid = 100

        def execute(self, sql, args):
            captured["sql"] = sql
            captured["args"] = args

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            captured["committed"] = True

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(oa, "get_conn", lambda: FakeConn())

    run_id = start_profit_run(
        task_code="backfill",
        window_start_at=datetime(2026, 4, 1),
        window_end_at=datetime(2026, 4, 30),
        rmb_per_usd=6.83,
        return_reserve_rate=0.01,
    )
    assert run_id == 100
    assert "INSERT INTO order_profit_runs" in captured["sql"]
    assert captured["committed"] is True


def test_finish_profit_run_updates_status(monkeypatch):
    captured = {}
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): captured.setdefault("args", args))

    finish_profit_run(
        run_id=100,
        status="success",
        lines_total=100,
        lines_ok=60,
        lines_incomplete=40,
        lines_error=0,
        unallocated_ad_spend_usd=12.34,
        summary={"note": "test"},
    )
    assert 100 in captured["args"]  # run_id
    assert "success" in captured["args"]
    assert 60 in captured["args"]  # lines_ok
