"""German pipeline SocketIO adapter — mirrors pipeline_runner.py for DeTranslateRunner."""
from __future__ import annotations

import threading

from appcore.events import EventBus
from appcore.runtime_de import DeTranslateRunner
from appcore.task_recovery import register_active_task, unregister_active_task
from web.extensions import socketio


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return handler


def _run_with_tracking(runner: DeTranslateRunner, task_id: str, start_step: str | None = None):
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
    runner = DeTranslateRunner(bus=bus, user_id=user_id)
    register_active_task(runner.project_type, task_id)
    thread = threading.Thread(target=_run_with_tracking, args=(runner, task_id), daemon=False)
    thread.start()


def resume(task_id: str, start_step: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = DeTranslateRunner(bus=bus, user_id=user_id)
    register_active_task(runner.project_type, task_id)
    thread = threading.Thread(target=_run_with_tracking, args=(runner, task_id, start_step), daemon=False)
    thread.start()


def run_analysis(task_id: str, user_id: int | None = None):
    """手动触发单次 AI 视频分析，不影响任务整体 status。"""
    from appcore.runtime import run_analysis_only

    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = DeTranslateRunner(bus=bus, user_id=user_id)
    thread = threading.Thread(
        target=run_analysis_only,
        args=(task_id, runner),
        daemon=False,
    )
    thread.start()
