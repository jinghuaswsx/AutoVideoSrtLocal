"""Smoke test for video-translation copy LLM binding migration."""
from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_13_video_translate_copy_gemini3_flash_bindings.sql")

COPY_TEXT_USE_CASES = (
    "video_translate.localize",
    "video_translate.tts_script",
    "video_translate.rewrite",
    "video_translate.source_normalize",
    "video_translate.av_localize",
    "video_translate.av_rewrite",
    "asr_normalize.translate_zh_to_en",
    "asr_normalize.translate_es_to_en",
    "asr_normalize.translate_generic_to_en",
    "ja_translate.localize",
    "ja_translate.rewrite",
    "translate_lab.shot_translate",
    "translate_lab.tts_refine",
)


def test_video_translate_copy_bindings_migration_forces_openrouter_gemini_3_flash():
    sql = MIGRATION.read_text(encoding="utf-8")

    for code in COPY_TEXT_USE_CASES:
        assert code in sql
    assert "'openrouter'" in sql
    assert "'google/gemini-3-flash-preview'" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "provider_code = VALUES(provider_code)" in sql
    assert "model_id = VALUES(model_id)" in sql
    assert "enabled = VALUES(enabled)" in sql
