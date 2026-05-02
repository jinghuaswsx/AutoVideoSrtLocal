import threading
import time

import pytest


@pytest.fixture(autouse=True)
def _reset_shutdown_state():
    from appcore import shutdown_coordinator

    shutdown_coordinator.reset()
    yield
    shutdown_coordinator.reset()


def test_initial_state_not_requested():
    from appcore import shutdown_coordinator

    assert shutdown_coordinator.is_shutdown_requested() is False
    assert shutdown_coordinator.reason() is None


def test_request_shutdown_sets_state():
    from appcore import shutdown_coordinator

    shutdown_coordinator.request_shutdown("signal=SIGTERM")
    assert shutdown_coordinator.is_shutdown_requested() is True
    assert shutdown_coordinator.reason() == "signal=SIGTERM"


def test_request_shutdown_idempotent_first_reason_wins():
    from appcore import shutdown_coordinator

    shutdown_coordinator.request_shutdown("first")
    shutdown_coordinator.request_shutdown("second")
    assert shutdown_coordinator.reason() == "first"


def test_request_shutdown_default_reason_set():
    from appcore import shutdown_coordinator

    shutdown_coordinator.request_shutdown()
    assert shutdown_coordinator.reason() == "unspecified"


def test_wait_returns_true_when_set():
    from appcore import shutdown_coordinator

    shutdown_coordinator.request_shutdown()
    assert shutdown_coordinator.wait(0.05) is True


def test_wait_returns_false_on_timeout():
    from appcore import shutdown_coordinator

    assert shutdown_coordinator.wait(0.05) is False


def test_throw_if_cancel_requested_no_op_when_clean():
    from appcore import cancellation

    cancellation.throw_if_cancel_requested()


def test_throw_if_cancel_requested_raises_when_requested():
    from appcore import cancellation, shutdown_coordinator

    shutdown_coordinator.request_shutdown("signal=SIGTERM")
    with pytest.raises(cancellation.OperationCancelled):
        cancellation.throw_if_cancel_requested()


def test_cancellable_sleep_runs_to_completion_when_clean():
    from appcore import cancellation

    start = time.monotonic()
    cancellation.cancellable_sleep(0.05)
    elapsed = time.monotonic() - start
    assert 0.04 <= elapsed < 1.0


def test_cancellable_sleep_interrupted_raises():
    from appcore import cancellation, shutdown_coordinator

    def trigger():
        time.sleep(0.05)
        shutdown_coordinator.request_shutdown("test-trigger")

    threading.Thread(target=trigger).start()
    with pytest.raises(cancellation.OperationCancelled):
        cancellation.cancellable_sleep(2.0)


def test_cancellable_sleep_zero_seconds_no_op_when_clean():
    from appcore import cancellation

    cancellation.cancellable_sleep(0)


def test_cancellable_sleep_zero_seconds_raises_when_requested():
    from appcore import cancellation, shutdown_coordinator

    shutdown_coordinator.request_shutdown()
    with pytest.raises(cancellation.OperationCancelled):
        cancellation.cancellable_sleep(0)


def test_wait_for_active_tasks_returns_zero_when_empty():
    from appcore import shutdown_coordinator

    assert shutdown_coordinator.wait_for_active_tasks(0.05) == 0


def test_wait_for_active_tasks_returns_count_on_timeout():
    from appcore import shutdown_coordinator, task_recovery

    task_recovery.register_active_task("t", "task-1")
    try:
        assert shutdown_coordinator.wait_for_active_tasks(0.05) == 1
    finally:
        task_recovery.unregister_active_task("t", "task-1")


def test_wait_for_active_tasks_returns_zero_after_drain():
    from appcore import shutdown_coordinator, task_recovery

    task_recovery.register_active_task("t", "task-2")

    def drain():
        time.sleep(0.05)
        task_recovery.unregister_active_task("t", "task-2")

    threading.Thread(target=drain).start()
    assert shutdown_coordinator.wait_for_active_tasks(2.0, poll_interval=0.02) == 0


def test_reset_clears_state():
    from appcore import shutdown_coordinator

    shutdown_coordinator.request_shutdown("x")
    shutdown_coordinator.reset()
    assert shutdown_coordinator.is_shutdown_requested() is False
    assert shutdown_coordinator.reason() is None
