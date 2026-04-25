from __future__ import annotations

import threading


class OperationCancelled(RuntimeError):
    """Raised when the user requests the current replacement task to stop."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def throw_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise OperationCancelled("用户已停止任务")

    def wait(self, seconds: float) -> None:
        if seconds <= 0:
            self.throw_if_cancelled()
            return
        if self._event.wait(seconds):
            self.throw_if_cancelled()


def throw_if_cancelled(token: CancellationToken | None) -> None:
    if token is not None:
        token.throw_if_cancelled()


def cancellable_sleep(token: CancellationToken | None, seconds: float) -> None:
    if token is None:
        import time

        time.sleep(seconds)
        return
    token.wait(seconds)
