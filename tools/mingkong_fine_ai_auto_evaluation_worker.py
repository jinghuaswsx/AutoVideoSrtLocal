from __future__ import annotations

import argparse
import fcntl
import logging
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore import mingkong_fine_ai_auto_evaluation as worker

LOCK_PATH = Path("/tmp/autovideosrt-mingkong-fine-ai-worker.lock")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mingkong Fine AI evaluation worker pool.")
    parser.add_argument("--workers", type=int, default=worker.DEFAULT_WORKER_CONCURRENCY)
    parser.add_argument("--idle-sleep-seconds", type=float, default=worker.WORKER_IDLE_SLEEP_SECONDS)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop_event = threading.Event()

    def _stop(signum, frame):
        logging.getLogger(__name__).warning("stop signal received: %s", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.getLogger(__name__).warning("worker lock is held; another worker is already running")
            return 0
        lock_file.write(str(Path.cwd()))
        lock_file.flush()
        summary = worker.run_worker_pool(
            max_workers=args.workers,
            idle_sleep_seconds=args.idle_sleep_seconds,
            stop_event=stop_event,
        )
        logging.getLogger(__name__).info("worker stopped: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
