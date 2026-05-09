from pathlib import Path
from datetime import date

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_snapshot_migration_declares_expected_table_and_indexes():
    sql = (ROOT / "db" / "migrations" / "2026_05_10_sku_actual_breakeven_roas_snapshots.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS sku_actual_breakeven_roas_snapshots" in sql
    assert "actual_breakeven_roas DECIMAL(12,4) NULL" in sql
    assert "fee_source ENUM('real','estimated_7pct','mixed')" in sql
    assert "UNIQUE KEY uk_sku_actual_roas_window (sku, window_start, window_end)" in sql
    assert "KEY idx_sku_actual_roas_latest (sku, computed_at)" in sql
    assert "docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md" in sql


def test_calculate_window_uses_rolling_30_stable_days():
    from appcore import sku_actual_roas

    assert sku_actual_roas.calculate_window(date(2026, 5, 10)) == (
        date(2026, 4, 9),
        date(2026, 5, 8),
    )


def test_aggregate_rows_prefers_real_payment_fee_and_marks_mixed():
    from appcore import sku_actual_roas

    rows = [
        {
            "dxm_package_id": "pkg-1",
            "extended_order_id": "#1001",
            "product_display_sku": "SKU-A",
            "quantity": 1,
            "line_amount": 20,
            "ship_amount": 4,
            "logistic_fee": 6,
            "purchase_price_cny": 35,
            "xmyc_unit_price": None,
            "product_purchase_price": None,
        },
        {
            "dxm_package_id": "pkg-2",
            "extended_order_id": "#1002",
            "product_display_sku": "SKU-A",
            "quantity": 2,
            "line_amount": 30,
            "ship_amount": 0,
            "logistic_fee": 8,
            "purchase_price_cny": 40,
            "xmyc_unit_price": None,
            "product_purchase_price": None,
        },
    ]

    snapshots = sku_actual_roas.aggregate_sku_rows(rows, {"#1001": 2.4}, rmb_per_usd=7)
    row = snapshots["SKU-A"]

    assert row["orders_count"] == 2
    assert row["units"] == 3
    assert row["revenue_usd"] == pytest.approx(54.0)
    assert row["shopify_fee_usd"] == pytest.approx(2.4 + 30 * 0.07)
    assert row["fee_source"] == "mixed"
    assert row["actual_breakeven_roas"] is not None


def test_aggregate_rows_uses_7pct_when_payment_missing_and_nulls_unprofitable_roas():
    from appcore import sku_actual_roas

    rows = [
        {
            "dxm_package_id": "pkg-1",
            "extended_order_id": "#1001",
            "product_display_sku": "SKU-B",
            "quantity": 1,
            "line_amount": 10,
            "ship_amount": 0,
            "logistic_fee": 200,
            "purchase_price_cny": 100,
            "xmyc_unit_price": None,
            "product_purchase_price": None,
        },
    ]

    row = sku_actual_roas.aggregate_sku_rows(rows, {}, rmb_per_usd=7)["SKU-B"]

    assert row["fee_source"] == "estimated_7pct"
    assert row["shopify_fee_usd"] == pytest.approx(0.7)
    assert row["actual_breakeven_roas"] is None
