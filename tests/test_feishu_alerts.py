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
