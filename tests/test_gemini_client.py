import importlib
from types import SimpleNamespace


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
