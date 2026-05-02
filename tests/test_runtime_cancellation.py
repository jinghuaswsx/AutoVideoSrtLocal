"""Verify cooperative cancellation in ``PipelineRunner._run``.

A FakeRunner subclasses PipelineRunner, replaces
``_get_pipeline_steps`` with an in-memory list of step lambdas, and
drives it through task_state directly. No DB, no SocketIO -- the goal
is to assert that the cancellation checkpoint added before each step
does fire on ``shutdown_coordinator.request_shutdown``, that the task
is marked ``interrupted`` (not ``error``), and that PIPELINE_ERROR
events carry ``cancelled=True``.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    from appcore import shutdown_coordinator, task_state

    shutdown_coordinator.reset()
    task_state._tasks.clear()
    monkeypatch.setattr("appcore.db.execute", lambda *a, **k: None)
    monkeypatch.setattr(
        "appcore.source_video.ensure_local_source_video",
        lambda task_id: None,
    )
    yield
    shutdown_coordinator.reset()
    task_state._tasks.clear()


def _seed_task(task_id: str, *, step_names: list[str]) -> None:
    from appcore import task_state

    task_state._tasks[task_id] = {
        "id": task_id,
        "video_path": "/tmp/dummy.mp4",
        "task_dir": "/tmp/dummy",
        "steps": {name: "pending" for name in step_names},
        "step_messages": {name: "" for name in step_names},
        "status": "uploaded",
    }


def _build_runner(steps_with_callbacks):
    """Create a PipelineRunner subclass driven by the given step list.

    ``steps_with_callbacks`` is a list of (step_name, callable). Each
    callback runs as the step body; callbacks may flip task_state to
    simulate ``done``/``failed``/``waiting``.
    """
    from appcore.events import EventBus
    from appcore.runtime import PipelineRunner

    captured: list = []
    bus = EventBus()
    bus.subscribe(lambda event: captured.append(event))

    class _FakeRunner(PipelineRunner):
        project_type = "translation"

        def _get_pipeline_steps(self, task_id, video_path, task_dir):
            return list(steps_with_callbacks)

    runner = _FakeRunner(bus=bus)
    return runner, captured


def test_no_cancellation_runs_all_steps():
    from appcore import task_state

    task_id = "no-cancel"
    _seed_task(task_id, step_names=["extract", "asr"])
    ran: list[str] = []

    def step_extract():
        ran.append("extract")
        task_state.set_step(task_id, "extract", "done")

    def step_asr():
        ran.append("asr")
        task_state.set_step(task_id, "asr", "done")

    runner, _ = _build_runner([
        ("extract", step_extract),
        ("asr", step_asr),
    ])
    runner._run(task_id)

    assert ran == ["extract", "asr"]
    assert task_state._tasks[task_id]["steps"]["extract"] == "done"
    assert task_state._tasks[task_id]["steps"]["asr"] == "done"


def test_shutdown_mid_pipeline_marks_interrupted_and_skips_remaining():
    from appcore import cancellation, shutdown_coordinator, task_state
    from appcore.events import EVT_PIPELINE_ERROR

    task_id = "cancel-mid"
    _seed_task(task_id, step_names=["extract", "asr"])

    ran: list[str] = []

    def step_extract():
        ran.append("extract")
        # mark running so _mark_pipeline_interrupted has something to flip
        task_state.set_step(task_id, "extract", "done")
        # Now simulate the SIGTERM path: signal handler flips the flag
        # mid-pipeline.
        shutdown_coordinator.request_shutdown("signal=SIGTERM")

    def step_asr():
        ran.append("asr")  # must NOT run

    runner, captured = _build_runner([
        ("extract", step_extract),
        ("asr", step_asr),
    ])

    with pytest.raises(cancellation.OperationCancelled):
        runner._run(task_id)

    assert ran == ["extract"]
    state = task_state._tasks[task_id]
    assert state["status"] == "interrupted"
    assert state["steps"]["asr"] == "interrupted"
    # ``extract`` was already done before cancel -- must remain done
    assert state["steps"]["extract"] == "done"

    cancelled_events = [e for e in captured if e.type == EVT_PIPELINE_ERROR]
    assert cancelled_events, "expected a PIPELINE_ERROR event with cancelled=True"
    assert cancelled_events[-1].payload.get("cancelled") is True


def test_shutdown_before_first_step_skips_everything():
    from appcore import cancellation, shutdown_coordinator, task_state

    task_id = "cancel-before"
    _seed_task(task_id, step_names=["extract", "asr"])
    shutdown_coordinator.request_shutdown("pre-pipeline")

    ran: list[str] = []

    runner, _ = _build_runner([
        ("extract", lambda: ran.append("extract")),
        ("asr", lambda: ran.append("asr")),
    ])

    with pytest.raises(cancellation.OperationCancelled):
        runner._run(task_id)

    assert ran == []
    state = task_state._tasks[task_id]
    assert state["status"] == "interrupted"


def test_unrelated_exception_marks_error_not_interrupted():
    """Sanity: a normal exception still goes through the existing
    error path, never the cancellation path."""
    from appcore import task_state

    task_id = "boom"
    _seed_task(task_id, step_names=["extract"])

    def step_extract():
        raise RuntimeError("kaboom")

    runner, _ = _build_runner([("extract", step_extract)])
    runner._run(task_id)

    state = task_state._tasks[task_id]
    assert state["status"] == "error"
    assert "kaboom" in state.get("error", "")
