import pytest


def test_fine_ai_model_config_defaults_to_manual_and_scheduled_vertex(monkeypatch):
    from appcore import fine_ai_evaluation_model_config as config

    monkeypatch.setattr(config.settings_store, "get_setting", lambda key: None)

    assert config.get_profile_config("manual") == {
        "profile": "manual",
        "provider": "gemini_vertex",
        "model": "gemini-3.5-flash",
        "label": "GOOGLE VERTEX AI",
    }
    assert config.get_profile_config("scheduled") == {
        "profile": "scheduled",
        "provider": "gemini_vertex",
        "model": "gemini-3.5-flash",
        "label": "GOOGLE VERTEX AI",
    }


def test_fine_ai_model_config_maps_openrouter_to_google_model_id(monkeypatch):
    from appcore import fine_ai_evaluation_model_config as config

    store = {}
    monkeypatch.setattr(config.settings_store, "get_setting", lambda key: store.get(key))
    monkeypatch.setattr(config.settings_store, "set_setting", lambda key, value: store.__setitem__(key, value))

    config.set_profile_provider("manual", "openrouter")

    manual = config.get_profile_config("manual")
    assert manual["provider"] == "openrouter"
    assert manual["model"] == "google/gemini-3.5-flash"
    assert manual["label"] == "OPENROUTER"


def test_fine_ai_model_config_rejects_invalid_provider():
    from appcore import fine_ai_evaluation_model_config as config

    with pytest.raises(ValueError, match="Unsupported fine AI provider"):
        config.set_profile_provider("manual", "doubao")


def test_fine_ai_country_concurrency_defaults_to_one(monkeypatch):
    from appcore import fine_ai_evaluation_model_config as config

    monkeypatch.setattr(config.settings_store, "get_setting", lambda key: None)

    assert config.get_parallel_mode() == "serial"
    assert config.get_country_concurrency() == 1


def test_fine_ai_country_concurrency_saves_and_validates_range(monkeypatch):
    from appcore import fine_ai_evaluation_model_config as config

    store = {}
    monkeypatch.setattr(config.settings_store, "get_setting", lambda key: store.get(key))
    monkeypatch.setattr(config.settings_store, "set_setting", lambda key, value: store.__setitem__(key, value))

    config.set_country_concurrency("2")

    assert store[config.COUNTRY_CONCURRENCY_KEY] == "2"
    assert config.get_country_concurrency() == 2

    for invalid in ("0", "6", "abc"):
        with pytest.raises(ValueError, match="Unsupported fine AI country concurrency"):
            config.set_country_concurrency(invalid)

    store[config.COUNTRY_CONCURRENCY_KEY] = "invalid"
    assert config.get_country_concurrency() == 1


def test_fine_ai_model_config_falls_back_when_stored_provider_is_invalid(monkeypatch):
    from appcore import fine_ai_evaluation_model_config as config

    store = {config.SETTING_KEYS["manual"]: "doubao"}
    monkeypatch.setattr(config.settings_store, "get_setting", lambda key: store.get(key))

    assert config.get_profile_config("manual")["provider"] == "gemini_vertex"
