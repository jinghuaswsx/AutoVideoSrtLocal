from __future__ import annotations

import importlib
import os
import sys

import pytest


_SERVER_ENV_NAMES = (
    "AUTOVIDEOSRT_SERVER_HOST",
    "AUTOVIDEOSRT_SERVER_SCHEME",
    "AUTOVIDEOSRT_SERVER_BASE_URL",
    "AUTOVIDEOSRT_TEST_SERVER_BASE_URL",
    "AUTOVIDEOSRT_LOCAL_IMAGE_BASE_URL",
)


@pytest.fixture(autouse=True)
def _restore_server_config_after_test():
    original_env = {name: os.getenv(name) for name in _SERVER_ENV_NAMES}
    original_disable_dotenv = os.getenv("AUTOVIDEOSRT_DISABLE_DOTENV")
    original_local_base_url = os.getenv("LOCAL_SERVER_BASE_URL")
    yield
    for name, value in original_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    if original_disable_dotenv is None:
        os.environ.pop("AUTOVIDEOSRT_DISABLE_DOTENV", None)
    else:
        os.environ["AUTOVIDEOSRT_DISABLE_DOTENV"] = original_disable_dotenv
    if original_local_base_url is None:
        os.environ.pop("LOCAL_SERVER_BASE_URL", None)
    else:
        os.environ["LOCAL_SERVER_BASE_URL"] = original_local_base_url

    import server_config

    importlib.reload(server_config)
    if "config" in sys.modules:
        import config

        importlib.reload(config)


def _reload_server_config(monkeypatch, **env: str):
    for name in _SERVER_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    import server_config

    return importlib.reload(server_config)


def test_server_config_defaults_build_urls_from_single_host(monkeypatch):
    cfg = _reload_server_config(monkeypatch)

    assert cfg.SERVER_HOST == cfg.DEFAULT_SERVER_HOST
    assert cfg.SERVER_BASE_URL == f"http://{cfg.DEFAULT_SERVER_HOST}"
    assert cfg.TEST_SERVER_BASE_URL == f"http://{cfg.DEFAULT_SERVER_HOST}:8080"
    assert cfg.LOCAL_IMAGE_BASE_URL_DEFAULT == f"http://{cfg.DEFAULT_SERVER_HOST}:82/v1"
    assert cfg.PROXY_BYPASS_LIST == f"127.0.0.1;localhost;{cfg.DEFAULT_SERVER_HOST};<local>"


def test_server_config_allows_host_and_base_url_overrides(monkeypatch):
    cfg = _reload_server_config(
        monkeypatch,
        AUTOVIDEOSRT_SERVER_HOST="10.20.30.40",
        AUTOVIDEOSRT_SERVER_BASE_URL="https://app.example.test",
        AUTOVIDEOSRT_TEST_SERVER_BASE_URL="https://test.example.test",
        AUTOVIDEOSRT_LOCAL_IMAGE_BASE_URL="https://image.example.test/v1",
    )

    assert cfg.SERVER_HOST == "10.20.30.40"
    assert cfg.SERVER_BASE_URL == "https://app.example.test"
    assert cfg.TEST_SERVER_BASE_URL == "https://test.example.test"
    assert cfg.LOCAL_IMAGE_BASE_URL_DEFAULT == "https://image.example.test/v1"
    assert cfg.PROXY_BYPASS_LIST == "127.0.0.1;localhost;10.20.30.40;<local>"


def test_config_local_server_base_url_falls_back_to_global_server_base(monkeypatch):
    monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")
    monkeypatch.delenv("LOCAL_SERVER_BASE_URL", raising=False)
    cfg = _reload_server_config(monkeypatch, AUTOVIDEOSRT_SERVER_HOST="10.44.55.66")

    import config

    config = importlib.reload(config)

    assert config.LOCAL_SERVER_BASE_URL == cfg.SERVER_BASE_URL
    assert config.LOCAL_SERVER_BASE_URL == "http://10.44.55.66"
