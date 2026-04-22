from __future__ import annotations

import importlib
import sys
import threading
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


def test_private_probe_uses_fast_probe_client_settings(monkeypatch):
    constructed = []

    class FakeClient:
        def __init__(self, *, endpoint, **kwargs):
            self.endpoint = endpoint
            self.kwargs = kwargs
            constructed.append((endpoint, dict(kwargs)))

        def head_bucket(self, bucket):
            raise RuntimeError("private unavailable")

    fake_tos = types.SimpleNamespace(TosClientV2=FakeClient)
    monkeypatch.setitem(sys.modules, "tos", fake_tos)
    tos_clients = _reload_tos_clients(monkeypatch, use_private=True)

    assert tos_clients.private_endpoint_ready(force=True) is False

    private_inits = [kwargs for endpoint, kwargs in constructed if endpoint == "private.tos.example.com"]
    assert private_inits
    assert private_inits[0]["max_retry_count"] == 0
    assert private_inits[0]["connection_time"] == 2
    assert private_inits[0]["socket_timeout"] == 2


def test_private_probe_failure_is_shared_across_concurrent_callers(monkeypatch):
    probe_calls = 0
    probe_calls_lock = threading.Lock()
    release_probe = threading.Event()
    workers_ready = threading.Barrier(9)

    class FakeClient:
        def __init__(self, *, endpoint, **kwargs):
            self.endpoint = endpoint

        def head_bucket(self, bucket):
            nonlocal probe_calls
            with probe_calls_lock:
                probe_calls += 1
            release_probe.wait(timeout=1)
            raise RuntimeError("private unavailable")

    fake_tos = types.SimpleNamespace(TosClientV2=FakeClient)
    monkeypatch.setitem(sys.modules, "tos", fake_tos)
    tos_clients = _reload_tos_clients(monkeypatch, use_private=True)

    results: list[str] = []

    def _worker():
        workers_ready.wait(timeout=1)
        results.append(tos_clients.get_server_client().endpoint)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()

    workers_ready.wait(timeout=1)
    release_probe.wait(0.05)

    assert probe_calls == 1

    release_probe.set()
    for thread in threads:
        thread.join(timeout=1)

    assert results == ["public.tos.example.com"] * 8


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


def test_collect_task_tos_keys_includes_result_tos_key(monkeypatch):
    monkeypatch.setitem(sys.modules, "tos", types.SimpleNamespace(TosClientV2=object))
    tos_clients = _reload_tos_clients(monkeypatch, use_private=False)

    state = {
        "source_tos_key": "uploads/1/task/source.mp4",
        "result_tos_key": "artifacts/1/task/result.mp4",
    }

    assert tos_clients.collect_task_tos_keys(state) == [
        "uploads/1/task/source.mp4",
        "artifacts/1/task/result.mp4",
    ]


def _install_fake_tos(monkeypatch, *, head_returns_exists: bool):
    """注册一个假 TosClientV2，供 upload_media_object 用 put + head 校验。"""
    calls = []

    class FakeClient:
        def __init__(self, *, endpoint, **kwargs):
            self.endpoint = endpoint

        def put_object(self, bucket, object_key, content=None, content_type=None):
            calls.append(("put", bucket, object_key, content, content_type))

        def head_object(self, bucket, object_key):
            calls.append(("head", bucket, object_key))
            if not head_returns_exists:
                raise RuntimeError("NoSuchKey")
            return types.SimpleNamespace(content_length=0)

        def head_bucket(self, bucket):
            return True

    fake_tos = types.SimpleNamespace(TosClientV2=FakeClient)
    monkeypatch.setitem(sys.modules, "tos", fake_tos)
    return calls


def test_upload_media_object_raises_when_head_fails_after_put(monkeypatch):
    """put 看似成功但 head 找不到对象 → silent fail，必须主动抛错。"""
    calls = _install_fake_tos(monkeypatch, head_returns_exists=False)
    monkeypatch.setenv("TOS_MEDIA_BUCKET", "auto-video-srt-media")
    tos_clients = _reload_tos_clients(monkeypatch, use_private=False)

    import pytest
    with pytest.raises(Exception) as exc:
        tos_clients.upload_media_object("33/medias/316/x.gif", b"\x47\x49\x46", content_type="image/gif")

    msg = str(exc.value).lower()
    assert "33/medias/316/x.gif" in str(exc.value) or "no such" in msg or "verify" in msg or "缺失" in msg or "未找到" in msg
    # 确保 put 和 head 都调过
    op_names = [c[0] for c in calls]
    assert "put" in op_names
    assert "head" in op_names


def test_upload_media_object_succeeds_when_head_confirms(monkeypatch):
    calls = _install_fake_tos(monkeypatch, head_returns_exists=True)
    monkeypatch.setenv("TOS_MEDIA_BUCKET", "auto-video-srt-media")
    tos_clients = _reload_tos_clients(monkeypatch, use_private=False)

    tos_clients.upload_media_object("1/medias/1/a.jpg", b"jpeg-bytes", content_type="image/jpeg")

    op_names = [c[0] for c in calls]
    assert op_names == ["put", "head"]
