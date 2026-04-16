"""图片翻译后台任务管理器（线程启动 + 重启恢复）。"""
from __future__ import annotations

import json
import threading

from appcore.db import query as db_query
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


def resume_inflight_tasks() -> list[str]:
    """服务重启时扫描未完成的 image_translate 任务并重新拉起。"""
    restored: list[str] = []
    try:
        rows = db_query(
            """
            SELECT id, user_id, status, state_json
            FROM projects
            WHERE type='image_translate'
              AND deleted_at IS NULL
              AND status IN ('queued','running')
            ORDER BY created_at ASC
            """,
            (),
        )
    except Exception:
        return restored

    for row in rows:
        tid = (row.get("id") or "").strip()
        if not tid or is_running(tid):
            continue
        state_json = row.get("state_json") or ""
        try:
            state = json.loads(state_json) if state_json else None
        except Exception:
            state = None
        if not state or state.get("type") != "image_translate":
            continue
        items = state.get("items") or []
        if items and all(it.get("status") in {"done", "failed"} for it in items):
            continue
        if start(tid, user_id=row.get("user_id")):
            restored.append(tid)
    return restored
