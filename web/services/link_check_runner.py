from __future__ import annotations

import threading

from appcore.link_check_runtime import LinkCheckRuntime
from appcore.runner_lifecycle import start_tracked_thread

_running: set[str] = set()
_lock = threading.Lock()


def start(task_id: str) -> bool:
    with _lock:
        if task_id in _running:
            return False
        _running.add(task_id)

    runtime = LinkCheckRuntime()

    def run() -> None:
        try:
            runtime.start(task_id)
        finally:
            with _lock:
                _running.discard(task_id)

    try:
        started = start_tracked_thread(
            project_type="link_check",
            task_id=task_id,
            target=run,
            daemon=True,
        )
    except BaseException:
        with _lock:
            _running.discard(task_id)
        raise
    if not started:
        with _lock:
            _running.discard(task_id)
    return started
