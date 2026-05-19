from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_19_openrouter_image2_low_defaults.sql")


def test_openrouter_image2_low_defaults_migration_sets_cheapest_defaults():
    body = MIGRATION.read_text(encoding="utf-8")

    assert "Docs-anchor: docs/superpowers/specs/2026-04-24-openrouter-openai-image2-image-translate-design.md" in body
    assert "'image_translate.channel', 'openrouter'" in body
    assert "'image_translate.openrouter_openai_image2_enabled', '1'" in body
    assert "'image_translate.openrouter_openai_image2_default_quality', 'low'" in body
    assert "'image_translate.default_model.openrouter', 'openai/gpt-5.4-image-2:low'" in body
    assert "'video_cover_model_defaults'" in body
    assert "'$.cover_generation.provider', 'openrouter'" in body
    assert "'$.cover_generation.model_id', 'openai/gpt-5.4-image-2:low'" in body
    assert "'$.cover_generation.execution_mode', 'parallel'" in body
    assert "ON DUPLICATE KEY UPDATE" in body
