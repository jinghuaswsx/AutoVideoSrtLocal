from __future__ import annotations

import threading

import config
from appcore.events import EventBus
from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime
from appcore.subtitle_removal_runtime_vod import SubtitleRemovalVodRuntime
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


def start(task_id: str, user_id: int | None = None):
    with _running_tasks_lock:
        if task_id in _running_tasks:
            return False
        _running_tasks.add(task_id)

    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    provider = (getattr(config, "SUBTITLE_REMOVAL_PROVIDER", "goodline") or "goodline").strip().lower()
    if provider == "vod":
        runtime = SubtitleRemovalVodRuntime(bus=bus, user_id=user_id)
    else:
        runtime = SubtitleRemovalRuntime(bus=bus, user_id=user_id)
    def run():
        try:
            runtime.start(task_id)
        finally:
            with _running_tasks_lock:
                _running_tasks.discard(task_id)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return True
