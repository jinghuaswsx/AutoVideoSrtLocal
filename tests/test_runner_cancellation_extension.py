"""Cancellation-checkpoint coverage for the runner extensions:

- bulk_translate_runtime.run_scheduler
- subtitle_removal_runtime.SubtitleRemovalRuntime._poll_until_terminal
- runtime_v2.PipelineRunnerV2._run

Each test exercises the new cancellation point in isolation, no DB and
no network. Smoke-style: raise OperationCancelled when shutdown is
requested, leave task state in a consistent shape.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    from appcore import shutdown_coordinator, task_state

    shutdown_coordinator.reset()
    task_state._tasks.clear()
    monkeypatch.setattr("appcore.db.execute", lambda *a, **k: None)
    yield
    shutdown_coordinator.reset()
    task_state._tasks.clear()


# ---------------------------------------------------------------------------
# bulk_translate_runtime.run_scheduler
# ---------------------------------------------------------------------------


def test_bulk_translate_run_scheduler_raises_on_shutdown(monkeypatch):
    from appcore import bulk_translate_runtime, cancellation, shutdown_coordinator

    monkeypatch.setattr(
        bulk_translate_runtime,
        "get_task",
        lambda task_id: {"status": "running", "state": {"plan": []}},
    )
    shutdown_coordinator.request_shutdown("test-bulk")
    with pytest.raises(cancellation.OperationCancelled):
        bulk_translate_runtime.run_scheduler("bt-1", max_loops=5, sleep_fn=lambda *_: None)


def test_bulk_translate_run_scheduler_normal_path_no_cancel(monkeypatch):
    """Sanity: without shutdown, scheduler does not raise OperationCancelled."""
    from appcore import bulk_translate_runtime, cancellation

    fake_task = {"status": "running", "state": {"plan": []}}
    monkeypatch.setattr(bulk_translate_runtime, "get_task", lambda task_id: fake_task)
    monkeypatch.setattr(bulk_translate_runtime, "_save_state", lambda *a, **k: None)
    monkeypatch.setattr(bulk_translate_runtime, "_emit", lambda *a, **k: None)

    # max_loops=1 -> one loop only; empty plan triggers the "done" branch
    # which returns cleanly. No OperationCancelled expected.
    try:
        bulk_translate_runtime.run_scheduler(
            "bt-2", max_loops=1, sleep_fn=lambda *_: None,
        )
    except cancellation.OperationCancelled:
        pytest.fail("run_scheduler raised OperationCancelled without shutdown")


# ---------------------------------------------------------------------------
# subtitle_removal_runtime._poll_until_terminal
# ---------------------------------------------------------------------------


def test_subtitle_removal_poll_raises_on_shutdown(monkeypatch):
    from appcore import cancellation, shutdown_coordinator, task_state
    from appcore.events import EventBus
    from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime

    task_id = "sr-1"
    task_state._tasks[task_id] = {
        "id": task_id,
        "type": "subtitle_removal",
        "status": "running",
        "provider_task_id": "upstream-xyz",
        "steps": {"submit": "done", "poll": "running"},
        "step_messages": {},
        "poll_attempts": 0,
    }

    runtime = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    shutdown_coordinator.request_shutdown("test-sr")

    with pytest.raises(cancellation.OperationCancelled):
        runtime._poll_until_terminal(task_id)


def test_subtitle_removal_poll_cancellable_sleep_unblocks_loop(monkeypatch):
    """The polling loop's cancellable_sleep wakes early when shutdown fires
    mid-sleep, so we exit without waiting the full poll interval."""
    import time

    from appcore import cancellation, shutdown_coordinator, task_state
    from appcore.events import EventBus
    from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime
    import appcore.subtitle_removal_runtime as srrt

    task_id = "sr-2"
    task_state._tasks[task_id] = {
        "id": task_id,
        "type": "subtitle_removal",
        "status": "running",
        "provider_task_id": "upstream-abc",
        "steps": {"submit": "done", "poll": "running"},
        "step_messages": {},
        "poll_attempts": 0,
    }

    monkeypatch.setattr(
        srrt,
        "query_progress",
        lambda pid: {"status": "running", "emsg": "", "resultUrl": ""},
    )

    runtime = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)

    # Schedule shutdown shortly after entering the sleep
    import threading
    threading.Timer(0.05, lambda: shutdown_coordinator.request_shutdown("test-mid-sleep")).start()

    start = time.monotonic()
    with pytest.raises(cancellation.OperationCancelled):
        runtime._poll_until_terminal(task_id)
    elapsed = time.monotonic() - start

    # Without cancellable_sleep we would wait config.SUBTITLE_REMOVAL_POLL_FAST_SECONDS
    # (typically several seconds). 1.0s is comfortably above the timer
    # delay (0.05s) and far below the natural poll interval.
    assert elapsed < 1.0, f"cancellable_sleep failed to wake early: {elapsed}s"


# ---------------------------------------------------------------------------
# runtime_v2.PipelineRunnerV2._run
# ---------------------------------------------------------------------------


def _seed_v2_task(task_id: str, *, step_names: list[str]) -> None:
    from appcore import task_state

    task_state._tasks[task_id] = {
        "id": task_id,
        "type": "translate_lab",
        "video_path": "/tmp/v2.mp4",
        "task_dir": "/tmp/v2",
        "target_language": "en",
        "steps": {name: "pending" for name in step_names},
        "step_messages": {name: "" for name in step_names},
        "status": "uploaded",
    }


def test_runtime_v2_cancels_mid_pipeline_and_marks_interrupted(monkeypatch):
    from appcore import cancellation, shutdown_coordinator, task_state
    from appcore.events import EVT_LAB_PIPELINE_ERROR, EventBus
    from appcore.runtime_v2 import PipelineRunnerV2

    monkeypatch.setattr(
        "appcore.source_video.ensure_local_source_video",
        lambda task_id: None,
    )

    task_id = "v2-cancel"
    _seed_v2_task(task_id, step_names=["extract", "asr", "translate"])

    ran: list[str] = []

    def step_extract():
        ran.append("extract")
        task_state.set_step(task_id, "extract", "done")
        shutdown_coordinator.request_shutdown("signal=SIGTERM")

    def step_asr():
        ran.append("asr")  # must NOT run

    def step_translate():
        ran.append("translate")  # must NOT run

    captured: list = []
    bus = EventBus()
    bus.subscribe(lambda event: captured.append(event))

    class _FakeV2(PipelineRunnerV2):
        def _build_steps(self, task_id, video_path, task_dir):
            return [
                ("extract", step_extract),
                ("asr", step_asr),
                ("translate", step_translate),
            ]

    runner = _FakeV2(bus=bus)

    with pytest.raises(cancellation.OperationCancelled):
        runner._run(task_id)

    assert ran == ["extract"]
    state = task_state._tasks[task_id]
    assert state["status"] == "interrupted"
    assert state["steps"]["extract"] == "done"
    assert state["steps"]["asr"] == "interrupted"
    assert state["steps"]["translate"] == "interrupted"

    cancelled_events = [e for e in captured if e.type == EVT_LAB_PIPELINE_ERROR]
    assert cancelled_events
    assert cancelled_events[-1].payload.get("cancelled") is True
