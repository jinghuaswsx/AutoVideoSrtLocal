from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Any

from appcore.task_recovery import try_register_active_task, unregister_active_task


def _target_name(target: Callable[..., Any]) -> str:
    module = getattr(target, "__module__", "") or ""
    qualname = getattr(target, "__qualname__", "") or getattr(target, "__name__", "")
    if module and qualname:
        return f"{module}.{qualname}"
    return qualname or repr(target)


def start_tracked_thread(
    *,
    project_type: str,
    task_id: str,
    target: Callable[..., Any],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    daemon: bool = False,
    user_id: int | None = None,
    runner: str = "",
    entrypoint: str = "",
    stage: str = "",
    details: dict[str, Any] | None = None,
    interrupt_policy: str | None = None,
) -> bool:
    runner_name = runner or _target_name(target)
    if not try_register_active_task(
        project_type,
        task_id,
        user_id=user_id,
        runner=runner_name,
        entrypoint=entrypoint or runner_name,
        stage=stage,
        details={"daemon": daemon, **(details or {})},
        interrupt_policy=interrupt_policy,
    ):
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
