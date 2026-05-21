"""JapaneseTranslateRunner 的 SocketIO 适配层。"""
from __future__ import annotations

from appcore import runner_dispatch
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
    from appcore.runtime_ja import JapaneseTranslateRunner

    bus = EventBus()
    bus.subscribe(_handler(task_id))
    runner = JapaneseTranslateRunner(bus=bus, user_id=user_id)
    return start_tracked_thread(
        project_type=runner.project_type,
        task_id=task_id,
        target=_run,
        args=(runner, task_id),
        daemon=False,
    )


def resume(task_id: str, start_step: str, user_id: int | None = None) -> bool:
    from appcore.runtime_ja import JapaneseTranslateRunner

    bus = EventBus()
    bus.subscribe(_handler(task_id))
    runner = JapaneseTranslateRunner(bus=bus, user_id=user_id)
    return start_tracked_thread(
        project_type=runner.project_type,
        task_id=task_id,
        target=_run,
        args=(runner, task_id, start_step),
        daemon=False,
    )


runner_dispatch.register_ja_translate_runner(
    start=lambda task_id, user_id=None: start(task_id, user_id=user_id),
    resume=lambda task_id, start_step, user_id=None: resume(
        task_id,
        start_step,
        user_id=user_id,
    ),
)
