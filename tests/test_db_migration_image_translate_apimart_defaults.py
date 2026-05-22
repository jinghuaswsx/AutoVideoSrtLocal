from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_22_image_translate_apimart_image2_parallel_defaults.sql")


def test_image_translate_apimart_defaults_migration_sets_channel_and_model():
    body = MIGRATION.read_text(encoding="utf-8")

    assert "Docs-anchor: docs/superpowers/specs/2026-05-22-image-translate-apimart-image2-parallel-default.md" in body
    assert "'image_translate.channel', 'apimart'" in body
    assert "'image_translate.default_model.apimart', 'gpt-image-2'" in body
    assert "'video_cover_model_defaults'" not in body
