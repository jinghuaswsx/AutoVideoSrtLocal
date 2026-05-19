from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_19_zz_image_translate_local_image2_low_defaults.sql")


def test_image_translate_local_image2_low_defaults_migration_sets_fixed_cost_defaults():
    body = MIGRATION.read_text(encoding="utf-8")

    assert "Docs-anchor: docs/superpowers/specs/2026-05-19-image-translate-local-image2-low-cost-default.md" in body
    assert "'image_translate.channel', 'local_image_2'" in body
    assert "'image_translate.default_model.local_image_2', 'gpt-image-2'" in body
    assert "'image_translate.openrouter_openai_image2_enabled', '0'" in body
    assert "'video_cover_model_defaults'" not in body
