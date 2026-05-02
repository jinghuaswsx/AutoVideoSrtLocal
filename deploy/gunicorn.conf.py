"""Gunicorn config for the AutoVideoSrt web service.

Single-process threaded mode is intentional: the app uses in-process
Socket.IO rooms, in-memory task state and background schedulers, so
scaling by adding worker processes would require sticky sessions and a
shared message queue, which this deployment does not yet provide.

Graceful-shutdown plumbing (see
docs/superpowers/specs/2026-05-01-graceful-shutdown-worker-lifecycle-design.md):
- post_worker_init chains a SIGTERM / SIGINT handler that flips the
  process-level shutdown coordinator and stops APScheduler before
  deferring to Gunicorn's own handler.
- worker_exit snapshots active tasks, triggers the coordinator one more
  time, stops APScheduler as a fallback, and waits up to 200s for in-flight
  tracked threads to honour the cooperative cancellation points and
  unregister.
- graceful_timeout shrunk from 900s to 240s now that the signal chain
  exists -- 15 min was needed because nothing was telling the threads
  to leave; with the chain a clean exit takes 30-60s.
"""

from __future__ import annotations

import logging
import os
import signal


_log = logging.getLogger("gunicorn.error")


bind = "0.0.0.0:80"
worker_class = "gthread"
workers = 1
threads = 32
bind = os.getenv("AUTOVIDEOSRT_GUNICORN_BIND", bind)
threads = int(os.getenv("AUTOVIDEOSRT_GUNICORN_THREADS", str(threads)))
timeout = int(os.getenv("AUTOVIDEOSRT_GUNICORN_TIMEOUT", "300"))
# 240s = 200s drain window for tracked threads + 40s buffer for HTTP
# request shutdown + APScheduler teardown. Paired with systemd
# TimeoutStopSec=300, leaving a 60s systemd buffer.
graceful_timeout = int(os.getenv("AUTOVIDEOSRT_GUNICORN_GRACEFUL_TIMEOUT", "240"))
keepalive = int(os.getenv("AUTOVIDEOSRT_GUNICORN_KEEPALIVE", "10"))
capture_output = True
accesslog = "-"
errorlog = "-"
proc_name = "autovideosrt-web"


def _snapshot_shutdown_active_tasks(worker) -> None:
    try:
        from appcore.active_tasks import list_active_tasks, snapshot_active_tasks

        tasks = list_active_tasks()
        result = snapshot_active_tasks("shutdown_signal", tasks=tasks)
        worker.log.info("active task shutdown snapshot: %s", result)
        for task in tasks:
            worker.log.info(
                "active unfinished task: %s:%s policy=%s stage=%s runner=%s",
                task.project_type,
                task.task_id,
                task.interrupt_policy,
                task.stage or "-",
                task.runner or "-",
            )
    except Exception as exc:
        worker.log.warning("active task shutdown snapshot failed: %s", exc)


def _request_shutdown_and_stop_scheduler(reason: str, context: str) -> None:
    try:
        from appcore.shutdown_coordinator import request_shutdown

        request_shutdown(reason)
    except Exception:  # pragma: no cover
        _log.exception("%s: request_shutdown failed", context)
    try:
        from appcore.scheduler import shutdown_scheduler

        shutdown_scheduler(wait=False)
    except Exception:  # pragma: no cover
        _log.exception("%s: shutdown_scheduler failed", context)


def post_worker_init(worker):
    """Chain SIGTERM / SIGINT into the shutdown coordinator.

    Gunicorn already installs handlers for these signals (set
    worker.alive = False and stop accepting new connections). We want
    both: flip the shutdown coordinator first so all in-process
    tracked threads can react via throw_if_cancel_requested, stop
    APScheduler so no new scheduled work starts during drain, then
    defer to the original handler so Gunicorn does its own bookkeeping.
    """
    original_term = signal.getsignal(signal.SIGTERM)
    original_int = signal.getsignal(signal.SIGINT)

    def _term(signum, frame):
        _request_shutdown_and_stop_scheduler(f"signal={signum}", "post_worker_init: SIGTERM")
        if callable(original_term):
            try:
                original_term(signum, frame)
            except Exception:  # pragma: no cover
                _log.exception("post_worker_init: original SIGTERM handler raised")

    def _int(signum, frame):
        _request_shutdown_and_stop_scheduler(f"signal={signum}", "post_worker_init: SIGINT")
        if callable(original_int):
            try:
                original_int(signum, frame)
            except Exception:  # pragma: no cover
                _log.exception("post_worker_init: original SIGINT handler raised")

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _int)
    _log.info("[shutdown] post_worker_init: signal handlers chained")


def worker_exit(server, worker):
    """Run before Gunicorn waits the worker out.

    1. Set the shutdown coordinator (covers SIGKILL-from-master paths
       that bypass our post_worker_init handler).
    2. Shut down APScheduler so its non-daemon thread does not block
       process exit.
    3. Wait up to 200s for tracked threads to drain. Threads that miss
       the window will be SIGKILLed by systemd a moment later.
    """
    try:
        from appcore.shutdown_coordinator import (
            request_shutdown,
            wait_for_active_tasks,
        )
        from appcore.scheduler import shutdown_scheduler

        request_shutdown("worker_exit")
        _snapshot_shutdown_active_tasks(worker)
        try:
            shutdown_scheduler(wait=False)
        except Exception:  # pragma: no cover
            _log.exception("worker_exit: shutdown_scheduler raised")
        remaining = wait_for_active_tasks(timeout=200)
        if remaining:
            _log.warning(
                "[shutdown] worker_exit: %s tracked task(s) still active after drain window",
                remaining,
            )
        else:
            _log.info("[shutdown] worker_exit: all tracked tasks drained")
    except Exception:  # pragma: no cover
        _log.exception("worker_exit: unexpected failure")
