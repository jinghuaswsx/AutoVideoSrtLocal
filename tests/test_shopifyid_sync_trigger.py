from types import SimpleNamespace


def test_trigger_returns_running_without_starting_systemctl(monkeypatch):
    from appcore import shopifyid_sync_trigger as trigger

    calls = []
    monkeypatch.setattr(trigger, "latest_run", lambda: {"status": "running"})
    monkeypatch.setattr(trigger.shutil, "which", lambda name: calls.append(name) or "/bin/systemctl")

    result = trigger.trigger()

    assert result["already_running"] is True
    assert not calls


def test_trigger_requires_systemctl(monkeypatch):
    from appcore import shopifyid_sync_trigger as trigger

    monkeypatch.setattr(trigger, "latest_run", lambda: None)
    monkeypatch.setattr(trigger.shutil, "which", lambda name: None)

    try:
        trigger.trigger()
    except RuntimeError as exc:
        assert "systemctl" in str(exc)
    else:
        raise AssertionError("trigger() should fail when systemctl is unavailable")


def test_trigger_starts_shopifyid_service(monkeypatch):
    from appcore import shopifyid_sync_trigger as trigger

    calls = []
    latest_values = [None, {"status": "running"}]

    def fake_latest():
        return latest_values.pop(0) if latest_values else {"status": "running"}

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(trigger, "latest_run", fake_latest)
    monkeypatch.setattr(trigger.shutil, "which", lambda name: "/bin/systemctl")
    monkeypatch.setattr(trigger.subprocess, "run", fake_run)

    result = trigger.trigger()

    assert result["already_running"] is False
    assert calls[0][0] == ["systemctl", "start", "--no-block", "autovideosrt-shopifyid-sync.service"]
    assert result["latest"] == {"status": "running"}
