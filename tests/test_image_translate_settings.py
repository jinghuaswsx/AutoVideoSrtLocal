import pytest


def test_get_prompt_rejects_invalid_preset():
    from appcore import image_translate_settings as its
    with pytest.raises(ValueError):
        its.get_prompt("invalid", "de")


def test_get_prompt_rejects_invalid_lang():
    from appcore import image_translate_settings as its
    with pytest.raises(ValueError):
        its.get_prompt("cover", "xx")


def test_get_prompt_bootstraps_when_missing(monkeypatch):
    from appcore import image_translate_settings as its
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
    calls = []
    def fake_execute(sql, params):
        calls.append(params)
    monkeypatch.setattr(its, "execute", fake_execute)

    its.update_prompt("cover", "ja", "自定义日语封面 prompt")
    assert len(calls) == 1
    assert calls[0][0] == "image_translate.prompt_cover_ja"
    assert calls[0][1] == "自定义日语封面 prompt"


def test_update_prompt_rejects_invalid():
    from appcore import image_translate_settings as its
    with pytest.raises(ValueError):
        its.update_prompt("invalid", "de", "x")
    with pytest.raises(ValueError):
        its.update_prompt("cover", "xx", "x")


def test_list_all_prompts_covers_all_combinations(monkeypatch):
    from appcore import image_translate_settings as its
    store = {}
    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None
    def fake_execute(sql, params):
        store[params[0]] = params[1]
    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    data = its.list_all_prompts()
    assert set(data.keys()) == set(its.SUPPORTED_LANGS)
    for lang, prompts in data.items():
        assert set(prompts.keys()) == set(its.PRESETS)
        for preset, text in prompts.items():
            assert text  # 每条都有内容
