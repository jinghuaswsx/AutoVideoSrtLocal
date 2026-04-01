from __future__ import annotations

import importlib
import sys
import types


def _reload_tos_clients(monkeypatch, *, use_private: bool):
    monkeypatch.setenv("TOS_PUBLIC_ENDPOINT", "public.tos.example.com")
    monkeypatch.setenv("TOS_PRIVATE_ENDPOINT", "private.tos.example.com")
    monkeypatch.setenv("TOS_BUCKET", "auto-video-srt")
    monkeypatch.setenv("TOS_REGION", "cn-shanghai")
    monkeypatch.setenv("TOS_ACCESS_KEY", "ak")
    monkeypatch.setenv("TOS_SECRET_KEY", "sk")
    monkeypatch.setenv("TOS_USE_PRIVATE_ENDPOINT", "true" if use_private else "false")
    monkeypatch.setenv("TOS_SIGNED_URL_EXPIRES", "3600")
    monkeypatch.setenv("TOS_BROWSER_UPLOAD_PREFIX", "uploads/")
    monkeypatch.setenv("TOS_FINAL_ARTIFACT_PREFIX", "artifacts/")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    else:
        import config  # noqa: F401

    if "appcore.tos_clients" in sys.modules:
        return importlib.reload(sys.modules["appcore.tos_clients"])
    return importlib.import_module("appcore.tos_clients")


def test_signed_urls_always_use_public_endpoint(monkeypatch):
    captured = []

    class FakeSignedUrl:
        def __init__(self, url):
            self.signed_url = url

    class FakeHttpMethod:
        def __init__(self, value):
            self.value = value

    class FakeClient:
        def __init__(self, *, endpoint, **kwargs):
            self.endpoint = endpoint

        def pre_signed_url(self, method, bucket, object_key, expires=3600):
            captured.append((self.endpoint, method.value, bucket, object_key, expires))
            return FakeSignedUrl(f"https://{self.endpoint}/{bucket}/{object_key}?method={method.value}")

    fake_tos = types.SimpleNamespace(
        TosClientV2=FakeClient,
        HttpMethodType=types.SimpleNamespace(
            Http_Method_Get=FakeHttpMethod("GET"),
            Http_Method_Put=FakeHttpMethod("PUT"),
        ),
    )
    monkeypatch.setitem(sys.modules, "tos", fake_tos)
    tos_clients = _reload_tos_clients(monkeypatch, use_private=True)

    download_url = tos_clients.generate_signed_download_url("artifacts/1/task/video.mp4")
    upload_url = tos_clients.generate_signed_upload_url("uploads/1/task/source.mp4")

    assert download_url.startswith("https://public.tos.example.com/")
    assert upload_url.startswith("https://public.tos.example.com/")
    assert captured[0][:2] == ("public.tos.example.com", "GET")
    assert captured[1][:2] == ("public.tos.example.com", "PUT")


def test_server_client_uses_public_when_private_disabled(monkeypatch):
    class FakeClient:
        def __init__(self, *, endpoint, **kwargs):
            self.endpoint = endpoint

        def head_bucket(self, bucket):
            raise AssertionError("private health check should not run when disabled")

    fake_tos = types.SimpleNamespace(TosClientV2=FakeClient)
    monkeypatch.setitem(sys.modules, "tos", fake_tos)
    tos_clients = _reload_tos_clients(monkeypatch, use_private=False)

    client = tos_clients.get_server_client()

    assert client.endpoint == "public.tos.example.com"


def test_private_probe_failure_falls_back_to_public(monkeypatch):
    probes = []

    class FakeClient:
        def __init__(self, *, endpoint, **kwargs):
            self.endpoint = endpoint

        def head_bucket(self, bucket):
            probes.append((self.endpoint, bucket))
            if self.endpoint == "private.tos.example.com":
                raise RuntimeError("private unavailable")
            return True

    fake_tos = types.SimpleNamespace(TosClientV2=FakeClient)
    monkeypatch.setitem(sys.modules, "tos", fake_tos)
    tos_clients = _reload_tos_clients(monkeypatch, use_private=True)

    client = tos_clients.get_server_client()

    assert client.endpoint == "public.tos.example.com"
    assert probes == [("private.tos.example.com", "auto-video-srt")]


def test_collect_task_tos_keys_includes_source_and_uploaded_artifacts(monkeypatch):
    monkeypatch.setitem(sys.modules, "tos", types.SimpleNamespace(TosClientV2=object))
    tos_clients = _reload_tos_clients(monkeypatch, use_private=False)

    state = {
        "source_tos_key": "uploads/1/task/source.mp4",
        "tos_uploads": {
            "normal:soft_video": {
                "tos_key": "artifacts/1/task/normal/example_soft.mp4",
                "artifact_kind": "soft_video",
                "variant": "normal",
            },
            "legacy/artifacts/example.srt": "srt",
        },
    }

    assert tos_clients.collect_task_tos_keys(state) == [
        "uploads/1/task/source.mp4",
        "artifacts/1/task/normal/example_soft.mp4",
        "legacy/artifacts/example.srt",
    ]
