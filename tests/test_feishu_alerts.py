import json


def test_send_text_message_fetches_token_and_posts_chat_message(monkeypatch):
    from appcore import feishu_alerts

    settings = {
        "feishu_alerts.enabled": "1",
        "feishu_alerts.app_id": "cli_test",
        "feishu_alerts.app_secret": "secret_test",
        "feishu_alerts.chat_id": "oc_test",
    }
    monkeypatch.setattr(
        feishu_alerts.settings_store,
        "get_setting",
        lambda key: settings.get(key),
    )
    calls = []

    class FakeResponse:
        status_code = 200
        text = "ok"

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, *, json=None, headers=None, params=None, timeout=None):
        calls.append({
            "url": url,
            "json": json,
            "headers": headers,
            "params": params,
            "timeout": timeout,
        })
        if url.endswith("/tenant_access_token/internal"):
            return FakeResponse({"code": 0, "tenant_access_token": "tenant-token"})
        return FakeResponse({"code": 0, "data": {"message_id": "om_test"}})

    monkeypatch.setattr(feishu_alerts.requests, "post", fake_post)

    result = feishu_alerts.send_text_message("hello")

    assert result == {"ok": True, "message_id": "om_test"}
    assert calls[0]["json"] == {"app_id": "cli_test", "app_secret": "secret_test"}
    assert calls[1]["params"] == {"receive_id_type": "chat_id"}
    assert calls[1]["headers"]["Authorization"] == "Bearer tenant-token"
    assert calls[1]["json"]["receive_id"] == "oc_test"
    assert calls[1]["json"]["msg_type"] == "text"
    assert json.loads(calls[1]["json"]["content"]) == {"text": "hello"}


def test_send_feishu_test_alert_cli_outputs_json(monkeypatch, capsys):
    from tools import send_feishu_test_alert

    monkeypatch.setattr(
        send_feishu_test_alert.feishu_alerts,
        "send_test_alert",
        lambda message: {"ok": True, "message_id": "om_cli", "message": message},
    )

    exit_code = send_feishu_test_alert.main(["--message", "hello"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"ok": True, "message_id": "om_cli", "message": "hello"}


def _stub_settings(monkeypatch, **values):
    from appcore import feishu_alerts

    monkeypatch.setattr(
        feishu_alerts.settings_store,
        "get_setting",
        lambda key: values.get(key),
    )


def test_consecutive_failure_count_counts_back_until_success(monkeypatch):
    from appcore import feishu_alerts

    rows = [
        {"id": 50, "status": "failed"},
        {"id": 49, "status": "failed"},
        {"id": 48, "status": "failed"},
        {"id": 47, "status": "success"},
        {"id": 46, "status": "failed"},
    ]
    monkeypatch.setattr(feishu_alerts, "_query_recent_run_statuses", lambda task_code: rows)

    streak = feishu_alerts.consecutive_failure_count("roi_hourly_sync", current_run_id=50)
    assert streak == 3


def test_should_dispatch_failure_first_failure_after_success(monkeypatch):
    from appcore import feishu_alerts

    rows = [
        {"id": 11, "status": "failed"},
        {"id": 10, "status": "success"},
    ]
    monkeypatch.setattr(feishu_alerts, "_query_recent_run_statuses", lambda task_code: rows)
    _stub_settings(monkeypatch)

    should_send, streak = feishu_alerts.should_dispatch_failure(
        "roi_hourly_sync", current_run_id=11
    )
    assert (should_send, streak) == (True, 1)


def test_should_dispatch_failure_throttles_until_repeat_threshold(monkeypatch):
    from appcore import feishu_alerts

    # Streak of 3 failed runs, default repeat_every=5 → suppress.
    monkeypatch.setattr(
        feishu_alerts,
        "_query_recent_run_statuses",
        lambda task_code: [
            {"id": 13, "status": "failed"},
            {"id": 12, "status": "failed"},
            {"id": 11, "status": "failed"},
            {"id": 10, "status": "success"},
        ],
    )
    _stub_settings(monkeypatch)

    should_send, streak = feishu_alerts.should_dispatch_failure(
        "roi_hourly_sync", current_run_id=13
    )
    assert (should_send, streak) == (False, 3)


def test_should_dispatch_failure_fires_at_configured_repeat(monkeypatch):
    from appcore import feishu_alerts

    # Streak of 5 with repeat_every=5 → send.
    monkeypatch.setattr(
        feishu_alerts,
        "_query_recent_run_statuses",
        lambda task_code: [
            {"id": 15, "status": "failed"},
            {"id": 14, "status": "failed"},
            {"id": 13, "status": "failed"},
            {"id": 12, "status": "failed"},
            {"id": 11, "status": "failed"},
            {"id": 10, "status": "success"},
        ],
    )
    _stub_settings(monkeypatch, **{"feishu_alerts.failure_repeat_every": "5"})

    should_send, streak = feishu_alerts.should_dispatch_failure(
        "roi_hourly_sync", current_run_id=15
    )
    assert (should_send, streak) == (True, 5)


def test_prior_consecutive_failures_before_run_excludes_current(monkeypatch):
    from appcore import feishu_alerts

    monkeypatch.setattr(
        feishu_alerts,
        "_query_recent_run_statuses",
        lambda task_code: [
            {"id": 22, "status": "success"},
            {"id": 21, "status": "failed"},
            {"id": 20, "status": "failed"},
            {"id": 19, "status": "success"},
        ],
    )
    prior = feishu_alerts.prior_consecutive_failures_before_run(
        "roi_hourly_sync", current_run_id=22
    )
    assert prior == 2


def test_send_scheduled_task_recovery_skips_when_no_prior_failures(monkeypatch):
    from appcore import feishu_alerts

    sent = []
    monkeypatch.setattr(
        feishu_alerts,
        "send_text_message",
        lambda text, config=None: sent.append(text) or {"ok": True},
    )
    result = feishu_alerts.send_scheduled_task_recovery(
        {"task_code": "x", "id": 1}, prior_failures=0
    )
    assert result == {"ok": False, "skipped": True, "reason": "no_prior_failure"}
    assert sent == []


def test_send_scheduled_task_recovery_sends_with_prior_count(monkeypatch):
    from appcore import feishu_alerts

    sent = []
    _stub_settings(
        monkeypatch,
        **{
            "feishu_alerts.enabled": "1",
            "feishu_alerts.app_id": "x",
            "feishu_alerts.app_secret": "y",
            "feishu_alerts.chat_id": "z",
        },
    )
    monkeypatch.setattr(
        feishu_alerts,
        "send_text_message",
        lambda text, config=None: sent.append(text) or {"ok": True},
    )

    feishu_alerts.send_scheduled_task_recovery(
        {
            "task_code": "roi_hourly_sync",
            "task_name": "ROI sync",
            "id": 99,
            "started_at": "2026-05-09 18:00:00",
            "finished_at": "2026-05-09 18:01:00",
            "duration_seconds": 60,
        },
        prior_failures=4,
    )
    assert sent and "此前连续失败次数：4" in sent[0]
    assert "恢复" in sent[0]
