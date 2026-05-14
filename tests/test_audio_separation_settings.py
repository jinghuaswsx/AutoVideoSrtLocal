from __future__ import annotations

from pipeline import audio_separation as sep


def test_load_settings_defaults_to_local_audio_separator_when_url_missing(monkeypatch):
    values = {
        sep.SETTING_ENABLED: "1",
        sep.SETTING_API_URL: None,
        sep.SETTING_PRESET: "vocal_balanced",
        sep.SETTING_SEPARATION_GOAL: None,
        sep.SETTING_TASK_TIMEOUT: "300",
        sep.SETTING_BACKGROUND_VOLUME: "0.8",
    }

    monkeypatch.setattr("appcore.settings.get_setting", lambda key: values.get(key))

    settings = sep.load_settings()

    assert settings.api_url == "http://127.0.0.1:83"
    assert settings.separation_goal == "background_preserve"
    assert settings.is_runnable is True


def test_load_settings_keeps_intentionally_blank_audio_separator_url(monkeypatch):
    values = {
        sep.SETTING_ENABLED: "1",
        sep.SETTING_API_URL: "",
        sep.SETTING_PRESET: "vocal_balanced",
        sep.SETTING_SEPARATION_GOAL: "background_preserve",
        sep.SETTING_TASK_TIMEOUT: "300",
        sep.SETTING_BACKGROUND_VOLUME: "0.8",
    }

    monkeypatch.setattr("appcore.settings.get_setting", lambda key: values.get(key))

    settings = sep.load_settings()

    assert settings.api_url == ""
    assert settings.separation_goal == "background_preserve"
    assert settings.is_runnable is False


def test_settings_template_exposes_background_preserve_goal_and_instrumental_presets():
    html = open("web/templates/settings.html", encoding="utf-8").read()

    assert 'name="separation_goal"' in html
    assert "background_preserve" in html
    assert "instrumental_full" in html
    assert "instrumental_balanced" in html
    assert "instrumental_clean" in html
