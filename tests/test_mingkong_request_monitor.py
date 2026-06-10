from __future__ import annotations

from datetime import datetime

import pytest
import requests


def test_record_request_writes_operational_metadata_only():
    from appcore import mingkong_request_monitor as monitor

    calls = []

    def fake_execute(sql, args=None):
        calls.append((sql, args))
        return 1

    ok = monitor.record_request(
        source="unit.test",
        method="GET",
        url="https://os.wedev.vip/api/marketing/medias?page=1&q=demo",
        status_code=200,
        duration_ms=123,
        response_bytes=456,
        called_at=datetime(2026, 6, 10, 12, 0, 1),
        execute_fn=fake_execute,
    )

    assert ok is True
    assert "CREATE TABLE IF NOT EXISTS mingkong_outbound_request_logs" in calls[0][0]
    insert_args = calls[1][1]
    assert insert_args == (
        datetime(2026, 6, 10, 12, 0, 1),
        "unit.test",
        "GET",
        "os.wedev.vip",
        "/api/marketing/medias",
        200,
        123,
        456,
        None,
        None,
    )


def test_tracked_get_records_success_and_transport_failure(monkeypatch):
    from appcore import mingkong_request_monitor as monitor

    records = []
    monkeypatch.setattr(monitor, "record_request", lambda **kwargs: records.append(kwargs) or True)

    class FakeResponse:
        status_code = 206
        headers = {"content-length": "99"}

    def fake_get(url, **kwargs):
        assert url == "https://os.wedev.vip/medias/demo.mp4"
        assert kwargs["stream"] is True
        return FakeResponse()

    response = monitor.tracked_get(
        "https://os.wedev.vip/medias/demo.mp4",
        source="unit.success",
        request_fn=fake_get,
        stream=True,
    )

    assert response.status_code == 206
    assert records[-1]["source"] == "unit.success"
    assert records[-1]["method"] == "GET"
    assert records[-1]["status_code"] == 206
    assert records[-1]["response_bytes"] == 99

    def fail_get(url, **kwargs):
        raise requests.ReadTimeout("down")

    with pytest.raises(requests.ReadTimeout):
        monitor.tracked_get(
            "https://os.wedev.vip/api/marketing/medias",
            source="unit.failure",
            request_fn=fail_get,
        )

    assert records[-1]["source"] == "unit.failure"
    assert isinstance(records[-1]["error"], requests.ReadTimeout)


def test_evaluate_minute_buckets_flags_any_minute_above_threshold():
    from appcore import mingkong_request_monitor as monitor

    summary = monitor.evaluate_minute_buckets(
        [
            {
                "minute_bucket": "2026-06-10 14:25:00",
                "request_count": 61,
                "error_count": 2,
                "first_called_at": "2026-06-10 14:25:01",
                "last_called_at": "2026-06-10 14:25:59",
                "sources": "a,b",
            },
            {"minute_bucket": "2026-06-10 14:24:00", "request_count": 60},
        ],
        threshold_per_minute=60,
    )

    assert summary["breached"] is True
    assert summary["max_requests_per_minute"] == 61
    assert summary["breached_minutes"][0]["minute"] == "2026-06-10 14:25:00"
    assert summary["breached_minutes"][0]["request_count"] == 61


def test_run_scheduled_check_marks_failed_for_threshold_breach(monkeypatch):
    from appcore import mingkong_request_monitor as monitor

    events = []
    monkeypatch.setattr("appcore.scheduled_tasks.start_run", lambda task_code: events.append(("start", task_code)) or 42)
    monkeypatch.setattr(
        "appcore.scheduled_tasks.finish_run",
        lambda run_id, status, summary=None, error_message=None: events.append(
            ("finish", run_id, status, summary, error_message)
        ),
    )
    monkeypatch.setattr(
        monitor,
        "inspect_recent_window",
        lambda **_kwargs: {
            "threshold_per_minute": 60,
            "breached": True,
            "breached_minutes": [{"minute": "2026-06-10 14:25:00", "request_count": 61}],
        },
    )

    summary = monitor.run_scheduled_check()

    assert summary["breached"] is True
    assert events[0] == ("start", monitor.TASK_CODE)
    assert events[1][0:3] == ("finish", 42, "failed")
    assert "14:25:00" in events[1][4]
