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


def test_get_prompt_generates_generic_prompt_for_dynamic_lang(monkeypatch):
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

    value = its.get_prompt("cover", "nl")
    assert "荷兰语" in value
    assert "只替换文字" in value
    assert "保留布局" in value
    assert store["image_translate.prompt_cover_nl"] == value


def test_get_prompt_generates_generic_detail_prompt_for_dynamic_lang(monkeypatch):
    from appcore import image_translate_settings as its

    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
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

    value = its.get_prompt("detail", "nl")
    assert "荷兰语" in value
    assert "只替换文字" in value
    assert "保留布局" in value
    assert store["image_translate.prompt_detail_nl"] == value


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
