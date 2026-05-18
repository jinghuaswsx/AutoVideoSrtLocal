from pathlib import Path


def test_meta_hot_posts_translate_gemini3_flash_binding_migration():
    body = Path(
        "db/migrations/2026_05_18_meta_hot_posts_translate_message_gemini3_flash_binding.sql"
    ).read_text(encoding="utf-8")

    assert "Docs-anchor: docs/superpowers/specs/2026-05-18-meta-hot-posts-translate-model-and-schedule-design.md" in body
    assert "'meta_hot_posts.translate_message'" in body
    assert "'openrouter'" in body
    assert "'google/gemini-3-flash-preview'" in body
    assert "'google/gemini-3.1-flash-lite'" not in body
    assert "'title_translate.generate'" not in body
    assert "'copywriting_translate.generate'" not in body
    assert "ON DUPLICATE KEY UPDATE" in body
    assert "provider_code = VALUES(provider_code)" in body
    assert "model_id = VALUES(model_id)" in body
    assert "enabled = VALUES(enabled)" in body
