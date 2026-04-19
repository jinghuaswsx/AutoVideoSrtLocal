from __future__ import annotations


def test_link_check_runner_tracks_active_task(monkeypatch):
    from web.services import link_check_runner

    calls = []

    monkeypatch.setattr(link_check_runner, "_running", set(), raising=False)
    monkeypatch.setattr(
        link_check_runner,
        "register_active_task",
        lambda project_type, task_id: calls.append(("register", project_type, task_id)),
    )
    monkeypatch.setattr(
        link_check_runner,
        "unregister_active_task",
        lambda project_type, task_id: calls.append(("unregister", project_type, task_id)),
    )

    class _Runtime:
        def start(self, task_id):
            calls.append(("start", task_id))

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(link_check_runner, "LinkCheckRuntime", lambda: _Runtime())
    monkeypatch.setattr(link_check_runner.threading, "Thread", _ImmediateThread)

    assert link_check_runner.start("lc-1") is True
    assert calls == [
        ("register", "link_check", "lc-1"),
        ("start", "lc-1"),
        ("unregister", "link_check", "lc-1"),
    ]
    assert "lc-1" not in link_check_runner._running


def test_link_check_runner_rejects_duplicate_task(monkeypatch):
    from web.services import link_check_runner

    monkeypatch.setattr(link_check_runner, "_running", {"lc-dup"}, raising=False)
    register_calls = []
    monkeypatch.setattr(
        link_check_runner,
        "register_active_task",
        lambda *args: register_calls.append(args),
    )

    assert link_check_runner.start("lc-dup") is False
    assert register_calls == []
