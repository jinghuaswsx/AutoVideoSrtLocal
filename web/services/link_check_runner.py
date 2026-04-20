from __future__ import annotations

import threading

from appcore.link_check_runtime import LinkCheckRuntime
from appcore.task_recovery import register_active_task, unregister_active_task

_running: set[str] = set()
_lock = threading.Lock()


def start(task_id: str) -> bool:
    with _lock:
        if task_id in _running:
            return False
        _running.add(task_id)

    runtime = LinkCheckRuntime()
    register_active_task("link_check", task_id)

    def run() -> None:
        try:
            runtime.start(task_id)
        finally:
            unregister_active_task("link_check", task_id)
            with _lock:
                _running.discard(task_id)

    threading.Thread(target=run, daemon=True).start()
    return True
