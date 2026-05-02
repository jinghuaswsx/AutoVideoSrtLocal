"""Cancellation protocol for web background tasks.

Same name but unrelated to ``tools/shopify_image_localizer/cancellation.py``
(internal to the Shopify Localizer tool). The two modules do not import
each other.

Contract: long-running runners call ``throw_if_cancel_requested()`` at the
top of each step / inner loop, and replace any ``time.sleep(...)`` with
``cancellable_sleep(...)``.
"""
from __future__ import annotations

from appcore import shutdown_coordinator


class OperationCancelled(RuntimeError):
    """Raised when a long task voluntarily exits because shutdown was requested.

    Top-level runners should catch this, mark the task as ``interrupted``,
    and return cleanly. Do not re-raise: that would leave an unhandled
    traceback in the worker log.
    """


def throw_if_cancel_requested(reason: str = "") -> None:
    """Raise OperationCancelled iff shutdown was requested. No-op otherwise."""
    if shutdown_coordinator.is_shutdown_requested():
        msg = reason or shutdown_coordinator.reason() or "shutdown requested"
        raise OperationCancelled(msg)


def cancellable_sleep(seconds: float) -> None:
    """Cancellable replacement for ``time.sleep``.

    Wakes early and raises OperationCancelled if shutdown was requested.
    """
    if seconds <= 0:
        throw_if_cancel_requested()
        return
    if shutdown_coordinator.wait(seconds):
        throw_if_cancel_requested()
