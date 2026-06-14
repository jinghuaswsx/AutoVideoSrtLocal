"""Shopify Payments CSV 解析测试。

CSV 列（来自 Shopify 后台 Payouts → Transactions 导出）：
  Transaction Date, Type, Order, Card Brand, Amount, Fee, Net,
  Presentment Amount, Presentment Currency, ...
"""
from __future__ import annotations

from io import StringIO

import pytest

import appcore.order_analytics.shopify_payments_import as payments_import
from appcore.order_analytics.shopify_payments_import import (
    parse_payments_csv,
)


SAMPLE_CSV = """Transaction Date,Type,Order,Card Brand,Amount,Fee,Net,Presentment Amount,Presentment Currency,Payout Date,Payout Status
2026-04-15,charge,#1001,visa,29.94,1.05,28.89,29.94,USD,2026-04-17,paid
2026-04-15,charge,#1002,master,22.13,1.41,20.72,20.94,EUR,2026-04-17,paid
2026-04-16,refund,#1001,visa,-29.94,0.00,-29.94,-29.94,USD,2026-04-17,paid
"""


def test_parse_payments_csv_basic():
    rows = parse_payments_csv(StringIO(SAMPLE_CSV), source_csv="test.csv")
    assert len(rows) == 3

    charge1 = rows[0]
    assert charge1["transaction_date"] == "2026-04-15"
    assert charge1["type"] == "charge"
    assert charge1["order_name"] == "#1001"
    assert charge1["amount_usd"] == pytest.approx(29.94)
    assert charge1["fee_usd"] == pytest.approx(1.05)
    assert charge1["net_usd"] == pytest.approx(28.89)
    assert charge1["presentment_currency"] == "USD"
    assert charge1["card_brand"] == "visa"


def test_parse_payments_csv_handles_eur_with_presentment_amount():
    rows = parse_payments_csv(StringIO(SAMPLE_CSV), source_csv="test.csv")
    eur = rows[1]
    assert eur["presentment_currency"] == "EUR"
    assert eur["amount_usd"] == pytest.approx(22.13)


def test_parse_payments_csv_handles_refund():
    rows = parse_payments_csv(StringIO(SAMPLE_CSV), source_csv="test.csv")
    refund = rows[2]
    assert refund["type"] == "refund"
    assert refund["amount_usd"] == pytest.approx(-29.94)
    assert refund["fee_usd"] == 0.0


def test_parse_payments_csv_records_source_filename():
    rows = parse_payments_csv(StringIO(SAMPLE_CSV), source_csv="2026-04.csv")
    assert all(row["source_csv"] == "2026-04.csv" for row in rows)


def test_parse_payments_csv_runs_verify_for_charge_rows():
    rows = parse_payments_csv(StringIO(SAMPLE_CSV), source_csv="test.csv")
    # 第一条 USD 29.94 fee=1.05，反推应该匹配
    # （29.94 × 0.025 + 0.30 = 1.0485 ≈ 1.05 → domestic）
    charge = rows[0]
    assert charge.get("inferred_card_origin") in ("domestic", "international", "unknown")
    assert charge.get("matches_standard") in (0, 1)


def test_import_payments_csv_writes_transaction_date_and_refreshes_snapshots(monkeypatch):
    execute_calls = []
    refreshed = []

    def fake_execute(sql, params):
        execute_calls.append((sql, params))

    def fake_refresh(source_csvs=None):
        refreshed.append(source_csvs)
        return {"saved": 3, "window_end_date": "2026-06-06"}

    monkeypatch.setattr(payments_import, "execute", fake_execute)
    monkeypatch.setattr(payments_import, "refresh_fee_rate_snapshots", fake_refresh)
    monkeypatch.setattr(payments_import, "query_one", lambda sql, args=(): {"a": None, "b": None})

    csv_data = "\n".join(
        [
            "Transaction Date,Type,Order,Amount,Fee,Net,Presentment Currency",
            "2026-06-06,charge,#1001,10.00,0.55,9.45,USD",
        ]
    )

    result = payments_import.import_payments_csv(
        StringIO(csv_data),
        source_csv="newjoyloo__newjoyloo0606.csv",
    )

    assert result["inserted"] == 1
    assert result["fee_rate_snapshots"] == {"saved": 3, "window_end_date": "2026-06-06"}
    assert refreshed == [None]
    assert len(execute_calls) == 1
    sql, params = execute_calls[0]
    normalized_sql = sql.lower()
    assert "transaction_id, transaction_date, payout_id" in normalized_sql
    assert "transaction_date=values(transaction_date)" in normalized_sql
    assert params[1] == "2026-06-06"
    assert params[-2] == "newjoyloo__newjoyloo0606.csv"


