"""验证 config.py 为 push-module 直连模式暴露的环境变量。"""
import importlib


def _reload_config(monkeypatch, env: dict[str, str]):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import config
    return importlib.reload(config)


def test_autovideo_base_url_default(monkeypatch):
    monkeypatch.delenv("AUTOVIDEO_BASE_URL", raising=False)
    cfg = _reload_config(monkeypatch, {})
    assert cfg.AUTOVIDEO_BASE_URL == "http://14.103.220.208:8888"


def test_autovideo_base_url_env_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"AUTOVIDEO_BASE_URL": "http://example.test:9999"})
    assert cfg.AUTOVIDEO_BASE_URL == "http://example.test:9999"


def test_autovideo_api_key_default(monkeypatch):
    monkeypatch.delenv("AUTOVIDEO_API_KEY", raising=False)
    cfg = _reload_config(monkeypatch, {})
    assert cfg.AUTOVIDEO_API_KEY == "autovideosrt-materials-openapi"


def test_push_medias_target_default(monkeypatch):
    monkeypatch.delenv("PUSH_MEDIAS_TARGET", raising=False)
    cfg = _reload_config(monkeypatch, {})
    assert cfg.PUSH_MEDIAS_TARGET == "http://172.17.254.77:22400/dify/shopify/medias"


def test_push_medias_target_env_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"PUSH_MEDIAS_TARGET": "http://downstream.test/push"})
    assert cfg.PUSH_MEDIAS_TARGET == "http://downstream.test/push"
