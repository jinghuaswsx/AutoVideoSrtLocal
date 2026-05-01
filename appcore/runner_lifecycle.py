from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Any

from appcore.task_recovery import try_register_active_task, unregister_active_task


def start_tracked_thread(
    *,
    project_type: str,
    task_id: str,
    target: Callable[..., Any],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    daemon: bool = False,
) -> bool:
    if not try_register_active_task(project_type, task_id):
        return False

    call_kwargs = kwargs or {}

    def run() -> None:
        try:
            target(*args, **call_kwargs)
        finally:
            unregister_active_task(project_type, task_id)

    thread = threading.Thread(target=run, daemon=daemon)
    try:
        thread.start()
    except BaseException:
        unregister_active_task(project_type, task_id)
        raise
    return True
