from __future__ import annotations

import importlib

import pytest


def test_subtitle_removal_provider_submit_builds_expected_payload(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "msg": "ok", "data": {"taskId": "provider-task-1"}}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(provider.requests, "post", fake_post)
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.simplemokey.com/api/openAi")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "GOLDEN_demo")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_NOTIFY_URL", "https://example.test/notify")

    task_id = provider.submit_task(
        file_size_mb=2.094,
        duration_seconds=10.006,
        resolution="720x1280",
        video_name="sr_task_0_0_720_1280",
        source_url="https://tos.example/source.mp4",
        cover_url="https://tos.example/cover.jpg",
    )

    assert task_id == "provider-task-1"
    assert captured["url"] == "https://goodline.simplemokey.com/api/openAi"
    assert captured["headers"]["authorization"] == "GOLDEN_demo"
    assert captured["json"]["biz"] == "aiRemoveSubtitleSubmitTask"
    assert captured["json"]["fileSize"] == 2.09
    assert captured["json"]["duration"] == 10.01
    assert captured["json"]["resolution"] == "720x1280"
    assert captured["json"]["videoName"] == "sr_task_0_0_720_1280"
    assert captured["json"]["coverUrl"] == "https://tos.example/cover.jpg"
    assert captured["json"]["url"] == "https://tos.example/source.mp4"
    assert captured["json"]["notifyUrl"] == "https://example.test/notify"
    assert captured["timeout"] == 30


def test_subtitle_removal_provider_progress_returns_first_item(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 0,
                "msg": "ok",
                "data": [
                    {
                        "taskId": "provider-task-1",
                        "status": "success",
                        "resultUrl": "https://provider.example/result.mp4",
                        "position": "{\"l\":0,\"t\":0,\"w\":720,\"h\":1280}",
                    }
                ],
            }

    monkeypatch.setattr(provider.requests, "post", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.simplemokey.com/api/openAi")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "GOLDEN_demo")

    payload = provider.query_progress("provider-task-1")

    assert payload["taskId"] == "provider-task-1"
    assert payload["status"] == "success"
    assert payload["resultUrl"].endswith("result.mp4")


def test_subtitle_removal_provider_requires_token(monkeypatch):
    provider = importlib.import_module("appcore.subtitle_removal_provider")
    provider = importlib.reload(provider)

    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "")

    with pytest.raises(provider.SubtitleRemovalProviderError, match="SUBTITLE_REMOVAL_PROVIDER_TOKEN"):
        provider.submit_task(
            file_size_mb=1.0,
            duration_seconds=1.0,
            resolution="720x1280",
            video_name="demo",
            source_url="https://tos.example/source.mp4",
        )


def test_subtitle_removal_provider_rejects_blank_url(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "   ")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "GOLDEN_demo")

    with pytest.raises(provider.SubtitleRemovalProviderError, match="SUBTITLE_REMOVAL_PROVIDER_URL"):
        provider.submit_task(
            file_size_mb=1.0,
            duration_seconds=1.0,
            resolution="720x1280",
            video_name="demo",
            source_url="https://tos.example/source.mp4",
        )


def test_subtitle_removal_provider_wraps_request_exceptions(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.simplemokey.com/api/openAi")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "GOLDEN_demo")

    def fake_post(*args, **kwargs):
        raise provider.requests.RequestException("network down")

    monkeypatch.setattr(provider.requests, "post", fake_post)

    with pytest.raises(provider.SubtitleRemovalProviderError, match="network down"):
        provider.submit_task(
            file_size_mb=1.0,
            duration_seconds=1.0,
            resolution="720x1280",
            video_name="demo",
            source_url="https://tos.example/source.mp4",
        )


def test_subtitle_removal_provider_wraps_invalid_json(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("no json")

    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.simplemokey.com/api/openAi")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "GOLDEN_demo")
    monkeypatch.setattr(provider.requests, "post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(provider.SubtitleRemovalProviderError, match="no json"):
        provider.submit_task(
            file_size_mb=1.0,
            duration_seconds=1.0,
            resolution="720x1280",
            video_name="demo",
            source_url="https://tos.example/source.mp4",
        )


def test_subtitle_removal_provider_rejects_nonzero_code(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 1, "msg": "failed"}

    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.simplemokey.com/api/openAi")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "GOLDEN_demo")
    monkeypatch.setattr(provider.requests, "post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(provider.SubtitleRemovalProviderError, match="failed"):
        provider.submit_task(
            file_size_mb=1.0,
            duration_seconds=1.0,
            resolution="720x1280",
            video_name="demo",
            source_url="https://tos.example/source.mp4",
        )


def test_subtitle_removal_provider_rejects_empty_progress_data(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "msg": "ok", "data": []}

    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.simplemokey.com/api/openAi")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "GOLDEN_demo")
    monkeypatch.setattr(provider.requests, "post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(provider.SubtitleRemovalProviderError, match="missing data"):
        provider.query_progress("provider-task-1")


def test_subtitle_removal_provider_subtitle_mode_omits_operation(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "msg": "ok", "data": {"taskId": "provider-task-1"}}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(provider.requests, "post", fake_post)
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.example/api")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "TOKEN")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_NOTIFY_URL", "")

    provider.submit_task(
        file_size_mb=1.0,
        duration_seconds=1.0,
        resolution="720x1280",
        video_name="demo",
        source_url="https://tos.example/s.mp4",
        erase_text_type="subtitle",
    )

    assert "operation" not in captured["json"], "subtitle 模式不应下发 operation 字段"


def test_subtitle_removal_provider_text_mode_adds_operation(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "msg": "ok", "data": {"taskId": "provider-task-2"}}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(provider.requests, "post", fake_post)
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.example/api")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "TOKEN")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_NOTIFY_URL", "")

    provider.submit_task(
        file_size_mb=1.0,
        duration_seconds=1.0,
        resolution="720x1280",
        video_name="demo",
        source_url="https://tos.example/s.mp4",
        erase_text_type="text",
    )

    operation = captured["json"].get("operation")
    assert operation == {
        "type": "Task",
        "task": {
            "type": "Erase",
            "erase": {
                "mode": "Auto",
                "auto": {"type": "Text"},
            },
        },
    }


def test_subtitle_removal_provider_rejects_invalid_erase_text_type(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.example/api")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "TOKEN")

    with pytest.raises(ValueError, match="erase_text_type"):
        provider.submit_task(
            file_size_mb=1.0,
            duration_seconds=1.0,
            resolution="720x1280",
            video_name="demo",
            source_url="https://tos.example/s.mp4",
            erase_text_type="bogus",
        )
