"""Smoke test for AV sync GPT-5.5 binding migration."""
from pathlib import Path


MIGRATION = Path("db/migrations/2026_04_28_av_sync_gpt55_bindings.sql")


def test_migration_updates_only_old_default_av_sync_bindings():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "video_translate.av_localize" in sql
    assert "video_translate.av_rewrite" in sql
    assert "openai/gpt-5.5" in sql
    assert "anthropic/claude-sonnet-4.6" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "provider_code = 'openrouter'" in sql
    assert "model_id = 'anthropic/claude-sonnet-4.6'" in sql
    assert "enabled" in sql
    assert "VALUES" in sql
