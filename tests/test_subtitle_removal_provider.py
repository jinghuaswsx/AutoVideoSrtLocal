from __future__ import annotations

import importlib

import pytest

from appcore.llm_provider_configs import ProviderConfigError


class FakeProviderConfig:
    provider_code = "subtitle_removal"
    display_name = "字幕移除"

    def __init__(
        self,
        *,
        api_key: str = "GOLDEN_demo",
        base_url: str = "https://goodline.simplemokey.com/api/openAi",
        notify_url: str = "",
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.extra_config = {"notify_url": notify_url} if notify_url else {}

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ProviderConfigError(
                "缺少供应商配置 subtitle_removal.api_key，请在 /settings 填写。"
            )
        return self.api_key

    def require_base_url(self, default: str | None = None) -> str:
        value = (self.base_url or "").strip() or (default or "").strip()
        if not value:
            raise ProviderConfigError(
                "缺少供应商配置 subtitle_removal.base_url，请在 /settings 填写。"
            )
        return value


def configure_provider(monkeypatch, provider, **kwargs) -> FakeProviderConfig:
    cfg = FakeProviderConfig(**kwargs)
    monkeypatch.setattr(provider, "require_provider_config", lambda code: cfg)
    return cfg


def test_subtitle_removal_provider_submit_builds_expected_payload(monkeypatch):
    import appcore.subtitle_removal_provider as provider
    from appcore.llm_provider_configs import ProviderConfigError

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
    configure_provider(
        monkeypatch,
        provider,
        notify_url="https://example.test/notify",
    )

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
    from appcore.llm_provider_configs import ProviderConfigError

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
    configure_provider(monkeypatch, provider)

    payload = provider.query_progress("provider-task-1")

    assert payload["taskId"] == "provider-task-1"
    assert payload["status"] == "success"
    assert payload["resultUrl"].endswith("result.mp4")


def test_subtitle_removal_provider_requires_token(monkeypatch):
    provider = importlib.import_module("appcore.subtitle_removal_provider")
    provider = importlib.reload(provider)
    from appcore.llm_provider_configs import ProviderConfigError

    configure_provider(monkeypatch, provider, api_key="")

    with pytest.raises(provider.SubtitleRemovalProviderError, match="subtitle_removal.api_key"):
        provider.submit_task(
            file_size_mb=1.0,
            duration_seconds=1.0,
            resolution="720x1280",
            video_name="demo",
            source_url="https://tos.example/source.mp4",
        )


def test_subtitle_removal_provider_rejects_blank_url(monkeypatch):
    import appcore.subtitle_removal_provider as provider
    from appcore.llm_provider_configs import ProviderConfigError

    configure_provider(monkeypatch, provider, base_url="")

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "msg": "ok", "data": {"taskId": "provider-task-1"}}

    def fake_post(url, **kwargs):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(provider.requests, "post", fake_post)
    provider.submit_task(
            file_size_mb=1.0,
            duration_seconds=1.0,
            resolution="720x1280",
            video_name="demo",
            source_url="https://tos.example/source.mp4",
    )
    assert captured["url"] == "https://goodline.simplemokey.com/api/openAi"


def test_subtitle_removal_provider_wraps_request_exceptions(monkeypatch):
    import appcore.subtitle_removal_provider as provider
    from appcore.llm_provider_configs import ProviderConfigError

    configure_provider(monkeypatch, provider)

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
    from appcore.llm_provider_configs import ProviderConfigError

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("no json")

    configure_provider(monkeypatch, provider)
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
    from appcore.llm_provider_configs import ProviderConfigError

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 1, "msg": "failed"}

    configure_provider(monkeypatch, provider)
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
    from appcore.llm_provider_configs import ProviderConfigError

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "msg": "ok", "data": []}

    configure_provider(monkeypatch, provider)
    monkeypatch.setattr(provider.requests, "post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(provider.SubtitleRemovalProviderError, match="missing data"):
        provider.query_progress("provider-task-1")


def test_subtitle_removal_provider_subtitle_mode_omits_operation(monkeypatch):
    import appcore.subtitle_removal_provider as provider
    from appcore.llm_provider_configs import ProviderConfigError

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
    configure_provider(
        monkeypatch,
        provider,
        api_key="TOKEN",
        base_url="https://goodline.example/api",
    )

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
    from appcore.llm_provider_configs import ProviderConfigError

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
    configure_provider(
        monkeypatch,
        provider,
        api_key="TOKEN",
        base_url="https://goodline.example/api",
    )

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
    from appcore.llm_provider_configs import ProviderConfigError

    configure_provider(
        monkeypatch,
        provider,
        api_key="TOKEN",
        base_url="https://goodline.example/api",
    )

    with pytest.raises(ValueError, match="erase_text_type"):
        provider.submit_task(
            file_size_mb=1.0,
            duration_seconds=1.0,
            resolution="720x1280",
            video_name="demo",
            source_url="https://tos.example/s.mp4",
            erase_text_type="bogus",
        )
