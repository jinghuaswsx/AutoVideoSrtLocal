from __future__ import annotations


def test_start_registers_active_task_and_clears_it_after_success(monkeypatch):
    from web.services import link_check_runner as mod

    calls = []

    class ImmediateThread:
        def __init__(self, *, target, daemon):
            self._target = target

        def start(self):
            try:
                self._target()
            except Exception:
                pass

    class FakeRuntime:
        def start(self, task_id: str) -> None:
            calls.append(("runtime", task_id))

    monkeypatch.setattr(mod, "LinkCheckRuntime", lambda: FakeRuntime())
    monkeypatch.setattr(mod, "register_active_task", lambda project_type, task_id: calls.append(("register", project_type, task_id)))
    monkeypatch.setattr(mod, "unregister_active_task", lambda project_type, task_id: calls.append(("unregister", project_type, task_id)))
    monkeypatch.setattr(mod.threading, "Thread", ImmediateThread)

    mod._running.discard("lc-runner-1")
    assert mod.start("lc-runner-1") is True
    assert ("register", "link_check", "lc-runner-1") in calls
    assert ("runtime", "lc-runner-1") in calls
    assert ("unregister", "link_check", "lc-runner-1") in calls
    assert "lc-runner-1" not in mod._running


def test_start_unregisters_active_task_after_failure(monkeypatch):
    from web.services import link_check_runner as mod

    calls = []

    class ImmediateThread:
        def __init__(self, *, target, daemon):
            self._target = target

        def start(self):
            try:
                self._target()
            except Exception:
                pass

    class FakeRuntime:
        def start(self, task_id: str) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(mod, "LinkCheckRuntime", lambda: FakeRuntime())
    monkeypatch.setattr(mod, "register_active_task", lambda project_type, task_id: calls.append(("register", project_type, task_id)))
    monkeypatch.setattr(mod, "unregister_active_task", lambda project_type, task_id: calls.append(("unregister", project_type, task_id)))
    monkeypatch.setattr(mod.threading, "Thread", ImmediateThread)

    mod._running.discard("lc-runner-2")
    assert mod.start("lc-runner-2") is True
    assert calls[0] == ("register", "link_check", "lc-runner-2")
    assert calls[-1] == ("unregister", "link_check", "lc-runner-2")
    assert "lc-runner-2" not in mod._running


def test_start_rejects_duplicate_task(monkeypatch):
    from web.services import link_check_runner as mod

    register_calls = []

    monkeypatch.setattr(mod, "_running", {"lc-dup"}, raising=False)
    monkeypatch.setattr(
        mod,
        "register_active_task",
        lambda *args: register_calls.append(args),
    )

    assert mod.start("lc-dup") is False
    assert register_calls == []
