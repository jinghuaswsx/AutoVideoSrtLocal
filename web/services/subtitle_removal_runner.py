from __future__ import annotations

from appcore.subtitle_removal_runtime import (
    start_subtitle_removal_task,
    is_subtitle_removal_task_running,
)
from web.extensions import socketio


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)

    return handler


def is_running(task_id: str) -> bool:
    return is_subtitle_removal_task_running(task_id)


def start(task_id: str, user_id: int | None = None) -> bool:
    return start_subtitle_removal_task(
        task_id,
        user_id,
        on_event=_make_socketio_handler(task_id),
    )

