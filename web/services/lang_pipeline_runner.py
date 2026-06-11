"""Shared pipeline SocketIO adapter for multi-language translation runners."""
from __future__ import annotations

from appcore.events import EventBus
from appcore.runner_lifecycle import start_tracked_thread
from appcore.runtime_de import DeTranslateRunner
from appcore.runtime_fr import FrTranslateRunner
from web.extensions import socketio


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return handler


def _make_runner(lang_code: str, bus: EventBus, user_id: int | None):
    if lang_code == "de":
        return DeTranslateRunner(bus=bus, user_id=user_id)
    elif lang_code == "fr":
        return FrTranslateRunner(bus=bus, user_id=user_id)
    else:
        raise ValueError(f"Unsupported language code: {lang_code}")


def _run_with_tracking(runner, task_id: str, start_step: str | None = None):
    if start_step is None:
        runner.start(task_id)
    else:
        runner.resume(task_id, start_step)


def start(lang_code: str, task_id: str, user_id: int | None = None) -> bool:
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = _make_runner(lang_code, bus, user_id)
    return start_tracked_thread(
        project_type=runner.project_type,
        task_id=task_id,
        target=_run_with_tracking,
        args=(runner, task_id),
        daemon=False,
    )


def resume(lang_code: str, task_id: str, start_step: str, user_id: int | None = None) -> bool:
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = _make_runner(lang_code, bus, user_id)
    return start_tracked_thread(
        project_type=runner.project_type,
        task_id=task_id,
        target=_run_with_tracking,
        args=(runner, task_id, start_step),
        daemon=False,
    )


def run_analysis(lang_code: str, task_id: str, user_id: int | None = None):
    """手动触发单次 AI 视频 analysis，不影响任务整体 status。"""
    from appcore.runtime import run_analysis_only

    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = _make_runner(lang_code, bus, user_id)
    return start_tracked_thread(
        project_type=runner.project_type,
        task_id=task_id,
        target=run_analysis_only,
        args=(task_id, runner),
        daemon=False,
        user_id=user_id,
        runner="appcore.runtime.run_analysis_only",
        entrypoint=f"web.services.{lang_code}_pipeline_runner.run_analysis",
        stage="analysis",
        details={"action": "manual_analysis"},
    )
