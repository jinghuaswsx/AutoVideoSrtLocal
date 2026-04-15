"""
Pipeline execution service — thin SocketIO adapter over appcore.runtime.

All business logic lives in appcore.runtime.PipelineRunner.
This module only bridges EventBus events to Socket.IO rooms.
"""

from __future__ import annotations

import threading

import appcore.task_state as task_state
from appcore.events import EventBus
from appcore.runtime import PipelineRunner
from appcore.task_recovery import register_active_task, unregister_active_task
from web.extensions import socketio


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return handler


def _run_with_tracking(runner: PipelineRunner, task_id: str, start_step: str | None = None):
    register_active_task(runner.project_type, task_id)
    try:
        if start_step is None:
            runner.start(task_id)
        else:
            runner.resume(task_id, start_step)
    finally:
        unregister_active_task(runner.project_type, task_id)


def start(task_id: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = PipelineRunner(bus=bus, user_id=user_id)
    register_active_task(runner.project_type, task_id)
    thread = threading.Thread(target=_run_with_tracking, args=(runner, task_id), daemon=True)
    thread.start()


def resume(task_id: str, start_step: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = PipelineRunner(bus=bus, user_id=user_id)
    register_active_task(runner.project_type, task_id)
    thread = threading.Thread(target=_run_with_tracking, args=(runner, task_id, start_step), daemon=True)
    thread.start()
