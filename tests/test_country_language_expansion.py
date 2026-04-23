from pathlib import Path


NEW_COUNTRY_LANGS = {
    "nl": "荷兰语",
    "sv": "瑞典语",
    "fi": "芬兰语",
}


def test_media_language_migration_seeds_new_country_languages_enabled():
    sql = Path("db/migrations/2026_04_23_add_nl_sv_fi_languages.sql").read_text(
        encoding="utf-8"
    )

    for code, name_zh in NEW_COUNTRY_LANGS.items():
        assert f"('{code}', '{name_zh}'" in sql
        assert "enabled = VALUES(enabled)" in sql


def test_video_translation_supports_new_country_languages():
    from appcore.video_translate_defaults import VIDEO_SUPPORTED_LANGS
    from appcore import bulk_translate_runtime as btr
    from pipeline.languages import registry
    from pipeline.languages.prompt_defaults import DEFAULTS
    from web.routes import admin_prompts, multi_translate, translate_lab

    for code in NEW_COUNTRY_LANGS:
        assert code in VIDEO_SUPPORTED_LANGS
        assert code in btr._MULTI_TRANSLATE_SUPPORTED_LANGS
        assert code in registry.SUPPORTED_LANGS
        assert code in admin_prompts.SUPPORTED_LANGS
        assert code in multi_translate.SUPPORTED_LANGS
        assert code in translate_lab._ALLOWED_TARGET_LANGUAGES
        for slot in ("base_translation", "base_tts_script", "base_rewrite"):
            assert (slot, code) in DEFAULTS


def test_av_translate_targets_and_markets_include_new_countries():
    from appcore.av_translate_inputs import (
        AV_TARGET_LANGUAGE_CODES,
        AV_TARGET_LANGUAGE_NAME_MAP,
        AV_TARGET_MARKET_CODES,
    )

    assert {"nl", "sv", "fi"}.issubset(AV_TARGET_LANGUAGE_CODES)
    assert AV_TARGET_LANGUAGE_NAME_MAP["nl"] == "Dutch"
    assert AV_TARGET_LANGUAGE_NAME_MAP["sv"] == "Swedish"
    assert AV_TARGET_LANGUAGE_NAME_MAP["fi"] == "Finnish"
    assert {"NL", "SE", "FI"}.issubset(AV_TARGET_MARKET_CODES)


def test_static_language_fallbacks_include_new_country_languages():
    from appcore import image_translate_settings as its
    from web import preview_artifacts
    from web.routes import medias, text_translate

    assert {"nl", "sv", "fi"}.issubset(set(its.SUPPORTED_LANGS))
    assert medias._DETAIL_IMAGES_ARCHIVE_COUNTRY_PREFIXES["nl"] == "荷兰"
    assert medias._DETAIL_IMAGES_ARCHIVE_COUNTRY_PREFIXES["sv"] == "瑞典"
    assert medias._DETAIL_IMAGES_ARCHIVE_COUNTRY_PREFIXES["fi"] == "芬兰"
    assert preview_artifacts._lang("nl") == "荷兰语"
    assert preview_artifacts._lang("sv") == "瑞典语"
    assert preview_artifacts._lang("fi") == "芬兰语"
    assert text_translate.LANG_MAP["nl"] == "荷兰语"
    assert text_translate.LANG_MAP["sv"] == "瑞典语"
    assert text_translate.LANG_MAP["fi"] == "芬兰语"


def test_legacy_bulk_translate_dialog_lists_new_languages():
    script = Path("web/static/bulk_translate_ui.js").read_text(encoding="utf-8")
    template = Path("web/templates/translate_lab_list.html").read_text(encoding="utf-8")
    admin_template = Path("web/templates/admin_settings.html").read_text(encoding="utf-8")

    for code, name_zh in NEW_COUNTRY_LANGS.items():
        assert f"code: '{code}'" in script
        assert f'value="{code}"' in template
        assert f'{code}:"{name_zh}"' in admin_template
