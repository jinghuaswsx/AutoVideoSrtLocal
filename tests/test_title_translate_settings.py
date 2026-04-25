import pytest


def _mock_languages(monkeypatch, rows):
    import appcore.medias as medias

    monkeypatch.setattr(medias, "list_languages", lambda: rows)


def _assert_structured_prompt(prompt):
    assert "标题:" in prompt
    assert "文案:" in prompt
    assert "描述:" in prompt
    assert "标题:[...]" not in prompt
    assert "文案:[...]" not in prompt
    assert "描述:[...]" not in prompt
    assert "方括号" in prompt
    assert "不允许" in prompt and "保留" in prompt and "英文" in prompt
    assert "- 标题最多 100 个字符。" in prompt
    assert "- 文案最多 200 个字符。" in prompt
    assert "- 描述最多 50 个字符。" in prompt
    assert "{{SOURCE_TEXT}}" in prompt
    assert prompt.count("{{SOURCE_TEXT}}") == 1


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


def test_get_prompt_requires_structured_three_part_input_and_output(monkeypatch):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [{"code": "de", "name_zh": "德语", "enabled": 1}],
    )

    prompt = tts.get_prompt("de")
    _assert_structured_prompt(prompt)


@pytest.mark.parametrize(
    ("code", "name_zh", "expected_bits"),
    [
        ("de", "德语", ["德语本土化专家", "Bundesdeutsch"]),
        ("fr", "法语", ["法语本土化专家", "法语用户"]),
        ("es", "西班牙语", ["西班牙语本土化专家", "西语用户"]),
        ("it", "意大利语", ["意大利语本土化专家", "意大利用户"]),
        ("ja", "日语", ["日语本土化专家", "日本用户"]),
        ("pt", "葡萄牙语", ["葡萄牙语本土化专家", "葡语用户"]),
    ],
)
def test_get_prompt_special_languages_keep_localized_signals_and_placeholder(monkeypatch, code, name_zh, expected_bits):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [{"code": code, "name_zh": name_zh, "enabled": 1}],
    )

    prompt = tts.get_prompt(code)
    _assert_structured_prompt(prompt)
    for bit in expected_bits:
        assert bit in prompt


def test_get_prompt_returns_generic_fallback_for_dynamic_language(monkeypatch):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [{"code": "nl", "name_zh": "荷兰语", "enabled": 1}],
    )

    prompt = tts.get_prompt(" nl ")
    _assert_structured_prompt(prompt)
    assert "荷兰语" in prompt
    assert "本土化专家" not in prompt
