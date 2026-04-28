import importlib

import pytest


@pytest.fixture(autouse=True)
def disable_dotenv(monkeypatch):
    monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")


def test_validate_runtime_config_reports_missing_infra_keys(monkeypatch):
    monkeypatch.delenv("TOS_ACCESS_KEY", raising=False)
    monkeypatch.delenv("TOS_SECRET_KEY", raising=False)

    config = importlib.import_module("config")
    config = importlib.reload(config)

    with pytest.raises(RuntimeError) as exc:
        config.validate_runtime_config()

    assert "TOS_ACCESS_KEY" in str(exc.value)
    assert "TOS_SECRET_KEY" in str(exc.value)


def test_runtime_settings_do_not_expose_provider_secret_env_names(monkeypatch):
    for key in [
        "VOLC_API_KEY",
        "OPENROUTER_API_KEY",
        "ELEVENLABS_API_KEY",
        "OPENAPI_MEDIA_API_KEY",
        "APIMART_IMAGE_API_KEY",
        "SEEDANCE_API_KEY",
        "DOUBAO_LLM_API_KEY",
        "GEMINI_CLOUD_PROJECT",
    ]:
        monkeypatch.delenv(key, raising=False)

    config = importlib.import_module("config")
    config = importlib.reload(config)

    assert not hasattr(config, "VOLC_API_KEY")
    assert not hasattr(config, "OPENROUTER_API_KEY")
    assert not hasattr(config, "ELEVENLABS_API_KEY")
    assert not hasattr(config, "OPENAPI_MEDIA_API_KEY")
    assert not hasattr(config, "APIMART_IMAGE_API_KEY")
    assert not hasattr(config, "SEEDANCE_API_KEY")
    assert not hasattr(config, "DOUBAO_LLM_API_KEY")
    assert not hasattr(config, "GEMINI_CLOUD_PROJECT")


def test_provider_defaults_are_non_secret_constants_only(monkeypatch):
    config = importlib.import_module("config")
    config = importlib.reload(config)

    assert config.OPENROUTER_BASE_URL_DEFAULT == "https://openrouter.ai/api/v1"
    assert config.DOUBAO_LLM_BASE_URL_DEFAULT == "https://ark.cn-beijing.volces.com/api/v3"
    assert config.ELEVENLABS_BASE_URL_DEFAULT == "https://api.elevenlabs.io/v1"
    assert config.APIMART_BASE_URL_DEFAULT == "https://api.apimart.ai"
    assert config.SUBTITLE_REMOVAL_PROVIDER_URL_DEFAULT == "https://goodline.simplemokey.com/api/openAi"


def test_subtitle_removal_runtime_settings_defaults(monkeypatch):
    monkeypatch.delenv("SUBTITLE_REMOVAL_PROVIDER", raising=False)
    monkeypatch.delenv("SUBTITLE_REMOVAL_POLL_FAST_SECONDS", raising=False)
    monkeypatch.delenv("SUBTITLE_REMOVAL_POLL_SLOW_SECONDS", raising=False)
    monkeypatch.delenv("SUBTITLE_REMOVAL_MAX_DURATION_SECONDS", raising=False)

    config = importlib.import_module("config")
    config = importlib.reload(config)

    assert config.SUBTITLE_REMOVAL_PROVIDER == "goodline"
    assert config.SUBTITLE_REMOVAL_POLL_FAST_SECONDS == 8
    assert config.SUBTITLE_REMOVAL_POLL_SLOW_SECONDS == 15
    assert config.SUBTITLE_REMOVAL_MAX_DURATION_SECONDS == 600


def test_push_management_config_defaults(monkeypatch):
    for k in ["PUSH_TARGET_URL", "AD_URL_TEMPLATE", "AD_URL_PROBE_TIMEOUT"]:
        monkeypatch.delenv(k, raising=False)
    import config as cfg
    importlib.reload(cfg)
    assert cfg.PUSH_TARGET_URL == ""
    assert "{lang}" in cfg.AD_URL_TEMPLATE
    assert "{product_code}" in cfg.AD_URL_TEMPLATE
    assert cfg.AD_URL_PROBE_TIMEOUT == 5


def test_push_management_config_override(monkeypatch):
    monkeypatch.setenv("PUSH_TARGET_URL", "http://10.0.0.1/api/push")
    monkeypatch.setenv("AD_URL_TEMPLATE", "https://x.com/{lang}/{product_code}")
    monkeypatch.setenv("AD_URL_PROBE_TIMEOUT", "8")
    import config as cfg
    importlib.reload(cfg)
    assert cfg.PUSH_TARGET_URL == "http://10.0.0.1/api/push"
    assert cfg.AD_URL_TEMPLATE == "https://x.com/{lang}/{product_code}"
    assert cfg.AD_URL_PROBE_TIMEOUT == 8
