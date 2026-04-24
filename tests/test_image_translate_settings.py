import pytest


def _mock_languages(monkeypatch, rows):
    import appcore.medias as medias

    monkeypatch.setattr(medias, "list_languages", lambda: rows)


def test_get_prompt_rejects_invalid_preset():
    from appcore import image_translate_settings as its
    with pytest.raises(ValueError):
        its.get_prompt("invalid", "de")


def test_get_prompt_rejects_invalid_lang(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [{"code": "de", "name_zh": "德语", "enabled": 1}],
    )

    with pytest.raises(ValueError):
        its.get_prompt("cover", "xx")


def test_list_image_translate_languages_filters_out_en(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "de", "name_zh": "德语", "enabled": 1},
            {"code": "nl", "name_zh": "荷兰语", "enabled": 1},
        ],
    )

    langs = its.list_image_translate_languages()
    assert [lang["code"] for lang in langs] == ["de", "nl"]


def test_language_support_checks_normalize_code(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "nl", "name_zh": "荷兰语", "enabled": 1},
        ],
    )

    assert its.is_image_translate_language_supported(" NL ")
    info = its._get_language_info(" NL ")
    assert info["code"] == "nl"
    assert info["name_zh"] == "荷兰语"


@pytest.mark.parametrize(
    ("code", "name_zh", "cover_language", "market_hint", "file_suffix"),
    [
        ("nl", "荷兰语", "Dutch", "荷兰及比利时弗拉芒区市场", "Dutch"),
        ("sv", "瑞典语", "Swedish", "瑞典市场", "Swedish"),
        ("fi", "芬兰语", "Finnish", "芬兰市场", "Finnish"),
    ],
)
def test_get_prompt_bootstraps_new_builtin_language_prompts_from_german_template(
    monkeypatch, code, name_zh, cover_language, market_hint, file_suffix
):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "de", "name_zh": "德语", "enabled": 1},
            {"code": code, "name_zh": name_zh, "enabled": 1},
        ],
    )

    store = {}

    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None

    def fake_execute(sql, params):
        store[params[0]] = params[1]

    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    cover = its.get_prompt("cover", code)
    detail = its.get_prompt("detail", code)

    assert f"Task: Localize this English video cover image into {cover_language}." in cover
    assert "【Core Rules】" in cover
    assert f"【{cover_language} Translation Requirements】" in cover
    assert "Output size must be strictly 1080×1920 pixels (vertical 9:16)" in cover
    assert f"only replace the English text with {cover_language}" in cover
    assert f"If the {cover_language} text is longer than the English" in cover
    assert "只替换文字，保留布局" not in cover

    assert f"英语到{name_zh}翻译" in detail
    assert f"从英语翻译成{name_zh}" in detail
    assert "### 需要翻译的内容" in detail
    assert "### 不得翻译的内容" in detail
    assert "### 文件命名" in detail
    assert market_hint in detail
    assert f"[原始文件名]-{file_suffix}.[原始扩展名]" in detail
    assert "只替换文字，保留布局" not in detail

    assert store[f"image_translate.prompt_cover_{code}"] == cover
    assert store[f"image_translate.prompt_detail_{code}"] == detail


def test_get_prompt_generates_generic_prompt_for_unlisted_dynamic_lang(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "pl", "name_zh": "波兰语", "enabled": 1},
        ],
    )

    store = {}

    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None

    def fake_execute(sql, params):
        store[params[0]] = params[1]

    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    value = its.get_prompt("cover", "pl")
    assert "波兰语" in value
    assert "只替换文字" in value
    assert "保留布局" in value
    assert store["image_translate.prompt_cover_pl"] == value


def test_get_prompt_generates_generic_detail_prompt_for_unlisted_dynamic_lang(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "pl", "name_zh": "波兰语", "enabled": 1},
        ],
    )

    store = {}

    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None

    def fake_execute(sql, params):
        store[params[0]] = params[1]

    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    value = its.get_prompt("detail", "pl")
    assert "波兰语" in value
    assert "只替换文字" in value
    assert "保留布局" in value
    assert store["image_translate.prompt_detail_pl"] == value


