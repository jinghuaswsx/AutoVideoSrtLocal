import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_vertex_client_uses_project_and_location(monkeypatch):
    monkeypatch.setenv("GEMINI_BACKEND", "cloud")
    monkeypatch.setenv("GEMINI_CLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("GEMINI_CLOUD_LOCATION", "global")

    config = importlib.import_module("config")
    config = importlib.reload(config)
    gemini = importlib.import_module("appcore.gemini")
    gemini = importlib.reload(gemini)

    created = {}

    def fake_client(**kwargs):
        created.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(gemini.genai, "Client", fake_client)
    gemini._clients.clear()

    client = gemini._get_client("cloud-key-that-should-not-be-used-as-api-key")

    assert isinstance(client, SimpleNamespace)
    assert created == {
        "vertexai": True,
        "project": "demo-project",
        "location": "global",
    }


def test_vertex_client_requires_project(monkeypatch):
    monkeypatch.setenv("GEMINI_BACKEND", "cloud")
    monkeypatch.delenv("GEMINI_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("GEMINI_CLOUD_LOCATION", "global")

    importlib.reload(importlib.import_module("config"))
    gemini = importlib.import_module("appcore.gemini")
    gemini = importlib.reload(gemini)
    gemini._clients.clear()

    with monkeypatch.context() as m:
        m.setattr(gemini.genai, "Client", MagicMock())
        try:
            gemini._get_client("cloud-key")
        except gemini.GeminiError as exc:
            assert "GEMINI_CLOUD_PROJECT" in str(exc)
        else:
            raise AssertionError("expected GeminiError")
