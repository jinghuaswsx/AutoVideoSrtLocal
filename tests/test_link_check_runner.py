from __future__ import annotations


def test_start_registers_active_task_and_clears_it_after_success(monkeypatch):
    from appcore import runner_lifecycle, task_recovery
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
            assert task_recovery.is_task_active("link_check", task_id) is True
            calls.append(("runtime", task_id))

    monkeypatch.setattr(mod, "LinkCheckRuntime", lambda: FakeRuntime())
    monkeypatch.setattr(runner_lifecycle.threading, "Thread", ImmediateThread)

    mod._running.discard("lc-runner-1")
    task_recovery.unregister_active_task("link_check", "lc-runner-1")
    assert mod.start("lc-runner-1") is True
    assert ("runtime", "lc-runner-1") in calls
    assert task_recovery.is_task_active("link_check", "lc-runner-1") is False
    assert "lc-runner-1" not in mod._running


def test_start_unregisters_active_task_after_failure(monkeypatch):
    from appcore import runner_lifecycle, task_recovery
    from web.services import link_check_runner as mod

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
    monkeypatch.setattr(runner_lifecycle.threading, "Thread", ImmediateThread)

    mod._running.discard("lc-runner-2")
    task_recovery.unregister_active_task("link_check", "lc-runner-2")
    assert mod.start("lc-runner-2") is True
    assert task_recovery.is_task_active("link_check", "lc-runner-2") is False
    assert "lc-runner-2" not in mod._running


def test_start_rejects_duplicate_task(monkeypatch):
    from appcore import runner_lifecycle
    from web.services import link_check_runner as mod

    starts = []

    class FakeThread:
        def __init__(self, *, target, daemon):
            self._target = target

        def start(self):
            starts.append("thread")

    monkeypatch.setattr(mod, "_running", {"lc-dup"}, raising=False)
    monkeypatch.setattr(runner_lifecycle.threading, "Thread", FakeThread)

    assert mod.start("lc-dup") is False
    assert starts == []
