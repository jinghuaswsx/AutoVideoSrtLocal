from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from appcore.cancellation import OperationCancelled
from appcore.task_recovery import try_register_active_task, unregister_active_task

log = logging.getLogger(__name__)


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
        except OperationCancelled as exc:
            # Cooperative cancellation is the *expected* exit path during
            # graceful shutdown. Treat it as a clean return so the worker
            # log does not show a stray traceback per cancelled task.
            log.warning(
                "[lifecycle] task cancelled (project=%s task=%s reason=%s)",
                project_type, task_id, exc,
            )
        finally:
            unregister_active_task(project_type, task_id)

    thread = threading.Thread(target=run, daemon=daemon)
    try:
        thread.start()
    except BaseException:
        unregister_active_task(project_type, task_id)
        raise
    return True
