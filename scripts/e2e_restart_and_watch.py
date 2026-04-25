"""Restart an existing task and watch convergence.

Usage:
    python scripts/e2e_restart_and_watch.py <task_id>
"""
from __future__ import annotations

import json
import re
import sys
import time

import requests

BASE = "http://172.30.254.14:8080"
USERNAME = "admin"
PASSWORD = "709709@"
POLL_INTERVAL = 8
POLL_MAX_MIN = 25


def login(s):
    r = s.get(f"{BASE}/login", timeout=15)
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text).group(1)
    r = s.post(f"{BASE}/login", data={"csrf_token": csrf, "username": USERNAME, "password": PASSWORD}, allow_redirects=False, timeout=15)
    assert r.status_code == 302, f"login failed: {r.status_code}"
    print(f"[login] OK")


def restart(s, tid):
    r = s.post(f"{BASE}/api/multi-translate/{tid}/restart", json={}, timeout=30)
    print(f"[restart] HTTP {r.status_code}: {r.text[:200]}")
    return r.status_code in (200, 201, 202)


def poll(s, tid):
    print(f"[poll] watching {tid}")
    start = time.time()
    last = ""
    while time.time() - start < POLL_MAX_MIN * 60:
        r = s.get(f"{BASE}/api/multi-translate/{tid}", timeout=15)
        if r.status_code != 200:
            time.sleep(POLL_INTERVAL)
            continue
        data = r.json()
        task = data.get("task") or data
        steps = task.get("steps") or {}
        summary = " ".join(f"{k}:{v}" for k, v in steps.items())
        if summary != last:
            elapsed = int(time.time() - start)
            print(f"  [{elapsed:>4}s] {summary}")
            last = summary
        if any(v == "error" for v in steps.values()):
            print("[poll] error detected")
            return task
        if all(v == "done" for v in steps.values()) and len(steps) >= 7:
            print("[poll] all done")
            return task
        time.sleep(POLL_INTERVAL)
    raise TimeoutError("timeout")


def report(task):
    print("\n=== KPI ===")
    print(f"source_language = {task.get('source_language')!r}")
    rounds = task.get("tts_duration_rounds") or []
    print(f"tts_duration_rounds count = {len(rounds)}")
    if rounds:
        r0 = rounds[0]
        print(f"  round[0].max_rewrite_attempts = {r0.get('max_rewrite_attempts')} (expect 7 for de)")
        print(f"  round[0].word_tolerance = {r0.get('word_tolerance')} (expect 0.15 for de)")


def main():
    if len(sys.argv) < 2:
        print("usage: python scripts/e2e_restart_and_watch.py <task_id>")
        sys.exit(2)
    tid = sys.argv[1]
    s = requests.Session()
    login(s)
    if not restart(s, tid):
        print("restart failed")
        sys.exit(1)
    final = poll(s, tid)
    print(f"\nfinal status={final.get('status')}, error={final.get('error')!r}")
    report(final)
    open("/tmp/e2e_final.json", "w", encoding="utf-8").write(json.dumps(final, ensure_ascii=False, indent=2))
    print("saved /tmp/e2e_final.json")


if __name__ == "__main__":
    main()
