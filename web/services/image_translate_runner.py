"""图片翻译后台任务管理器（仅管理内存中的运行线程）。

启动期的「中断恢复」由 appcore.task_recovery.recover_all_interrupted_tasks()
统一处理：重启后 DB 里 queued/running 的任务被标为 interrupted，由用户在前端
手动点「重新生成」再次入队。本模块不再承担 resume 逻辑。
"""
from __future__ import annotations

import threading

from appcore.events import EventBus
from appcore.image_translate_runtime import ImageTranslateRuntime
from web.extensions import socketio

_running_tasks: set[str] = set()
_running_tasks_lock = threading.Lock()


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return handler


def is_running(task_id: str) -> bool:
    with _running_tasks_lock:
        return task_id in _running_tasks


def start(task_id: str, user_id: int | None = None) -> bool:
    with _running_tasks_lock:
        if task_id in _running_tasks:
            return False
        _running_tasks.add(task_id)

    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runtime = ImageTranslateRuntime(bus=bus, user_id=user_id)

    def run():
        try:
            runtime.start(task_id)
        finally:
            with _running_tasks_lock:
                _running_tasks.discard(task_id)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return True
