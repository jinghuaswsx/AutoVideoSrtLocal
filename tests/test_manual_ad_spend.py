"""Tests for appcore.order_analytics.manual_ad_spend DAO + routes.

详细设计：docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from appcore.db import get_conn
from appcore.order_analytics import manual_ad_spend


@pytest.fixture(autouse=True)
def _clean_table():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM meta_ad_manual_daily_spend")
        conn.commit()
    yield
    with conn.cursor() as cur:
        cur.execute("DELETE FROM meta_ad_manual_daily_spend")
        conn.commit()


def test_upsert_entries_inserts_new_rows():
    written = manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[
            {"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "300.00"},
            {"account_code": "Omurio",    "ad_account_id": "1253003326160754", "spend_usd": "200.50"},
        ],
        updated_by=7,
    )
    assert written == 2

    rows = manual_ad_spend.list_range(date(2026, 5, 8), date(2026, 5, 8))
    by_code = {r["account_code"]: r for r in rows}
    assert set(by_code) == {"newjoyloo", "Omurio"}
    assert by_code["newjoyloo"]["spend_usd"] == Decimal("300.0000")
    assert by_code["Omurio"]["spend_usd"] == Decimal("200.5000")
    assert by_code["newjoyloo"]["updated_by"] == 7


def test_upsert_updates_existing_row_and_preserves_created_at():
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "100"}],
        updated_by=7,
    )
    first = manual_ad_spend.list_range(date(2026, 5, 8), date(2026, 5, 8))[0]

    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "250.00"}],
        updated_by=9,
    )
    after = manual_ad_spend.list_range(date(2026, 5, 8), date(2026, 5, 8))[0]
    assert after["spend_usd"] == Decimal("250.0000")
    assert after["updated_by"] == 9
    assert after["created_at"] == first["created_at"]


def test_upsert_partial_entries_does_not_clear_others():
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[
            {"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "300"},
            {"account_code": "Omurio",    "ad_account_id": "1253003326160754", "spend_usd": "200"},
        ],
        updated_by=7,
    )
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "999"}],
        updated_by=9,
    )
    rows = {r["account_code"]: r for r in manual_ad_spend.list_range(date(2026, 5, 8), date(2026, 5, 8))}
    assert rows["newjoyloo"]["spend_usd"] == Decimal("999.0000")
    assert rows["Omurio"]["spend_usd"] == Decimal("200.0000")


def test_delete_entry_returns_true_when_existed():
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "100"}],
        updated_by=7,
    )
    deleted = manual_ad_spend.delete_entry(business_date=date(2026, 5, 8), account_code="newjoyloo")
    assert deleted is True
    assert manual_ad_spend.list_range(date(2026, 5, 8), date(2026, 5, 8)) == []


def test_delete_entry_returns_false_when_absent():
    deleted = manual_ad_spend.delete_entry(business_date=date(2026, 5, 8), account_code="ghost")
    assert deleted is False


def test_load_supplement_map_returns_keyed_by_date_and_account_id():
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 7),
        entries=[
            {"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "300"},
        ],
        updated_by=7,
    )
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 8),
        entries=[
            {"account_code": "Omurio", "ad_account_id": "1253003326160754", "spend_usd": "200"},
        ],
        updated_by=7,
    )
    out = manual_ad_spend.load_supplement_map(date(2026, 5, 7), date(2026, 5, 8))
    assert out == {
        (date(2026, 5, 7), "1861285821213497"): Decimal("300.0000"),
        (date(2026, 5, 8), "1253003326160754"): Decimal("200.0000"),
    }


def test_load_supplement_map_filters_by_range():
    manual_ad_spend.upsert_entries(
        business_date=date(2026, 5, 1),
        entries=[{"account_code": "newjoyloo", "ad_account_id": "1861285821213497", "spend_usd": "100"}],
        updated_by=7,
    )
    out = manual_ad_spend.load_supplement_map(date(2026, 5, 7), date(2026, 5, 8))
    assert out == {}
