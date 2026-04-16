import importlib

import pytest


def test_validate_runtime_config_reports_missing_required_keys(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    config = importlib.import_module("config")
    config = importlib.reload(config)

    with pytest.raises(RuntimeError) as exc:
        config.validate_runtime_config(["OPENROUTER_API_KEY", "ELEVENLABS_API_KEY"])

    assert "OPENROUTER_API_KEY" in str(exc.value)
    assert "ELEVENLABS_API_KEY" in str(exc.value)


def test_runtime_settings_do_not_embed_real_default_secrets(monkeypatch):
    for key in [
        "VOLC_API_KEY",
        "TOS_ACCESS_KEY",
        "OPENROUTER_API_KEY",
        "ELEVENLABS_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    config = importlib.import_module("config")
    config = importlib.reload(config)

    assert config.VOLC_API_KEY == ""
    assert config.TOS_ACCESS_KEY == ""
    assert config.OPENROUTER_API_KEY == ""
    assert config.ELEVENLABS_API_KEY == ""


def test_materials_openapi_key_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("OPENAPI_MEDIA_API_KEY", raising=False)

    config = importlib.import_module("config")
    config = importlib.reload(config)

    assert config.OPENAPI_MEDIA_API_KEY == ""


def test_subtitle_removal_provider_defaults(monkeypatch):
    monkeypatch.setenv("SUBTITLE_REMOVAL_PROVIDER_TOKEN", "test-token")
    monkeypatch.delenv("SUBTITLE_REMOVAL_PROVIDER_URL", raising=False)
    monkeypatch.delenv("SUBTITLE_REMOVAL_NOTIFY_URL", raising=False)
    monkeypatch.delenv("SUBTITLE_REMOVAL_POLL_FAST_SECONDS", raising=False)
    monkeypatch.delenv("SUBTITLE_REMOVAL_POLL_SLOW_SECONDS", raising=False)
    monkeypatch.delenv("SUBTITLE_REMOVAL_MAX_DURATION_SECONDS", raising=False)

    config = importlib.import_module("config")
    config = importlib.reload(config)

    assert config.SUBTITLE_REMOVAL_PROVIDER_URL == "https://goodline.simplemokey.com/api/openAi"
    assert config.SUBTITLE_REMOVAL_NOTIFY_URL == ""
    assert config.SUBTITLE_REMOVAL_POLL_FAST_SECONDS == 8
    assert config.SUBTITLE_REMOVAL_POLL_SLOW_SECONDS == 15
    assert config.SUBTITLE_REMOVAL_MAX_DURATION_SECONDS == 600
