import importlib
from types import SimpleNamespace

import pytest


def _reload_gemini(monkeypatch):
    config = importlib.import_module("config")
    config = importlib.reload(config)
    gemini = importlib.import_module("appcore.gemini")
    gemini = importlib.reload(gemini)
    gemini._clients.clear()
    return config, gemini


def test_cloud_project_location_initializes_vertex_client(monkeypatch):
    monkeypatch.setenv("GEMINI_BACKEND", "cloud")
    monkeypatch.setenv("GEMINI_CLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("GEMINI_CLOUD_LOCATION", "global")
    monkeypatch.delenv("GEMINI_CLOUD_API_KEY", raising=False)

    _, gemini = _reload_gemini(monkeypatch)

    created = {}

    def fake_client(**kwargs):
        created.update(kwargs)
        client = SimpleNamespace()
        client.models = SimpleNamespace(
            generate_content=lambda **_: SimpleNamespace(text="ok", parsed=None)
        )
        return client

    monkeypatch.setattr(gemini.genai, "Client", fake_client)

    out = gemini.generate(
        "hello",
        model="gemini-3.1-flash-lite-preview",
        max_retries=1,
    )

    assert out == "ok"
    assert created == {
        "vertexai": True,
        "project": "demo-project",
        "location": "global",
    }


def test_cloud_legacy_key_only_is_configured_and_generate_falls_back(monkeypatch):
    monkeypatch.setenv("GEMINI_BACKEND", "cloud")
    monkeypatch.delenv("GEMINI_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("GEMINI_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GEMINI_CLOUD_API_KEY", "legacy-cloud-key")

    _, gemini = _reload_gemini(monkeypatch)

    created = {}

    def fake_client(**kwargs):
        created.update(kwargs)
        client = SimpleNamespace()
        client.models = SimpleNamespace(
            generate_content=lambda **_: SimpleNamespace(text="ok", parsed=None)
        )
        return client

    monkeypatch.setattr(gemini.genai, "Client", fake_client)

    assert gemini.is_configured() is True

    out = gemini.generate(
        "hello",
        model="gemini-3.1-flash-lite-preview",
        max_retries=1,
    )

    assert out == "ok"
    assert created == {
        "vertexai": True,
        "api_key": "legacy-cloud-key",
    }


def test_cloud_legacy_key_only_generate_stream_falls_back(monkeypatch):
    monkeypatch.setenv("GEMINI_BACKEND", "cloud")
    monkeypatch.delenv("GEMINI_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("GEMINI_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GEMINI_CLOUD_API_KEY", "legacy-cloud-key")

    _, gemini = _reload_gemini(monkeypatch)

    created = {}

    def fake_client(**kwargs):
        created.update(kwargs)
        client = SimpleNamespace()
        client.models = SimpleNamespace(
            generate_content_stream=lambda **_: [
                SimpleNamespace(text="he"),
                SimpleNamespace(text="llo"),
            ]
        )
        return client

    monkeypatch.setattr(gemini.genai, "Client", fake_client)

    chunks = list(
        gemini.generate_stream(
            "hello",
            model="gemini-3.1-flash-lite-preview",
        )
    )

    assert chunks == ["he", "llo"]
    assert created == {
        "vertexai": True,
        "api_key": "legacy-cloud-key",
    }


def test_generate_logs_usage_via_ai_billing(monkeypatch):
    _, gemini = _reload_gemini(monkeypatch)

    resp = SimpleNamespace(
        text="ok",
        parsed=None,
        usage_metadata=SimpleNamespace(
            prompt_token_count=11,
            candidates_token_count=7,
        ),
    )
    client = SimpleNamespace(
        models=SimpleNamespace(generate_content=lambda **_: resp)
    )
    billing_calls: list[dict] = []

    monkeypatch.setattr(gemini, "resolve_config", lambda *a, **kw: ("api-key", "gemini-3.1-pro-preview"))
    monkeypatch.setattr(gemini, "_get_client", lambda api_key: client)
    monkeypatch.setattr(gemini.ai_billing, "log_request", lambda **kw: billing_calls.append(kw))

    out = gemini.generate(
        "hello",
        model="gemini-3.1-pro-preview",
        user_id=9,
        project_id="proj-9",
        service="video_score.run",
        max_retries=1,
    )

    assert out == "ok"
    assert len(billing_calls) == 1
    assert billing_calls[0]["use_case_code"] == "video_score.run"
    assert billing_calls[0]["provider"] == "gemini_aistudio"
    assert billing_calls[0]["model"] == "gemini-3.1-pro-preview"
    assert billing_calls[0]["input_tokens"] == 11
    assert billing_calls[0]["output_tokens"] == 7
    assert billing_calls[0]["success"] is True


def test_generate_logs_failure_via_ai_billing(monkeypatch):
    _, gemini = _reload_gemini(monkeypatch)

    def _boom(**_):
        raise RuntimeError("boom")

    client = SimpleNamespace(
        models=SimpleNamespace(generate_content=_boom)
    )
    billing_calls: list[dict] = []

    monkeypatch.setattr(gemini, "resolve_config", lambda *a, **kw: ("api-key", "gemini-3.1-pro-preview"))
    monkeypatch.setattr(gemini, "_get_client", lambda api_key: client)
    monkeypatch.setattr(gemini.ai_billing, "log_request", lambda **kw: billing_calls.append(kw))

    with pytest.raises(gemini.GeminiError, match="boom"):
        gemini.generate(
            "hello",
            model="gemini-3.1-pro-preview",
            user_id=9,
            project_id="proj-9",
            service="video_score.run",
            max_retries=1,
        )

    assert len(billing_calls) == 1
    assert billing_calls[0]["use_case_code"] == "video_score.run"
    assert billing_calls[0]["provider"] == "gemini_aistudio"
    assert billing_calls[0]["model"] == "gemini-3.1-pro-preview"
    assert billing_calls[0]["success"] is False
    assert billing_calls[0]["extra"]["error"] == "boom"


def test_generate_return_payload_includes_usage(monkeypatch):
    _, gemini = _reload_gemini(monkeypatch)

    resp = SimpleNamespace(
        text="ok",
        parsed=None,
        usage_metadata=SimpleNamespace(
            prompt_token_count=13,
            candidates_token_count=5,
        ),
    )
    client = SimpleNamespace(
        models=SimpleNamespace(generate_content=lambda **_: resp)
    )

    monkeypatch.setattr(gemini, "resolve_config", lambda *a, **kw: ("api-key", "gemini-3.1-pro-preview"))
    monkeypatch.setattr(gemini, "_get_client", lambda api_key: client)
    monkeypatch.setattr(gemini, "_log_gemini_usage", lambda **kwargs: None)

    payload = gemini.generate(
        "hello",
        model="gemini-3.1-pro-preview",
        user_id=9,
        max_retries=1,
        return_payload=True,
    )

    assert payload["text"] == "ok"
    assert payload["json"] is None
    assert payload["usage"] == {"input_tokens": 13, "output_tokens": 5}
