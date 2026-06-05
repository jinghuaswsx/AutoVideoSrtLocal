import pytest


def test_dianxiaomi_yuncang_sync_records_scheduled_task_run(monkeypatch, capsys):
    from tools import dianxiaomi_yuncang_sync as mod

    calls: list[tuple] = []
    summary = {"fetched": 3, "inserted": 3, "refresh_prices": {"refreshed": 2}}

    monkeypatch.setattr(mod.dianxiaomi_yuncang, "sync_skus", lambda cdp_url: summary)
    monkeypatch.setattr(mod.scheduled_tasks, "start_run", lambda code: calls.append(("start", code)) or 202)
    monkeypatch.setattr(
        mod.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: calls.append(("finish", run_id, kwargs)),
    )

    assert mod.main(["--cdp-url", "http://127.0.0.1:9225"]) == 0
    assert calls == [
        ("start", "dianxiaomi_yuncang_sync"),
        ("finish", 202, {"status": "success", "summary": summary}),
    ]
    assert '"inserted": 3' in capsys.readouterr().out


def test_dianxiaomi_yuncang_sync_records_failure(monkeypatch):
    from tools import dianxiaomi_yuncang_sync as mod

    calls: list[tuple] = []

    def boom(*, cdp_url):
        raise RuntimeError("dxm login expired")

    monkeypatch.setattr(mod.dianxiaomi_yuncang, "sync_skus", boom)
    monkeypatch.setattr(mod.scheduled_tasks, "start_run", lambda code: calls.append(("start", code)) or 203)
    monkeypatch.setattr(
        mod.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: calls.append(("finish", run_id, kwargs)),
    )

    with pytest.raises(RuntimeError, match="dxm login expired"):
        mod.main([])

    assert calls == [
        ("start", "dianxiaomi_yuncang_sync"),
        ("finish", 203, {"status": "failed", "error_message": "dxm login expired"}),
    ]