def test_get_prompt_replaces_stale_generic_prompt_for_new_builtin_language(monkeypatch):
    from appcore import image_translate_settings as its

    lang_info = {"code": "nl", "name_zh": "荷兰语", "enabled": 1}
    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            lang_info,
        ],
    )

    store = {
        "image_translate.prompt_cover_nl": its._build_generic_prompt("cover", lang_info),
        "image_translate.prompt_detail_nl": its._build_generic_prompt("detail", lang_info),
    }

    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None

    def fake_execute(sql, params):
        store[params[0]] = params[1]

    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    cover = its.get_prompt("cover", "nl")
    detail = its.get_prompt("detail", "nl")

    assert "【Dutch Translation Requirements】" in cover
    assert "### 文件命名" in detail
    assert store["image_translate.prompt_cover_nl"] == cover
    assert store["image_translate.prompt_detail_nl"] == detail


def test_get_prompt_keeps_custom_prompt_for_new_builtin_language(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [{"code": "nl", "name_zh": "荷兰语", "enabled": 1}],
    )

    store = {"image_translate.prompt_cover_nl": "自定义荷兰语封面 prompt"}

    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None

    def fake_execute(sql, params):
        store[params[0]] = params[1]

    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    assert its.get_prompt("cover", "nl") == "自定义荷兰语封面 prompt"
    assert store["image_translate.prompt_cover_nl"] == "自定义荷兰语封面 prompt"


def test_get_prompt_bootstraps_when_missing(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [{"code": "de", "name_zh": "德语", "enabled": 1}],
    )

    store = {}

    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None

    def fake_execute(sql, params):
        store[params[0]] = params[1]

    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    value = its.get_prompt("detail", "de")
    assert "德语" in value
    assert "DACH" in value
    assert "image_translate.prompt_detail_de" in store


def test_get_prompt_returns_user_override(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [{"code": "fr", "name_zh": "法语", "enabled": 1}],
    )

    store = {"image_translate.prompt_cover_fr": "自定义法语封面 prompt"}

    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None

    def fake_execute(sql, params):
        store[params[0]] = params[1]

    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    value = its.get_prompt("cover", "fr")
    assert value == "自定义法语封面 prompt"


def test_get_prompts_for_lang_returns_both_presets(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [{"code": "es", "name_zh": "西班牙语", "enabled": 1}],
    )

    store = {}

    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None

    def fake_execute(sql, params):
        store[params[0]] = params[1]

    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    prompts = its.get_prompts_for_lang("es")
    assert "cover" in prompts and "detail" in prompts
    assert "Spanish" in prompts["cover"]
    assert "西班牙语" in prompts["detail"]


def test_update_prompt_writes(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [{"code": "ja", "name_zh": "日语", "enabled": 1}],
    )

    calls = []

    def fake_execute(sql, params):
        calls.append(params)

    monkeypatch.setattr(its, "execute", fake_execute)

    its.update_prompt("cover", "ja", "自定义日语封面 prompt")
    assert len(calls) == 1
    assert calls[0][0] == "image_translate.prompt_cover_ja"
    assert calls[0][1] == "自定义日语封面 prompt"


def test_update_prompt_rejects_invalid(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [{"code": "de", "name_zh": "德语", "enabled": 1}],
    )

    with pytest.raises(ValueError):
        its.update_prompt("invalid", "de", "x")
    with pytest.raises(ValueError):
        its.update_prompt("cover", "xx", "x")


def _patch_store(monkeypatch, store):
    from appcore import image_translate_settings as its

    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None

    def fake_execute(sql, params):
        store[params[0]] = params[1]

    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)


def test_get_channel_returns_default_when_unset(monkeypatch):
    from appcore import image_translate_settings as its
    _patch_store(monkeypatch, {})
    assert its.get_channel() == "aistudio"


def test_get_channel_returns_persisted_value(monkeypatch):
    from appcore import image_translate_settings as its
    _patch_store(monkeypatch, {"image_translate.channel": "openrouter"})
    assert its.get_channel() == "openrouter"


def test_get_channel_falls_back_on_invalid_value(monkeypatch):
    from appcore import image_translate_settings as its
    _patch_store(monkeypatch, {"image_translate.channel": "mystery"})
    assert its.get_channel() == "aistudio"


def test_set_channel_writes_valid_value(monkeypatch):
    from appcore import image_translate_settings as its
    store = {}
    _patch_store(monkeypatch, store)
    its.set_channel("CLOUD")
    assert store["image_translate.channel"] == "cloud"


def test_set_channel_rejects_invalid(monkeypatch):
    from appcore import image_translate_settings as its
    _patch_store(monkeypatch, {})
    with pytest.raises(ValueError):
        its.set_channel("gpt-router")


def test_get_channel_accepts_doubao(monkeypatch):
    from appcore import image_translate_settings as its

    store = {}
    _patch_store(monkeypatch, store)
    its.set_channel("DOUBAO")

    assert store["image_translate.channel"] == "doubao"
    assert its.get_channel() == "doubao"


