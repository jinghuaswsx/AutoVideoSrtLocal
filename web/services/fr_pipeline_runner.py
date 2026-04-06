"""French pipeline SocketIO adapter — mirrors pipeline_runner.py for FrTranslateRunner."""
from __future__ import annotations

import threading

from appcore.events import EventBus
from appcore.runtime_fr import FrTranslateRunner
from web.extensions import socketio


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return handler


def start(task_id: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = FrTranslateRunner(bus=bus, user_id=user_id)
    thread = threading.Thread(target=runner.start, args=(task_id,), daemon=True)
    thread.start()


def resume(task_id: str, start_step: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = FrTranslateRunner(bus=bus, user_id=user_id)
    thread = threading.Thread(target=runner.resume, args=(task_id, start_step), daemon=True)
    thread.start()
