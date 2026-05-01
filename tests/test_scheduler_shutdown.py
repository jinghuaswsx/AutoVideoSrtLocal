"""Unit tests for ``appcore.scheduler`` shutdown / atexit behaviour.

The real BackgroundScheduler is not started; module-level ``_scheduler``
and ``atexit.register`` are monkeypatched to observe behaviour.
"""
from __future__ import annotations


class _FakeScheduler:
    def __init__(self) -> None:
        self.running = True
        self.shutdown_calls: list[bool] = []

    def shutdown(self, wait: bool = False) -> None:
        self.shutdown_calls.append(wait)
        self.running = False


def test_shutdown_scheduler_noop_when_not_started(monkeypatch):
    from appcore import scheduler

    monkeypatch.setattr(scheduler, "_scheduler", None, raising=False)
    scheduler.shutdown_scheduler()  # should not raise
    assert scheduler.current_scheduler() is None


def test_shutdown_scheduler_calls_underlying_shutdown_when_running(monkeypatch):
    from appcore import scheduler

    fake = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake, raising=False)

    scheduler.shutdown_scheduler(wait=False)

    assert fake.shutdown_calls == [False]
    assert scheduler.current_scheduler() is None


def test_shutdown_scheduler_skips_when_already_stopped(monkeypatch):
    from appcore import scheduler

    fake = _FakeScheduler()
    fake.running = False
    monkeypatch.setattr(scheduler, "_scheduler", fake, raising=False)

    scheduler.shutdown_scheduler()

    assert fake.shutdown_calls == []
    # finally clause still clears the singleton
    assert scheduler.current_scheduler() is None


def test_shutdown_scheduler_idempotent(monkeypatch):
    from appcore import scheduler

    fake = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake, raising=False)

    scheduler.shutdown_scheduler()
    scheduler.shutdown_scheduler()  # second call must not raise

    assert fake.shutdown_calls == [False]
    assert scheduler.current_scheduler() is None


def test_shutdown_scheduler_swallows_underlying_exception(monkeypatch, caplog):
    import logging

    from appcore import scheduler

    class _Boom:
        running = True

        def shutdown(self, wait=False):
            raise RuntimeError("scheduler boom")

    monkeypatch.setattr(scheduler, "_scheduler", _Boom(), raising=False)
    with caplog.at_level(logging.WARNING, logger="appcore.scheduler"):
        scheduler.shutdown_scheduler()  # must not re-raise
    assert any("shutdown failed" in r.message for r in caplog.records)
    assert scheduler.current_scheduler() is None


def test_register_atexit_shutdown_registers_once(monkeypatch):
    from appcore import scheduler

    monkeypatch.setattr(scheduler, "_atexit_registered", False, raising=False)
    registrations: list = []

    def fake_register(fn):
        registrations.append(fn)
        return fn

    monkeypatch.setattr(scheduler.atexit, "register", fake_register)

    scheduler.register_atexit_shutdown()
    scheduler.register_atexit_shutdown()

    assert len(registrations) == 1
    assert registrations[0] is scheduler.shutdown_scheduler
    assert scheduler._atexit_registered is True


def test_register_atexit_shutdown_skips_when_already_registered(monkeypatch):
    from appcore import scheduler

    monkeypatch.setattr(scheduler, "_atexit_registered", True, raising=False)
    called: list = []

    def fake_register(fn):
        called.append(fn)
        return fn

    monkeypatch.setattr(scheduler.atexit, "register", fake_register)
    scheduler.register_atexit_shutdown()
    assert called == []
