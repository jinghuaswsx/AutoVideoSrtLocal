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


def test_gemini_cloud_settings_defaults_and_overrides(monkeypatch):
    monkeypatch.delenv("GEMINI_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GEMINI_CLOUD_LOCATION", raising=False)

    config = importlib.import_module("config")
    config = importlib.reload(config)

    assert config.GEMINI_CLOUD_PROJECT == ""
    assert config.GEMINI_CLOUD_LOCATION == "global"

    monkeypatch.setenv("GEMINI_CLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("GEMINI_CLOUD_LOCATION", "us-central1")
    config = importlib.reload(config)

    assert config.GEMINI_CLOUD_PROJECT == "demo-project"
    assert config.GEMINI_CLOUD_LOCATION == "us-central1"


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


def test_push_management_config_defaults(monkeypatch):
    for k in ["PUSH_TARGET_URL", "AD_URL_TEMPLATE", "AD_URL_PROBE_TIMEOUT"]:
        monkeypatch.delenv(k, raising=False)
    import importlib, config as cfg
    importlib.reload(cfg)
    assert cfg.PUSH_TARGET_URL == ""
    assert "{lang}" in cfg.AD_URL_TEMPLATE
    assert "{product_code}" in cfg.AD_URL_TEMPLATE
    assert cfg.AD_URL_PROBE_TIMEOUT == 5


def test_push_management_config_override(monkeypatch):
    monkeypatch.setenv("PUSH_TARGET_URL", "http://10.0.0.1/api/push")
    monkeypatch.setenv("AD_URL_TEMPLATE", "https://x.com/{lang}/{product_code}")
    monkeypatch.setenv("AD_URL_PROBE_TIMEOUT", "8")
    import importlib, config as cfg
    importlib.reload(cfg)
    assert cfg.PUSH_TARGET_URL == "http://10.0.0.1/api/push"
    assert cfg.AD_URL_TEMPLATE == "https://x.com/{lang}/{product_code}"
    assert cfg.AD_URL_PROBE_TIMEOUT == 8
