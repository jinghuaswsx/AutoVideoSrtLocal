"""Smoke test for Doubao Seed 2.0 Lite exact price migration."""
from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_06_doubao_seed_2_lite_price.sql")


def test_doubao_seed_2_lite_price_migration_uses_exact_official_rates():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "doubao-seed-2-0-lite-260215" in sql
    assert "'doubao'" in sql
    assert "'tokens'" in sql
    assert "0.00000060" in sql
    assert "0.00000360" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "unit_input_cny=VALUES(unit_input_cny)" in sql
    assert "unit_output_cny=VALUES(unit_output_cny)" in sql
