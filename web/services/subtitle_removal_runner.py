from __future__ import annotations

import threading

from appcore.events import EventBus
from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime
from web.extensions import socketio


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)

    return handler


def start(task_id: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runtime = SubtitleRemovalRuntime(bus=bus, user_id=user_id)
    thread = threading.Thread(target=runtime.start, args=(task_id,), daemon=True)
    thread.start()
