from pathlib import Path
from datetime import date, datetime

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


def test_compute_loads_orders_payments_and_upserts_snapshots(monkeypatch):
    from appcore import sku_actual_roas

    calls = {"execute": []}

    def fake_query(sql, params=()):
        if "FROM dianxiaomi_order_lines" in sql:
            assert params == (date(2026, 4, 9), date(2026, 5, 8))
            return [
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
                }
            ]
        if "FROM shopify_payments_transactions" in sql:
            assert params == ("#1001",)
            return [{"order_name": "#1001", "fee": 2.4}]
        raise AssertionError(sql)

    monkeypatch.setattr(sku_actual_roas, "query", fake_query)
    monkeypatch.setattr(
        sku_actual_roas,
        "execute",
        lambda sql, params: calls["execute"].append((sql, params)) or 1,
    )

    result = sku_actual_roas.compute_sku_actual_breakeven_roas(
        date(2026, 4, 9),
        date(2026, 5, 8),
        rmb_per_usd=7,
        source_run_id=99,
    )

    assert result["skus"] == 1
    assert result["snapshots_written"] == 1
    assert "ON DUPLICATE KEY UPDATE" in calls["execute"][0][0]
    assert calls["execute"][0][1][0] == "SKU-A"
    assert calls["execute"][0][1][12] == 99


def test_sku_actual_roas_uses_db_helpers_by_default():
    source = (ROOT / "appcore" / "sku_actual_roas.py").read_text(encoding="utf-8")

    assert "from appcore.db import execute, query" in source
    assert "sys.modules[__package__]" not in source


def test_get_latest_sku_actual_roas_returns_map(monkeypatch):
    from appcore import sku_actual_roas

    def fake_query(sql, params=()):
        assert "MAX(computed_at)" in sql
        assert params == ("SKU-A", "SKU-B")
        return [
            {
                "sku": "SKU-A",
                "window_start": date(2026, 4, 9),
                "window_end": date(2026, 5, 8),
                "orders_count": 2,
                "units": 3,
                "actual_breakeven_roas": 2.3456,
                "fee_source": "mixed",
                "computed_at": datetime(2026, 5, 10, 0, 0, 8),
            }
        ]

    monkeypatch.setattr(sku_actual_roas, "query", fake_query)

    out = sku_actual_roas.get_latest_sku_actual_roas(["SKU-A", "SKU-B"])

    assert out["SKU-A"]["value"] == 2.3456
    assert out["SKU-A"]["fee_source"] == "mixed"
    assert out["SKU-A"]["window_start"] == "2026-04-09"
    assert out["SKU-A"]["computed_at"] == "2026-05-10T00:00:08"


def test_run_snapshot_records_scheduled_task_run(monkeypatch):
    from tools import sku_actual_roas_snapshot

    calls = []

    monkeypatch.setattr(
        sku_actual_roas_snapshot.scheduled_tasks,
        "start_run",
        lambda code: calls.append(("start", code)) or 99,
    )

    def fake_finish(run_id, *, status, summary=None, error_message=None):
        calls.append(("finish", run_id, status, summary, error_message))

    monkeypatch.setattr(sku_actual_roas_snapshot.scheduled_tasks, "finish_run", fake_finish)

    def fake_compute(window_start, window_end, *, source_run_id=None):
        assert window_start == date(2026, 4, 9)
        assert window_end == date(2026, 5, 8)
        assert source_run_id == 99
        return {"skus": 2, "snapshots_written": 2}

    monkeypatch.setattr(
        sku_actual_roas_snapshot.sku_actual_roas,
        "compute_sku_actual_breakeven_roas",
        fake_compute,
    )

    summary = sku_actual_roas_snapshot.run_snapshot(run_date=date(2026, 5, 10))

    assert summary["window_start"] == "2026-04-09"
    assert summary["window_end"] == "2026-05-08"
    assert summary["skus"] == 2
    assert calls[0] == ("start", "sku_actual_breakeven_roas")
    assert calls[1][0:3] == ("finish", 99, "success")
