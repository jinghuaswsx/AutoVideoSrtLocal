import pytest


def _mock_languages(monkeypatch, rows):
    import appcore.medias as medias

    monkeypatch.setattr(medias, "list_languages", lambda: rows)


def test_list_title_translate_languages_filters_out_en_and_disabled(monkeypatch):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "de", "name_zh": "德语", "enabled": 1},
            {"code": "fr", "name_zh": "法语", "enabled": 0},
            {"code": "nl", "name_zh": "荷兰语", "enabled": True},
        ],
    )

    langs = tts.list_title_translate_languages()
    assert [lang["code"] for lang in langs] == ["de", "nl"]


def test_get_title_translate_language_rejects_en_unknown_and_disabled(monkeypatch):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "de", "name_zh": "德语", "enabled": 1},
            {"code": "nl", "name_zh": "荷兰语", "enabled": 0},
        ],
    )

    assert tts.get_title_translate_language("  DE ").get("code") == "de"

    with pytest.raises(ValueError):
        tts.get_title_translate_language("en")
    with pytest.raises(ValueError):
        tts.get_title_translate_language("xx")
    with pytest.raises(ValueError):
        tts.get_title_translate_language("nl")


def test_get_prompt_returns_builtin_german_template(monkeypatch):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [{"code": "de", "name_zh": "德语", "enabled": 1}],
    )

    prompt = tts.get_prompt("de")
    assert "德语本土化专家" in prompt
    assert "Bundesdeutsch" in prompt
    assert "{{SOURCE_TEXT}}" in prompt


def test_get_prompt_returns_generic_fallback_for_dynamic_language(monkeypatch):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [{"code": "nl", "name_zh": "荷兰语", "enabled": 1}],
    )

    prompt = tts.get_prompt(" nl ")
    assert "荷兰语" in prompt
    assert "{{SOURCE_TEXT}}" in prompt
    assert "本土化专家" in prompt
