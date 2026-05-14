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


def test_delete_setting_deletes_key(monkeypatch):
    import appcore.settings as settings

    calls = []
    monkeypatch.setattr(settings, "_execute", lambda sql, args: calls.append((sql, args)) or 1)

    result = settings.delete_setting("foo")

    assert result == 1
    assert calls == [("DELETE FROM system_settings WHERE `key` = %s", ("foo",))]


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


def test_list_ai_model_prices_serializes_rows(monkeypatch):
    from decimal import Decimal

    import appcore.settings as settings

    captured = {}
    rows = [
        {
            "id": 7,
            "provider": "gemini_vertex",
            "model": "gemini-3.1-pro-preview",
            "units_type": "tokens",
            "unit_input_cny": Decimal("0.003"),
            "unit_output_cny": Decimal("0.012"),
            "unit_flat_cny": None,
            "note": "ok",
            "updated_at": "2026-05-07 10:00:00",
        }
    ]

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return rows

    monkeypatch.setattr(settings, "_query", fake_query)

    result = settings.list_ai_model_prices()

    assert "FROM ai_model_prices" in captured["sql"]
    assert "ORDER BY provider ASC, model ASC, id ASC" in captured["sql"]
    assert captured["args"] == ()
    assert result == [
        {
            "id": 7,
            "provider": "gemini_vertex",
            "model": "gemini-3.1-pro-preview",
            "units_type": "tokens",
            "unit_input_cny": 0.003,
            "unit_output_cny": 0.012,
            "unit_flat_cny": None,
            "note": "ok",
            "updated_at": "2026-05-07 10:00:00",
        }
    ]


def test_create_ai_model_price_inserts_and_returns_created_row(monkeypatch):
    import appcore.settings as settings

    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))
        return 9

    def fake_query(sql, args=()):
        assert "WHERE id = %s" in sql
        assert args == (9,)
        return [
            {
                "id": 9,
                "provider": "elevenlabs",
                "model": "eleven_multilingual_v2",
                "units_type": "chars",
                "unit_input_cny": None,
                "unit_output_cny": None,
                "unit_flat_cny": 0.0002,
                "note": "created",
                "updated_at": "2026-05-07 11:00:00",
            }
        ]

    monkeypatch.setattr(settings, "_execute", fake_execute)
    monkeypatch.setattr(settings, "_query", fake_query)

    result = settings.create_ai_model_price(
        {
            "provider": "elevenlabs",
            "model": "eleven_multilingual_v2",
            "units_type": "chars",
            "unit_input_cny": None,
            "unit_output_cny": None,
            "unit_flat_cny": 0.0002,
            "note": "created",
        }
    )

    assert "INSERT INTO ai_model_prices" in calls[0][0]
    assert calls[0][1] == (
        "elevenlabs",
        "eleven_multilingual_v2",
        "chars",
        None,
        None,
        0.0002,
        "created",
    )
    assert result["id"] == 9
    assert result["unit_flat_cny"] == 0.0002


def test_update_ai_model_price_updates_price_fields_and_returns_row(monkeypatch):
    import appcore.settings as settings

    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))
        return 1

    def fake_query(sql, args=()):
        assert args == (9,)
        return [
            {
                "id": 9,
                "provider": "elevenlabs",
                "model": "eleven_multilingual_v2",
                "units_type": "chars",
                "unit_input_cny": None,
                "unit_output_cny": None,
                "unit_flat_cny": 0.0003,
                "note": "updated",
                "updated_at": "2026-05-07 12:00:00",
            }
        ]

    monkeypatch.setattr(settings, "_execute", fake_execute)
    monkeypatch.setattr(settings, "_query", fake_query)

    result = settings.update_ai_model_price(
        9,
        {
            "provider": "ignored-on-update",
            "model": "ignored-on-update",
            "units_type": "chars",
            "unit_input_cny": None,
            "unit_output_cny": None,
            "unit_flat_cny": 0.0003,
            "note": "updated",
        },
    )

    assert "UPDATE ai_model_prices" in calls[0][0]
    assert "provider =" not in calls[0][0]
    assert "model =" not in calls[0][0]
    assert calls[0][1] == ("chars", None, None, 0.0003, "updated", 9)
    assert result["unit_flat_cny"] == 0.0003


def test_delete_ai_model_price_deletes_by_id(monkeypatch):
    import appcore.settings as settings

    calls = []
    monkeypatch.setattr(settings, "_execute", lambda sql, args: calls.append((sql, args)) or 1)

    result = settings.delete_ai_model_price(9)

    assert result == 1
    assert calls == [("DELETE FROM ai_model_prices WHERE id = %s", (9,))]


def test_project_type_labels_include_subtitle_removal():
    import appcore.settings as settings

    assert settings.PROJECT_TYPE_LABELS["subtitle_removal"] == "字幕移除"


def test_project_type_labels_include_omni_and_video_cover():
    import appcore.settings as settings

    assert settings.PROJECT_TYPE_LABELS["omni_translate"] == "全能视频翻译"
    assert settings.PROJECT_TYPE_LABELS["video_cover"] == "文案封面生成"
