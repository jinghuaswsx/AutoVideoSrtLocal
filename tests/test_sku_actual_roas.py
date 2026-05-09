from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_snapshot_migration_declares_expected_table_and_indexes():
    sql = (ROOT / "db" / "migrations" / "2026_05_10_sku_actual_breakeven_roas_snapshots.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS sku_actual_breakeven_roas_snapshots" in sql
    assert "actual_breakeven_roas DECIMAL(12,4) NULL" in sql
    assert "fee_source ENUM('real','estimated_7pct','mixed')" in sql
    assert "UNIQUE KEY uk_sku_actual_roas_window (sku, window_start, window_end)" in sql
    assert "KEY idx_sku_actual_roas_latest (sku, computed_at)" in sql
    assert "docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md" in sql