def test_import_payments_csv_keeps_insert_success_when_snapshot_refresh_fails(monkeypatch):
    execute_calls = []

    def fake_execute(sql, params):
        execute_calls.append((sql, params))

    def fake_refresh(source_csvs=None):
        raise RuntimeError("refresh boom")

    monkeypatch.setattr(payments_import, "execute", fake_execute)
    monkeypatch.setattr(payments_import, "refresh_fee_rate_snapshots", fake_refresh)
    monkeypatch.setattr(payments_import, "query_one", lambda sql, args=(): {"a": None, "b": None})

    csv_data = "\n".join(
        [
            "Transaction Date,Type,Order,Amount,Fee,Net,Presentment Currency",
            "2026-06-06,charge,#1001,10.00,0.55,9.45,USD",
        ]
    )

    result = payments_import.import_payments_csv(
        StringIO(csv_data),
        source_csv="newjoyloo__newjoyloo0606.csv",
    )

    assert result["inserted"] == 1
    assert len(execute_calls) == 1
    assert result["fee_rate_snapshots"]["saved"] == 0
    assert result["fee_rate_snapshots"]["refresh_failed"] is True
    assert "refresh boom" in result["fee_rate_snapshots"]["error"]


def test_import_payments_triggers_recompute_for_affected_business_dates(monkeypatch):
    import io
    from datetime import date
    from appcore.order_analytics import shopify_payments_import as mod

    monkeypatch.setattr(mod, "parse_payments_csv", lambda stream, source_csv="": [
        {"transaction_id": "t1", "transaction_date": "2026-03-01", "payout_id": "p1",
         "type": "charge", "order_name": "#3001", "presentment_currency": "EUR",
         "amount_usd": 40.0, "fee_usd": 2.5, "net_usd": 37.5, "card_brand": "visa",
         "inferred_card_origin": "DE", "inferred_tier": "B", "matches_standard": 1,
         "source_csv": "newjoyloo__x.csv", "raw_row_json": "{}"},
    ])
    monkeypatch.setattr(mod, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(mod, "refresh_fee_rate_snapshots", lambda **k: {"saved": 1})
    monkeypatch.setattr(mod, "query_one", lambda sql, args=(): {"a": date(2026, 2, 24), "b": date(2026, 6, 1)})

    triggered = {}
    monkeypatch.setattr(mod, "_trigger_profit_recompute", lambda f, t: triggered.update(f=f, t=t))

    stats = mod.import_payments_csv(io.StringIO("x"), source_csv="newjoyloo__x.csv")

    assert stats["affected_business_dates"] == {"from": "2026-02-24", "to": "2026-06-01"}
    assert triggered == {"f": date(2026, 2, 24), "t": date(2026, 6, 1)}


def test_import_payments_no_recompute_when_no_matching_orders(monkeypatch):
    import io
    from appcore.order_analytics import shopify_payments_import as mod

    monkeypatch.setattr(mod, "parse_payments_csv", lambda stream, source_csv="": [
        {"transaction_id": "t9", "transaction_date": "2026-03-01", "payout_id": "p",
         "type": "charge", "order_name": "#9999", "presentment_currency": "USD",
         "amount_usd": 10.0, "fee_usd": 0.6, "net_usd": 9.4, "card_brand": "visa",
         "inferred_card_origin": "US", "inferred_tier": "A", "matches_standard": 1,
         "source_csv": "newjoyloo__x.csv", "raw_row_json": "{}"},
    ])
    monkeypatch.setattr(mod, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(mod, "refresh_fee_rate_snapshots", lambda **k: {"saved": 0})
    monkeypatch.setattr(mod, "query_one", lambda sql, args=(): {"a": None, "b": None})

    calls = []
    monkeypatch.setattr(mod, "_trigger_profit_recompute", lambda f, t: calls.append((f, t)))

    stats = mod.import_payments_csv(io.StringIO("x"), source_csv="newjoyloo__x.csv")

    assert stats["affected_business_dates"] == {"from": None, "to": None}
    assert calls == []
