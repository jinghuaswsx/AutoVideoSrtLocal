from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO


DEFAULT_LINUX_LOCK_PATH = "/data/autovideosrt/browser/runtime/automation.lock"
DEFAULT_WINDOWS_LOCK_PATH = Path("output") / "browser_automation" / "automation.lock"


class BrowserAutomationLockTimeout(TimeoutError):
    """Raised when the shared server browser is busy for too long."""


def default_lock_path() -> Path:
    configured = os.environ.get("BROWSER_AUTOMATION_LOCK_PATH")
    if configured:
        return Path(configured)
    if os.name == "nt":
        return Path.cwd() / DEFAULT_WINDOWS_LOCK_PATH
    return Path(DEFAULT_LINUX_LOCK_PATH)


def _acquire_nonblocking(handle: TextIO) -> bool:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise


def _release(handle: TextIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def browser_automation_lock(
    *,
    task_code: str,
    timeout_seconds: int = 600,
    retry_seconds: int = 10,
    command: str | None = None,
    lock_path: str | os.PathLike[str] | None = None,
) -> Iterator[Path]:
    path = Path(lock_path) if lock_path is not None else default_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    timeout_seconds = max(0, int(timeout_seconds))
    retry_seconds = max(1, int(retry_seconds))
    deadline = time.monotonic() + timeout_seconds
    label = f"{task_code}: {command}" if command else task_code

    with path.open("a+", encoding="utf-8") as handle:
        if os.name == "nt":
            handle.seek(0)
            handle.write("\0")
            handle.flush()
            handle.seek(0)

        while True:
            if _acquire_nonblocking(handle):
                break
            if time.monotonic() >= deadline:
                raise BrowserAutomationLockTimeout(
                    f"browser automation lock timeout after {timeout_seconds}s: {path} ({label})"
                )
            time.sleep(min(retry_seconds, max(0.0, deadline - time.monotonic())))

        try:
            yield path
        finally:
            _release(handle)
