"""OmniTranslateRunner SocketIO adapter (mirrors multi_pipeline_runner)."""
from __future__ import annotations

import threading

from appcore.events import EventBus
from appcore.task_recovery import register_active_task, unregister_active_task
from web.extensions import socketio


def _handler(task_id: str):
    def fn(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return fn


def _run(runner, task_id: str, start_step: str | None = None):
    register_active_task(runner.project_type, task_id)
    try:
        if start_step is None:
            runner.start(task_id)
        else:
            runner.resume(task_id, start_step)
    finally:
        unregister_active_task(runner.project_type, task_id)


def start(task_id: str, user_id: int | None = None):
    from appcore.runtime_omni import OmniTranslateRunner
    bus = EventBus()
    bus.subscribe(_handler(task_id))
    runner = OmniTranslateRunner(bus=bus, user_id=user_id)
    threading.Thread(target=_run, args=(runner, task_id), daemon=False).start()


def resume(task_id: str, start_step: str, user_id: int | None = None):
    from appcore.runtime_omni import OmniTranslateRunner
    bus = EventBus()
    bus.subscribe(_handler(task_id))
    runner = OmniTranslateRunner(bus=bus, user_id=user_id)
    threading.Thread(target=_run, args=(runner, task_id, start_step), daemon=False).start()
