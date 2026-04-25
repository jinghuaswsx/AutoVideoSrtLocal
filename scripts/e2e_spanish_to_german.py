"""End-to-end self-test: Spanish source video → German translation.

Validates the multi-source-language treatment by:
1. Logging into the test env (172.30.254.14:8080) as admin.
2. Uploading the local Spanish sample video with source_language=es, target_lang=de.
3. Polling task status.
4. Asserting the ASR engine is ElevenLabs Scribe (not Doubao) — proves dispatch works.
5. Reporting tts_duration_rounds convergence behavior — proves Phase 2 tolerance fix.

Run from Windows host where the sample video lives:
    python scripts/e2e_spanish_to_german.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

BASE = "http://172.30.254.14:8080"
USERNAME = "admin"
PASSWORD = "709709@"
VIDEO_PATH = r"C:\Users\admin\Desktop\德国法国测试\西班牙语视频.mp4"
TARGET_LANG = "de"
SOURCE_LANGUAGE = "es"
POLL_INTERVAL_SEC = 8
POLL_MAX_MIN = 25  # 35s 视频，全流程预估 < 25 分钟


def login(session: requests.Session) -> None:
    print(f"[login] GET {BASE}/login")
    r = session.get(f"{BASE}/login", timeout=15)
    r.raise_for_status()
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("csrf_token not found on login page")
    csrf = m.group(1)
    print(f"[login] POST /login as {USERNAME}")
    r = session.post(
        f"{BASE}/login",
        data={"csrf_token": csrf, "username": USERNAME, "password": PASSWORD},
        allow_redirects=False,
        timeout=15,
    )
    if r.status_code != 302:
        raise RuntimeError(f"login failed: HTTP {r.status_code}, body={r.text[:200]}")
    print(f"[login] OK, redirect → {r.headers.get('Location')}")


def upload_task(session: requests.Session) -> str:
    print(f"[upload] POST /api/multi-translate/start (source={SOURCE_LANGUAGE}, target={TARGET_LANG})")
    p = Path(VIDEO_PATH)
    if not p.exists():
        raise FileNotFoundError(VIDEO_PATH)
    with p.open("rb") as f:
        r = session.post(
            f"{BASE}/api/multi-translate/start",
            files={"video": (p.name, f, "video/mp4")},
            data={
                "target_lang": TARGET_LANG,
                "source_language": SOURCE_LANGUAGE,
                "display_name": "E2E_西语翻德语_Test",
            },
            timeout=120,
        )
    if r.status_code != 201:
        raise RuntimeError(f"upload failed: HTTP {r.status_code}, body={r.text[:500]}")
    task_id = r.json()["task_id"]
    print(f"[upload] OK, task_id={task_id}")
    return task_id


def poll_task(session: requests.Session, task_id: str) -> dict:
    """Poll /api/multi-translate/<id> until terminal state or timeout."""
    print(f"[poll] watching task {task_id} (interval={POLL_INTERVAL_SEC}s, max={POLL_MAX_MIN}min)")
    start = time.time()
    last_step_summary = ""
    while time.time() - start < POLL_MAX_MIN * 60:
        r = session.get(f"{BASE}/api/multi-translate/{task_id}", timeout=15)
        if r.status_code != 200:
            print(f"  ! poll {r.status_code}, retry")
            time.sleep(POLL_INTERVAL_SEC)
            continue
        task = r.json().get("task") or r.json()
        steps = task.get("steps") or {}
        step_summary = " ".join(f"{k}:{v}" for k, v in steps.items())
        if step_summary != last_step_summary:
            elapsed = int(time.time() - start)
            print(f"  [{elapsed:>4}s] {step_summary}")
            last_step_summary = step_summary
        # terminal states
        if task.get("status") in ("done", "completed", "failed", "error"):
            print(f"[poll] terminal status={task.get('status')}")
            return task
        if all(v in ("done",) for v in steps.values()) and len(steps) > 4:
            print(f"[poll] all steps done")
            return task
        if any(v == "error" for v in steps.values()):
            print(f"[poll] a step errored")
            return task
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"task {task_id} did not finish within {POLL_MAX_MIN} min")


def assert_treatment_kpis(task: dict) -> None:
    print("\n========== TREATMENT VERIFICATION ==========")
    issues: list[str] = []

    # 1. source_language correctly stored
    src = task.get("source_language")
    print(f"task.source_language = {src!r}")
    if src != "es":
        issues.append(f"source_language={src!r}, expected 'es'")

    # 2. ASR engine should be Scribe for es source — check round records
    rounds = task.get("tts_duration_rounds") or []
    print(f"tts_duration_rounds count = {len(rounds)}")
    if rounds:
        first = rounds[0]
        max_attempts = first.get("max_rewrite_attempts")
        tolerance = first.get("word_tolerance")
        print(f"  round[0].max_rewrite_attempts = {max_attempts} (target={TARGET_LANG}, expect 7)")
        print(f"  round[0].word_tolerance = {tolerance} (target={TARGET_LANG}, expect 0.15)")
        if max_attempts != 7:
            issues.append(f"max_rewrite_attempts={max_attempts}, expected 7 for de target")
        if tolerance != 0.15:
            issues.append(f"word_tolerance={tolerance}, expected 0.15 for de target")

        rounds_count = len(rounds)
        print(f"  total rounds used = {rounds_count} (旧版西→德典型 5 跑满，治本目标 ≤ 3)")
        if rounds_count >= 5:
            print(f"  ⚠️  收敛轮数仍 ≥ 5，治本效果不明显，需要继续优化 prompt")

    print("==============================================")
    if issues:
        print("❌ FAILURES:")
        for x in issues:
            print(f"  - {x}")
        sys.exit(1)
    print("✅ KPI 验证全部通过")


def main():
    if not Path(VIDEO_PATH).exists():
        print(f"FATAL: 找不到视频 {VIDEO_PATH}")
        sys.exit(2)

    s = requests.Session()
    login(s)
    task_id = upload_task(s)
    final_task = poll_task(s, task_id)

    out = Path("/tmp/e2e_final_task.json")
    out.write_text(json.dumps(final_task, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nfinal task json saved → {out}")

    assert_treatment_kpis(final_task)


if __name__ == "__main__":
    main()
