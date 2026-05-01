"""OmniTranslateRunner SocketIO adapter (mirrors multi_pipeline_runner)."""
from __future__ import annotations

from appcore.events import EventBus
from appcore.runner_lifecycle import start_tracked_thread
from web.extensions import socketio


def _handler(task_id: str):
    def fn(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return fn


def _run(runner, task_id: str, start_step: str | None = None):
    if start_step is None:
        runner.start(task_id)
    else:
        runner.resume(task_id, start_step)


def start(task_id: str, user_id: int | None = None) -> bool:
    from appcore.runtime_omni import OmniTranslateRunner
    bus = EventBus()
    bus.subscribe(_handler(task_id))
    runner = OmniTranslateRunner(bus=bus, user_id=user_id)
    return start_tracked_thread(
        project_type=runner.project_type,
        task_id=task_id,
        target=_run,
        args=(runner, task_id),
        daemon=False,
    )


def resume(task_id: str, start_step: str, user_id: int | None = None) -> bool:
    from appcore.runtime_omni import OmniTranslateRunner
    bus = EventBus()
    bus.subscribe(_handler(task_id))
    runner = OmniTranslateRunner(bus=bus, user_id=user_id)
    return start_tracked_thread(
        project_type=runner.project_type,
        task_id=task_id,
        target=_run,
        args=(runner, task_id, start_step),
        daemon=False,
    )
