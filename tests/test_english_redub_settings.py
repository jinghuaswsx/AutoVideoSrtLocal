from __future__ import annotations

import pytest


def test_voice_match_strategy_defaults_to_legacy(monkeypatch):
    from appcore import english_redub_settings as settings

    monkeypatch.setattr(
        settings.settings_store,
        "get_setting",
        lambda key, default=None: None,
    )

    assert settings.get_voice_match_strategy() == "legacy"


def test_voice_match_strategy_uses_settings_store_signature(monkeypatch):
    from appcore import english_redub_settings as settings

    monkeypatch.setattr(
        settings.settings_store,
        "get_setting",
        lambda key: None,
    )

    assert settings.get_voice_match_strategy() == "legacy"


def test_voice_match_strategy_accepts_timbre_speed(monkeypatch):
    from appcore import english_redub_settings as settings

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        settings.settings_store,
        "set_setting",
        lambda key, value: calls.append((key, value)),
    )

    assert settings.set_voice_match_strategy("timbre_speed") == "timbre_speed"
    assert calls == [(settings.SETTING_VOICE_MATCH_STRATEGY, "timbre_speed")]


def test_voice_match_strategy_rejects_invalid_values():
    from appcore import english_redub_settings as settings

    with pytest.raises(ValueError, match="voice_match_strategy"):
        settings.set_voice_match_strategy("fast_only")
