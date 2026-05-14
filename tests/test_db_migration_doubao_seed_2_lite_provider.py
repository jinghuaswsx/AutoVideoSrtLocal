"""Smoke test for the dedicated Doubao Seed 2.0 Lite provider row migration."""
from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_14_doubao_seed_2_lite_provider.sql")


def test_doubao_seed_2_lite_provider_migration_seeds_dedicated_row_without_key():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "doubao_seed_2_lite" in sql
    assert "doubao-seed-2-0-lite-260215" in sql
    assert "https://ark.cn-beijing.volces.com/api/v3" in sql
    assert "INSERT IGNORE INTO llm_provider_configs" in sql
    assert "api_key" not in sql.lower()
    assert "ark-" not in sql
