from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

AdminEmitter = Callable[[str, dict[str, Any]], None]

_LOCK = threading.Lock()
_admin_emitter: AdminEmitter | None = None


def register_admin_emitter(emitter: AdminEmitter) -> None:
    with _LOCK:
        global _admin_emitter
        _admin_emitter = emitter


def clear_admin_emitter() -> None:
    with _LOCK:
        global _admin_emitter
        _admin_emitter = None


def emit_admin(event: str, payload: dict[str, Any]) -> bool:
    with _LOCK:
        emitter = _admin_emitter
    if emitter is None:
        return False
    try:
        emitter(event, payload)
        return True
    except Exception:
        log.warning("admin realtime emit failed: %s", event, exc_info=True)
        return False
