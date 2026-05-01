"""Gunicorn config for the AutoVideoSrt web service.

Single-process threaded mode is intentional here: the app uses in-process
Socket.IO rooms, in-memory task state and background schedulers, so scaling by
adding worker processes would require sticky sessions and a shared message
queue, which this deployment does not yet provide.
"""

from __future__ import annotations

import os


bind = "0.0.0.0:80"
worker_class = "gthread"
workers = 1
threads = 32
bind = os.getenv("AUTOVIDEOSRT_GUNICORN_BIND", bind)
threads = int(os.getenv("AUTOVIDEOSRT_GUNICORN_THREADS", str(threads)))
timeout = int(os.getenv("AUTOVIDEOSRT_GUNICORN_TIMEOUT", "300"))
# Web shutdown should not wait for long background jobs to finish in-process.
# Operators should run appcore.ops.active_tasks pre-restart before release; this
# timeout only gives ordinary requests and shutdown snapshots room to finish.
graceful_timeout = int(os.getenv("AUTOVIDEOSRT_GUNICORN_GRACEFUL_TIMEOUT", "45"))
keepalive = int(os.getenv("AUTOVIDEOSRT_GUNICORN_KEEPALIVE", "10"))
capture_output = True
accesslog = "-"
errorlog = "-"
proc_name = "autovideosrt-web"


def worker_exit(server, worker):
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