def test_get_default_model_returns_channel_default_when_unset(monkeypatch):
    from appcore import image_translate_settings as its

    _patch_store(monkeypatch, {})

    assert its.get_default_model("aistudio") == "gemini-3.1-flash-image-preview"
    assert its.get_default_model("doubao") == "doubao-seedream-5-0-260128"


def test_get_default_model_returns_persisted_model_for_channel(monkeypatch):
    from appcore import image_translate_settings as its

    _patch_store(
        monkeypatch,
        {
            "image_translate.default_model.openrouter": "gemini-3-pro-image-preview",
            "image_translate.default_model.doubao": "gemini-3-pro-image-preview",
        },
    )

    assert its.get_default_model("openrouter") == "gemini-3-pro-image-preview"
    assert its.get_default_model("doubao") == "doubao-seedream-5-0-260128"


def test_set_default_model_writes_valid_channel_model(monkeypatch):
    from appcore import image_translate_settings as its

    store = {}
    _patch_store(monkeypatch, store)

    its.set_default_model("cloud", "gemini-3-pro-image-preview")

    assert store["image_translate.default_model.cloud"] == "gemini-3-pro-image-preview"


def test_set_default_model_rejects_invalid_channel_or_model(monkeypatch):
    from appcore import image_translate_settings as its

    _patch_store(monkeypatch, {})

    with pytest.raises(ValueError):
        its.set_default_model("mystery", "gemini-3-pro-image-preview")
    with pytest.raises(ValueError):
        its.set_default_model("doubao", "gemini-3-pro-image-preview")


def test_openrouter_openai_image2_defaults(monkeypatch):
    from appcore import image_translate_settings as its

    _patch_store(monkeypatch, {})

    assert its.is_openrouter_openai_image2_enabled() is False
    assert its.get_openrouter_openai_image2_default_quality() == "mid"


def test_openrouter_openai_image2_settings_round_trip(monkeypatch):
    from appcore import image_translate_settings as its

    store = {}
    _patch_store(monkeypatch, store)

    its.set_openrouter_openai_image2_enabled(True)
    its.set_openrouter_openai_image2_default_quality("high")

    assert store["image_translate.openrouter_openai_image2_enabled"] == "1"
    assert store["image_translate.openrouter_openai_image2_default_quality"] == "high"
    assert its.is_openrouter_openai_image2_enabled() is True
    assert its.get_openrouter_openai_image2_default_quality() == "high"


def test_openrouter_openai_image2_enabled_accepts_truthy_strings(monkeypatch):
    from appcore import image_translate_settings as its

    for value in ("1", "true", "yes", "on", "TRUE"):
        _patch_store(monkeypatch, {"image_translate.openrouter_openai_image2_enabled": value})
        assert its.is_openrouter_openai_image2_enabled() is True

    for value in ("0", "false", "no", "off", "", "bogus"):
        _patch_store(monkeypatch, {"image_translate.openrouter_openai_image2_enabled": value})
        assert its.is_openrouter_openai_image2_enabled() is False


def test_openrouter_openai_image2_quality_rejects_invalid_value(monkeypatch):
    from appcore import image_translate_settings as its

    _patch_store(monkeypatch, {})

    with pytest.raises(ValueError):
        its.set_openrouter_openai_image2_default_quality("ultra")


def test_openrouter_openai_image2_quality_falls_back_when_corrupt(monkeypatch):
    from appcore import image_translate_settings as its

    _patch_store(
        monkeypatch,
        {"image_translate.openrouter_openai_image2_default_quality": "ultra"},
    )

    assert its.get_openrouter_openai_image2_default_quality() == "mid"


def test_apimart_channel_registered():
    from appcore import image_translate_settings as its
    assert "apimart" in its.CHANNELS
    assert "apimart" in its.CHANNEL_LABELS
    assert its.CHANNEL_LABELS["apimart"] == "APIMART (GPT-Image-2)"


def test_list_all_prompts_uses_dynamic_languages(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "de", "name_zh": "德语", "enabled": 1},
            {"code": "nl", "name_zh": "荷兰语", "enabled": 1},
        ],
    )

    store = {}

    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None

    def fake_execute(sql, params):
        store[params[0]] = params[1]

    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    data = its.list_all_prompts()
    assert set(data.keys()) == {"de", "nl"}
    for lang, prompts in data.items():
        assert set(prompts.keys()) == set(its.PRESETS)
        for preset, text in prompts.items():
            assert text
