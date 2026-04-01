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
from web.extensions import socketio


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return handler


def start(task_id: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = PipelineRunner(bus=bus)
    thread = threading.Thread(target=runner.start, args=(task_id,), daemon=True)
    thread.start()


def resume(task_id: str, start_step: str):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = PipelineRunner(bus=bus)
    thread = threading.Thread(target=runner.resume, args=(task_id, start_step), daemon=True)
    thread.start()
