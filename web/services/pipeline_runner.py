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


def _project_type_for_task(task_id: str, fallback: str) -> str:
    task = task_state.get(task_id) or {}
    if str(task.get("pipeline_version") or "").strip() == "av":
        return "sentence_translate"
    task_type = str(task.get("type") or "").strip()
    return task_type or fallback


def _make_runner(task_id: str, user_id: int | None) -> PipelineRunner:
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    task = task_state.get(task_id) or {}
    if str(task.get("pipeline_version") or "").strip() == "av":
        from appcore.runtime_sentence_translate import SentenceTranslateRunner

        runner = SentenceTranslateRunner(bus=bus, user_id=user_id)
    else:
        runner = PipelineRunner(bus=bus, user_id=user_id)
    runner.project_type = _project_type_for_task(task_id, runner.project_type)
    return runner


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
    runner = _make_runner(task_id, user_id)
    register_active_task(runner.project_type, task_id)
    thread = threading.Thread(target=_run_with_tracking, args=(runner, task_id), daemon=True)
    thread.start()


def resume(task_id: str, start_step: str, user_id: int | None = None):
    runner = _make_runner(task_id, user_id)
    register_active_task(runner.project_type, task_id)
    thread = threading.Thread(target=_run_with_tracking, args=(runner, task_id, start_step), daemon=True)
    thread.start()


def run_analysis(task_id: str, user_id: int | None = None):
    """手动触发单次 AI 视频分析，不影响任务整体 status。"""
    from appcore.runtime import run_analysis_only

    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = PipelineRunner(bus=bus, user_id=user_id)
    thread = threading.Thread(
        target=run_analysis_only,
        args=(task_id, runner),
        daemon=True,
    )
    thread.start()
