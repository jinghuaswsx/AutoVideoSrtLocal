"""Process-level shutdown coordinator.

Single source of truth for "should this thread exit now?". Any thread can
query ``is_shutdown_requested()``; any signal handler / hook can flip it
via ``request_shutdown()``.

See docs/superpowers/specs/2026-05-01-graceful-shutdown-worker-lifecycle-design.md
for the full design.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

_shutdown_event = threading.Event()
_reason: Optional[str] = None
_state_lock = threading.Lock()


def is_shutdown_requested() -> bool:
    return _shutdown_event.is_set()


def request_shutdown(reason: str = "") -> None:
    """Trigger shutdown. Idempotent: first call wins, later calls only log."""
    global _reason
    with _state_lock:
        if _shutdown_event.is_set():
            log.info("[shutdown] re-request ignored (already=%s, new=%s)", _reason, reason)
            return
        _reason = reason or "unspecified"
        _shutdown_event.set()
        log.warning("[shutdown] requested: %s", _reason)


def reason() -> Optional[str]:
    return _reason


def wait(timeout: float) -> bool:
    """Block until shutdown is requested or timeout. Returns True if requested."""
    return _shutdown_event.wait(timeout)


def reset() -> None:
    """Test-only. Production code must not call this."""
    global _reason
    with _state_lock:
        _shutdown_event.clear()
        _reason = None


def wait_for_active_tasks(timeout: float, *, poll_interval: float = 0.5) -> int:
    """Wait until all active tasks unregister, or timeout.

    Returns the count still active at return time (0 = clean drain).

    Implementation note: do NOT hold ``task_recovery._active_lock`` for the
    full wait window; that would deadlock with ``unregister_active_task``.
    Take the lock only briefly per poll.
    """
    from appcore import task_recovery

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        with task_recovery._active_lock:
            count = len(task_recovery._active_tasks)
        if count == 0:
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return count
        time.sleep(min(poll_interval, remaining))
