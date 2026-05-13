"""Smoke test for AV rewrite Gemini Flash binding migration."""
from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_13_av_rewrite_gemini_flash_binding.sql")


def test_migration_updates_only_old_default_av_rewrite_binding():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "Docs-anchor: docs/superpowers/specs/2026-05-13-omni-sentence-reconcile-parallel-ui-design.md" in sql
    assert "video_translate.av_rewrite" in sql
    assert "google/gemini-3-flash-preview" in sql
    assert "openai/gpt-5.5" in sql
    assert "anthropic/claude-sonnet-4.6" in sql
    assert "provider_code = 'openrouter'" in sql
    assert "model_id IN" in sql
