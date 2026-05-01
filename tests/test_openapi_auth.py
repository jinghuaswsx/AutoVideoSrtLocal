from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def openapi_client(monkeypatch):
    monkeypatch.setenv("LOCAL_SERVER_BASE_URL", "http://local.test")
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)

    import config as _config

    importlib.reload(_config)

    from web.app import create_app

    return create_app().test_client()


def test_plain_openapi_key_remains_valid_for_all_scopes():
    from appcore.openapi_auth import validate_openapi_key

    assert validate_openapi_key("legacy-key", "legacy-key", required_scope="materials:read")
    assert not validate_openapi_key("wrong", "legacy-key", required_scope="materials:read")


def test_json_openapi_keys_support_multiple_callers_and_scopes():
    from appcore.openapi_auth import validate_openapi_key

    raw = """
    {
      "keys": [
        {"key": "read-key", "caller": "link-check", "scopes": ["materials:read"]},
        {"key": "push-key", "caller": "push-worker", "scopes": ["push:write"]}
      ]
    }
    """

    read_credential = validate_openapi_key("read-key", raw, required_scope="materials:read")
    push_credential = validate_openapi_key("push-key", raw, required_scope="push:write")

    assert read_credential and read_credential.caller == "link-check"
    assert push_credential and push_credential.caller == "push-worker"
    assert not validate_openapi_key("read-key", raw, required_scope="push:write")


def test_openapi_route_accepts_json_key_configuration(openapi_client, monkeypatch):
    import json

    class FakeProviderConfig:
        api_key = json.dumps(
            {
                "keys": [
                    {"key": "readonly", "caller": "mobile-check", "scopes": ["materials:read"]},
                    {"key": "writer", "caller": "automation", "scopes": ["push:write"]},
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(
        "web.routes.openapi_materials.get_provider_config",
        lambda provider_code: FakeProviderConfig()
        if provider_code == "openapi_materials"
        else None,
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_shopify_localizer_languages",
        lambda: [{"code": "de", "label": "German"}],
    )

    response = openapi_client.get(
        "/openapi/medias/shopify-image-localizer/languages",
        headers={"X-API-Key": "readonly"},
    )

    assert response.status_code == 200
