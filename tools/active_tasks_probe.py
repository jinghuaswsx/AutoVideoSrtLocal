"""CLI wrapper for ``GET /admin/runtime/active-tasks``.

Intended for ops use before ``systemctl restart``: prints a snapshot of
in-flight background tasks and exits non-zero if anything is active so
``deploy/publish.sh`` (or a manual shell session) can fail-fast.

Usage:

    AUTOVIDEOSRT_PROBE_URL=http://127.0.0.1/admin/runtime/active-tasks \\
    AUTOVIDEOSRT_ADMIN_COOKIE='session=...; remember_token=...' \\
    python -m tools.active_tasks_probe

Exit codes:
  0 -- probe succeeded and no active tasks
  1 -- probe succeeded but at least one active task
  2 -- probe failed (HTTP error / network error / non-JSON response)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1/admin/runtime/active-tasks"


def fetch(url: str, cookie: str, *, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url)
    if cookie:
        req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def main(argv: list[str] | None = None) -> int:
    url = os.environ.get("AUTOVIDEOSRT_PROBE_URL", DEFAULT_URL)
    cookie = os.environ.get("AUTOVIDEOSRT_ADMIN_COOKIE", "")
    try:
        payload = fetch(url, cookie)
    except urllib.error.HTTPError as exc:
        print(f"probe HTTP error: {exc.code} {exc.reason}", file=sys.stderr)
        return 2
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        print(f"probe error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if int(payload.get("active_count") or 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
