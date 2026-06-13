from pathlib import Path


MIGRATION = Path("db/migrations/2026_06_13_dynamic_shopify_fee_rates.sql")


def test_dynamic_shopify_fee_migration_contains_snapshot_table_and_trace_columns():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create table if not exists shopify_fee_rate_snapshots" in sql
    assert "store_code" in sql
    assert "region" in sql
    assert "effective_rate" in sql
    assert "variable_rate" in sql
    assert "sample_status" in sql
    assert "source_csvs_json" in sql

    assert "alter table order_profit_lines" in sql
    assert "shopify_fee_source" in sql
    assert "shopify_fee_rate" in sql
    assert "shopify_fee_rate_region" in sql
    assert "shopify_fee_rate_window_start" in sql
    assert "shopify_fee_rate_window_end" in sql
    assert "shopify_fee_basis_json" in sql

    assert "alter table shopify_payments_transactions" in sql
    assert "transaction_date" in sql


def test_dynamic_shopify_fee_migration_has_lookup_indexes():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "idx_fee_snapshots_lookup" in sql
    assert "idx_profit_fee_source" in sql
