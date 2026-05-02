import pytest

from pipeline import seedance
from unittest.mock import MagicMock


def test_seedance_poll_timeout_is_tripled():
    assert seedance.POLL_TIMEOUT == 1800


def test_seedance_v2_submit_uses_configured_base_url_and_model(monkeypatch):
    posted = {}
    response = MagicMock()
    response.json.return_value = {"id": "seed-task-db"}

    def fake_post(url, json=None, headers=None, timeout=None):
        posted["url"] = url
        posted["json"] = json
        posted["headers"] = headers
        posted["timeout"] = timeout
        return response

    monkeypatch.setattr(seedance.requests, "post", fake_post)

    task_id = seedance.create_video_task_v2(
        api_key="seedance-key",
        prompt="make video",
        model="seedance-db-model",
        base_url="https://seedance.proxy.example/api/v9/",
    )

    assert task_id == "seed-task-db"
    assert posted["url"] == "https://seedance.proxy.example/api/v9/contents/generations/tasks"
    assert posted["json"]["model"] == "seedance-db-model"


def test_seedance_poll_uses_configured_base_url(monkeypatch):
    response = MagicMock()
    response.json.return_value = {
        "status": "succeeded",
        "content": {"video_url": "https://cdn.example/result.mp4"},
    }
    called = {}

    def fake_get(url, headers=None, timeout=None):
        called["url"] = url
        called["headers"] = headers
        return response

    monkeypatch.setattr(seedance.requests, "get", fake_get)

    result = seedance.poll_video_task(
        "seedance-key",
        "task-db-url",
        interval=0,
        timeout=1,
        base_url="https://seedance.proxy.example/api/v9",
    )

    assert result["video_url"] == "https://cdn.example/result.mp4"
    assert called["url"] == "https://seedance.proxy.example/api/v9/contents/generations/tasks/task-db-url"


def test_seedance_poll_exits_immediately_when_shutdown_requested(monkeypatch):
    from appcore import cancellation, shutdown_coordinator

    shutdown_coordinator.request_shutdown("test-seedance")
    get_calls = []

    def fake_get(*args, **kwargs):
        get_calls.append((args, kwargs))
        response = MagicMock()
        response.json.return_value = {"status": "running"}
        return response

    monkeypatch.setattr(seedance.requests, "get", fake_get)
    monkeypatch.setattr(
        seedance.time,
        "sleep",
        lambda seconds: pytest.fail("poll_video_task should use cancellable sleep"),
    )

    try:
        with pytest.raises(cancellation.OperationCancelled):
            seedance.poll_video_task(
                "seedance-key",
                "task-cancel",
                interval=30,
                timeout=60,
            )

        assert get_calls == []
    finally:
        shutdown_coordinator.reset()
