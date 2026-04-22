"""Gunicorn config for the AutoVideoSrt web service.

Single-process threaded mode is intentional here: the app uses in-process
Socket.IO rooms, in-memory task state and background schedulers, so scaling by
adding worker processes would require sticky sessions and a shared message
queue, which this deployment does not yet provide.
"""

from __future__ import annotations

import os


bind = os.getenv("AUTOVIDEOSRT_GUNICORN_BIND", "0.0.0.0:8888")
worker_class = "gthread"
workers = 1
threads = 32
timeout = int(os.getenv("AUTOVIDEOSRT_GUNICORN_TIMEOUT", "300"))
graceful_timeout = int(os.getenv("AUTOVIDEOSRT_GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("AUTOVIDEOSRT_GUNICORN_KEEPALIVE", "10"))
capture_output = True
accesslog = "-"
errorlog = "-"
proc_name = "autovideosrt-web"
