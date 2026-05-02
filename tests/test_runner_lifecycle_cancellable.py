"""Verify ``start_tracked_thread`` cleanly handles cooperative cancellation.

Targets the ``except OperationCancelled`` branch added in the
graceful-shutdown work: a long task that raises OperationCancelled must
not produce an unhandled traceback in the worker log, and must always
release its slot in ``_active_tasks``.
"""
from __future__ import annotations

import logging
import threading
import time

import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    from appcore import shutdown_coordinator, task_recovery

    shutdown_coordinator.reset()
    with task_recovery._active_lock:
        task_recovery._active_tasks.clear()
    yield
    shutdown_coordinator.reset()
    with task_recovery._active_lock:
        task_recovery._active_tasks.clear()


def _wait_until_unregistered(project_type: str, task_id: str, *, timeout: float = 1.0) -> bool:
    from appcore import task_recovery

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not task_recovery.is_task_active(project_type, task_id):
            return True
        time.sleep(0.01)
    return False


def test_normal_target_completes_and_unregisters():
    from appcore import runner_lifecycle, task_recovery

    seen: list[bool] = []

    def target() -> None:
        seen.append(True)

    started = runner_lifecycle.start_tracked_thread(
        project_type="t", task_id="ok", target=target, daemon=True,
    )
    assert started is True
    assert _wait_until_unregistered("t", "ok")
    assert task_recovery.is_task_active("t", "ok") is False
    assert seen == [True]


def test_operation_cancelled_unregisters_without_traceback(caplog):
    from appcore import cancellation, runner_lifecycle, task_recovery

    def target() -> None:
        raise cancellation.OperationCancelled("signal=SIGTERM")

    with caplog.at_level(logging.WARNING, logger="appcore.runner_lifecycle"):
        started = runner_lifecycle.start_tracked_thread(
            project_type="t", task_id="cancel-me", target=target, daemon=True,
        )

    assert started is True
    assert _wait_until_unregistered("t", "cancel-me")
    assert task_recovery.is_task_active("t", "cancel-me") is False

    # log should record the cancellation, but no error/traceback record
    cancel_msgs = [r for r in caplog.records if "cancelled" in r.message]
    assert cancel_msgs, "expected a [lifecycle] cancelled warning"
    assert all(r.levelno == logging.WARNING for r in cancel_msgs)
    # exc_info must be empty (no traceback was attached)
    assert all(r.exc_info is None for r in cancel_msgs)


def test_unexpected_exception_still_unregisters_and_propagates_to_thread(monkeypatch):
    """An unexpected exception in target must not skip ``finally`` cleanup."""
    from appcore import runner_lifecycle, task_recovery

    # Silence threading.excepthook for this test so the deliberate
    # unhandled exception does not surface as a noisy warning at
    # pytest teardown.
    monkeypatch.setattr(threading, "excepthook", lambda args: None)

    barrier = threading.Event()

    def target() -> None:
        barrier.set()
        raise RuntimeError("kaboom")  # unexpected -- must still finally-cleanup

    started = runner_lifecycle.start_tracked_thread(
        project_type="t", task_id="boom", target=target, daemon=True,
    )
    assert started is True
    barrier.wait(1.0)
    assert _wait_until_unregistered("t", "boom")
    assert task_recovery.is_task_active("t", "boom") is False


def test_cancelled_via_shutdown_event_unregisters():
    from appcore import cancellation, runner_lifecycle, shutdown_coordinator, task_recovery

    started_event = threading.Event()
    proceed_event = threading.Event()

    def target() -> None:
        started_event.set()
        # block until the test releases us, then check cancellation
        proceed_event.wait(2.0)
        cancellation.throw_if_cancel_requested()

    started = runner_lifecycle.start_tracked_thread(
        project_type="t", task_id="cooperate", target=target, daemon=True,
    )
    assert started is True
    assert started_event.wait(1.0)
    shutdown_coordinator.request_shutdown("test")
    proceed_event.set()

    assert _wait_until_unregistered("t", "cooperate")
    assert task_recovery.is_task_active("t", "cooperate") is False
