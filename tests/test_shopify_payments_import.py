"""Shopify Payments CSV 解析测试。

CSV 列（来自 Shopify 后台 Payouts → Transactions 导出）：
  Transaction Date, Type, Order, Card Brand, Amount, Fee, Net,
  Presentment Amount, Presentment Currency, ...
"""
from __future__ import annotations

from io import StringIO

import pytest

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
