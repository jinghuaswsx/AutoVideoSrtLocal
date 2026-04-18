"""
批量同步 ElevenLabs 共享音色库（运维脚本，非业务代码）。

用法（生产服务器）：
  cd /opt/autovideosrt
  nohup venv/bin/python -u scripts/sync_voice_libraries.py \
      > logs/voice_sync.log 2>&1 &
  echo $! > logs/voice_sync.pid

特性：
- 按序处理 LANGUAGES 列表中的语种。
- 每个语种两阶段：拉取 metadata → 回写 embedding。
- 状态持久化到 STATE_PATH，脚本重启后能从上次未完成的语种续跑。
- 不依赖 Flask session / SocketIO，直接调用 pipeline 函数。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.voice_library_sync import (  # noqa: E402
    embed_missing_voices,
    sync_all_shared_voices,
)
from appcore.db import query  # noqa: E402

LANGUAGES: list[str] = ["de", "fr", "es", "it", "ja", "pt"]
CACHE_DIR = str(ROOT / "uploads" / "voice_preview_cache")
STATE_PATH = ROOT / "logs" / "voice_sync.state.json"
LOG_PATH = ROOT / "logs" / "voice_sync.log"

log = logging.getLogger("voice_sync_driver")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s - %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, stream=sys.stdout)


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            log.warning("state file corrupt, starting fresh")
    return {"languages": {}}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _summary_row(lang: str) -> dict:
    rows = query(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN audio_embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded "
        "FROM elevenlabs_voices WHERE language=%s",
        (lang,),
    )
    r = rows[0] if rows else {}
    total = int(r.get("total") or 0)
    embedded = int(r.get("embedded") or 0)
    return {"total": total, "embedded": embedded}


def _on_page(lang: str, state: dict):
    def _cb(page_idx: int, voices: list) -> None:
        log.info("[%s] metadata page=%d size=%d", lang, page_idx, len(voices))
        state["languages"].setdefault(lang, {})["metadata_pages"] = page_idx + 1
        _save_state(state)
    return _cb


def _on_progress(lang: str, state: dict, throttle: dict):
    def _cb(done: int, total: int, voice_id: str, ok: bool) -> None:
        now = time.time()
        if not ok:
            log.warning("[%s] embed fail voice=%s (%d/%d)", lang, voice_id, done, total)
        if done == 1 or done == total or now - throttle.get("t", 0) >= 5:
            log.info("[%s] embed %d/%d", lang, done, total)
            throttle["t"] = now
        entry = state["languages"].setdefault(lang, {})
        entry["embed_done"] = done
        entry["embed_total"] = total
        _save_state(state)
    return _cb


def _sync_language(lang: str, api_key: str, state: dict) -> None:
    entry = state["languages"].setdefault(lang, {})
    if entry.get("status") == "done":
        log.info("[%s] already done, skip", lang)
        return

    entry["status"] = "running"
    entry["started_at"] = entry.get("started_at") or time.strftime("%Y-%m-%dT%H:%M:%S")
    _save_state(state)

    log.info("[%s] === phase 1: pull metadata ===", lang)
    pulled = sync_all_shared_voices(
        api_key=api_key,
        language=lang,
        on_page=_on_page(lang, state),
    )
    log.info("[%s] metadata pulled: %d voices", lang, pulled)

    log.info("[%s] === phase 2: embed ===", lang)
    throttle: dict = {}
    embedded = embed_missing_voices(
        CACHE_DIR,
        on_progress=_on_progress(lang, state, throttle),
        language=lang,
    )
    summary = _summary_row(lang)
    log.info(
        "[%s] done. embedded_this_run=%d total=%d embedded_total=%d",
        lang, embedded, summary["total"], summary["embedded"],
    )

    entry["status"] = "done"
    entry["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    entry["final_summary"] = summary
    _save_state(state)


def main() -> int:
    _setup_logging()
    api_key = os.getenv("ELEVENLABS_API_KEY") or ""
    if not api_key:
        # 退回读项目 .env
        env_path = ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("ELEVENLABS_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        log.error("ELEVENLABS_API_KEY missing")
        return 2

    state = _load_state()
    log.info("driver start, languages=%s state=%s", LANGUAGES, state)

    for lang in LANGUAGES:
        try:
            _sync_language(lang, api_key, state)
        except Exception as exc:
            log.exception("[%s] failed: %s", lang, exc)
            state["languages"].setdefault(lang, {})["status"] = "failed"
            state["languages"][lang]["error"] = str(exc)
            _save_state(state)

    log.info("driver finished. final state=%s", state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
