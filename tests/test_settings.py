# tests/test_settings.py
from __future__ import annotations


def test_get_retention_hours_default(monkeypatch):
    """无覆盖值时返回全局默认"""
    import appcore.settings as settings

    store = {"retention_default_hours": "168"}

    def fake_query_one(sql, args):
        key = args[0]
        if key in store:
            return {"value": store[key]}
        return None

    monkeypatch.setattr(settings, "_query_one", fake_query_one)
    assert settings.get_retention_hours("translation") == 168


def test_get_retention_hours_override(monkeypatch):
    """有模块覆盖值时优先使用"""
    import appcore.settings as settings

    store = {
        "retention_default_hours": "168",
        "retention_copywriting_hours": "48",
    }

    def fake_query_one(sql, args):
        key = args[0]
        if key in store:
            return {"value": store[key]}
        return None

    monkeypatch.setattr(settings, "_query_one", fake_query_one)
    assert settings.get_retention_hours("copywriting") == 48


def test_get_retention_hours_fallback_hardcode(monkeypatch):
    """数据库完全没有配置时，硬编码 168"""
    import appcore.settings as settings

    monkeypatch.setattr(settings, "_query_one", lambda sql, args: None)
    assert settings.get_retention_hours("translation") == 168


def test_get_retention_hours_ignores_non_positive_override(monkeypatch):
    import appcore.settings as settings

    store = {
        "retention_default_hours": "48",
        "retention_de_translate_hours": "0",
    }

    def fake_query_one(sql, args):
        key = args[0]
        if key in store:
            return {"value": store[key]}
        return None

    monkeypatch.setattr(settings, "_query_one", fake_query_one)
    assert settings.get_retention_hours("de_translate") == 48


def test_get_setting(monkeypatch):
    import appcore.settings as settings

    monkeypatch.setattr(
        settings, "_query_one",
        lambda sql, args: {"value": "hello"} if args[0] == "some_key" else None,
    )
    assert settings.get_setting("some_key") == "hello"
    assert settings.get_setting("missing") is None


def test_set_setting(monkeypatch):
    import appcore.settings as settings

    calls = []
    monkeypatch.setattr(settings, "_execute", lambda sql, args: calls.append(args))
    settings.set_setting("foo", "bar")
    assert len(calls) == 1
    assert calls[0] == ("foo", "bar")


def test_get_all_retention_settings(monkeypatch):
    import appcore.settings as settings

    rows = [
        {"key": "retention_default_hours", "value": "168"},
        {"key": "retention_copywriting_hours", "value": "48"},
        {"key": "retention_de_translate_hours", "value": "0"},
    ]
    monkeypatch.setattr(
        settings, "_query",
        lambda sql, args=(): [r for r in rows if r["key"].startswith("retention_")],
    )
    result = settings.get_all_retention_settings()
    assert result["default"] == 168
    assert result["copywriting"] == 48
    assert result.get("de_translate") is None
    assert result.get("translation") is None


def test_project_type_labels_include_subtitle_removal():
    import appcore.settings as settings

    assert settings.PROJECT_TYPE_LABELS["subtitle_removal"] == "字幕移除"
