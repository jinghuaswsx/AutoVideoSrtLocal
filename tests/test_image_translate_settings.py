from unittest.mock import patch


def test_render_prompt_replaces_language_name():
    from appcore import image_translate_settings as its
    out = its.render_prompt(
        "把文字翻译成 {target_language_name}，保持布局。{other}",
        target_language_name="日语",
    )
    assert out.startswith("把文字翻译成 日语")
    assert "{other}" in out


def test_get_default_prompts_bootstraps_when_missing(monkeypatch):
    from appcore import image_translate_settings as its
    store = {}
    def fake_query_one(sql, params):
        key = params[0]
        if key in store:
            return {"value": store[key]}
        return None
    def fake_execute(sql, params):
        store[params[0]] = params[1]
    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)
    prompts = its.get_default_prompts()
    assert "cover" in prompts and "detail" in prompts
    assert "{target_language_name}" in prompts["cover"]
    # 两条 key 已写入
    assert "image_translate.prompt_cover" in store
    assert "image_translate.prompt_detail" in store


def test_update_prompt_writes(monkeypatch):
    from appcore import image_translate_settings as its
    calls = []
    def fake_execute(sql, params):
        calls.append((sql, params))
    monkeypatch.setattr(its, "execute", fake_execute)
    its.update_prompt("cover", "自定义封面 {target_language_name}")
    assert len(calls) == 1
    assert "image_translate.prompt_cover" in calls[0][1]
    assert "自定义封面" in calls[0][1][1]


def test_update_prompt_rejects_invalid_preset():
    from appcore import image_translate_settings as its
    import pytest
    with pytest.raises(ValueError):
        its.update_prompt("invalid", "x")
