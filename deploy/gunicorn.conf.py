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
# 15 minutes — match systemd TimeoutStopSec so workers can finish in-flight
# pipeline batches before exit (long multi-translate tasks would otherwise
# get SIGKILL'd mid-batch on every service restart).
graceful_timeout = int(os.getenv("AUTOVIDEOSRT_GUNICORN_GRACEFUL_TIMEOUT", "900"))
keepalive = int(os.getenv("AUTOVIDEOSRT_GUNICORN_KEEPALIVE", "10"))
capture_output = True
accesslog = "-"
errorlog = "-"
proc_name = "autovideosrt-web"
