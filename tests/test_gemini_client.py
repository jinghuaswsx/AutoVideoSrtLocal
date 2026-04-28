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


def _cfg(api_key="", model_id="gemini-3.1-flash-lite-preview", extra=None):
    return SimpleNamespace(
        api_key=api_key,
        model_id=model_id,
        extra_config=extra or {},
    )


def test_cloud_project_location_initializes_vertex_client(monkeypatch):
    _, gemini = _reload_gemini(monkeypatch)
    monkeypatch.setattr(
        gemini,
        "get_provider_config",
        lambda code: _cfg(extra={"project": "demo-project", "location": "global"})
        if code == "gemini_cloud_text"
        else None,
    )

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
        service="gemini_cloud",
        max_retries=1,
    )

    assert out == "ok"
    assert created == {
        "vertexai": True,
        "project": "demo-project",
        "location": "global",
    }


def test_cloud_db_api_key_only_generates_with_vertex_api_key(monkeypatch):
    _, gemini = _reload_gemini(monkeypatch)
    monkeypatch.setattr(
        gemini,
        "get_provider_config",
        lambda code: _cfg(api_key="db-cloud-key")
        if code == "gemini_cloud_text"
        else None,
    )

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
        service="gemini_cloud",
        max_retries=1,
    )

    assert out == "ok"
    assert created == {
        "vertexai": True,
        "api_key": "db-cloud-key",
    }


def test_cloud_db_api_key_only_generate_stream_uses_vertex_api_key(monkeypatch):
    _, gemini = _reload_gemini(monkeypatch)
    monkeypatch.setattr(
        gemini,
        "get_provider_config",
        lambda code: _cfg(api_key="db-cloud-key")
        if code == "gemini_cloud_text"
        else None,
    )

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
                service="gemini_cloud",
            )
        )

    assert chunks == ["he", "llo"]
    assert created == {
        "vertexai": True,
        "api_key": "db-cloud-key",
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

    monkeypatch.setattr(gemini, "_get_client_for_service", lambda service: (client, "gemini-3.1-pro-preview"))
    monkeypatch.setattr(gemini, "_binding_lookup", lambda service: None)
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


def test_generate_logs_request_and_response_payloads(monkeypatch, tmp_path):
    _, gemini = _reload_gemini(monkeypatch)

    media_path = tmp_path / "frame.png"
    media_path.write_bytes(b"PNG")
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

    monkeypatch.setattr(gemini, "_get_client_for_service", lambda service: (client, "gemini-3.1-pro-preview"))
    monkeypatch.setattr(gemini, "_binding_lookup", lambda service: None)
    monkeypatch.setattr(gemini, "_build_contents", lambda client, prompt, media: ["parts"])
    monkeypatch.setattr(gemini.ai_billing, "log_request", lambda **kw: billing_calls.append(kw))

    out = gemini.generate(
        "hello",
        system="system",
        media=[media_path],
        model="gemini-3.1-pro-preview",
        user_id=9,
        project_id="proj-9",
        service="video_score.run",
        temperature=0.2,
        max_output_tokens=1024,
        max_retries=1,
    )

    assert out == "ok"
    payload_call = billing_calls[0]
    assert payload_call["request_payload"]["prompt"] == "hello"
    assert payload_call["request_payload"]["system"] == "system"
    assert payload_call["request_payload"]["media"] == [str(media_path)]
    assert payload_call["request_payload"]["temperature"] == 0.2
    assert payload_call["request_payload"]["max_output_tokens"] == 1024
    assert payload_call["response_payload"]["text"] == "ok"
    assert payload_call["response_payload"]["usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
    }


def test_generate_enables_google_search_tool_and_logs_request(monkeypatch):
    _, gemini = _reload_gemini(monkeypatch)

    captured: dict = {}
    resp = SimpleNamespace(
        text="ok",
        parsed=None,
        usage_metadata=SimpleNamespace(
            prompt_token_count=11,
            candidates_token_count=7,
        ),
    )

    def fake_generate_content(**kwargs):
        captured.update(kwargs)
        return resp

    client = SimpleNamespace(
        models=SimpleNamespace(generate_content=fake_generate_content)
    )
    billing_calls: list[dict] = []

    monkeypatch.setattr(gemini, "_get_client_for_service", lambda service: (client, "gemini-3.1-pro-preview"))
    monkeypatch.setattr(gemini, "_binding_lookup", lambda service: None)
    monkeypatch.setattr(gemini, "_build_contents", lambda client, prompt, media: ["parts"])
    monkeypatch.setattr(gemini.ai_billing, "log_request", lambda **kw: billing_calls.append(kw))

    out = gemini.generate(
        "hello",
        model="gemini-3.1-pro-preview",
        user_id=9,
        project_id="proj-9",
        service="material_evaluation.evaluate",
        google_search=True,
        max_retries=1,
    )

    assert out == "ok"
    tools = captured["config"].tools
    assert tools
    assert tools[0].google_search is not None
    assert billing_calls[0]["request_payload"]["google_search"] is True
    assert billing_calls[0]["request_payload"]["tools"] == [{"google_search": {}}]


def test_generate_logs_failure_via_ai_billing(monkeypatch):
    _, gemini = _reload_gemini(monkeypatch)

    def _boom(**_):
        raise RuntimeError("boom")

    client = SimpleNamespace(
        models=SimpleNamespace(generate_content=_boom)
    )
    billing_calls: list[dict] = []

    monkeypatch.setattr(gemini, "_get_client_for_service", lambda service: (client, "gemini-3.1-pro-preview"))
    monkeypatch.setattr(gemini, "_binding_lookup", lambda service: None)
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

    monkeypatch.setattr(gemini, "_get_client_for_service", lambda service: (client, "gemini-3.1-pro-preview"))
    monkeypatch.setattr(gemini, "_binding_lookup", lambda service: None)
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
