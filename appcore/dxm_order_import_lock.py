"""Dianxiaomi order-import browser lock.

Docs-anchor:
docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md#店小秘订单导入细节
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from appcore.browser_automation_lock import browser_automation_lock

DEFAULT_LINUX_LOCK_PATH = Path("/data/autovideosrt/browser/runtime-dxm-order-import/automation.lock")
DEFAULT_LOCAL_LOCK_PATH = Path("output") / "browser_automation" / "dxm_order_import.lock"
ROI_LOCK_TIMEOUT_SECONDS = 60
BACKFILL_LOCK_TIMEOUT_SECONDS = 600
LOCK_RETRY_SECONDS = 5


def default_dxm_order_import_lock_path() -> Path:
    configured = os.environ.get("DXM_ORDER_IMPORT_LOCK_PATH")
    if configured:
        return Path(configured)
    if os.name != "nt" and (DEFAULT_LINUX_LOCK_PATH.parent.exists() or os.path.exists("/data/autovideosrt")):
        return DEFAULT_LINUX_LOCK_PATH
    return Path.cwd() / DEFAULT_LOCAL_LOCK_PATH


def read_lock_holder(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def lock_timeout_summary(
    path: str | os.PathLike[str],
    *,
    timeout_seconds: int,
    error_message: str,
) -> dict[str, Any]:
    holder = read_lock_holder(path)
    return {
        "status": "skipped_lock_timeout",
        "lock_path": str(path),
        "timeout_seconds": int(timeout_seconds),
        "holder_pid": holder.get("pid"),
        "holder_command": holder.get("command"),
        "error": error_message,
    }


@contextmanager
def dxm_order_import_lock(
    *,
    task_code: str,
    timeout_seconds: int,
    command: str,
    lock_path: str | os.PathLike[str] | None = None,
) -> Iterator[Path]:
    path = Path(lock_path) if lock_path is not None else default_dxm_order_import_lock_path()
    with browser_automation_lock(
        task_code=task_code,
        timeout_seconds=timeout_seconds,
        retry_seconds=LOCK_RETRY_SECONDS,
        command=command,
        lock_path=path,
    ) as acquired_path:
        yield acquired_path
