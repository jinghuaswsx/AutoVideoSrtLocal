"""
管理员触发的 ElevenLabs 声音库同步任务（全局单任务）。

职责：
- 全局只允许一个同步任务运行；`start_sync` 在忙时抛 `RuntimeError`。
- 任务在 daemon 线程中运行，阶段性通过 SocketIO 向 "admin" 房间广播进度。
- `summarize()` 按语种汇总 DB 统计，并与 `medias.list_enabled_languages_kv()`
  合并，保证所有启用语种都出现（无数据时计数为 0）。
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Any, Optional

from appcore.db import query
from appcore import realtime_events

log = logging.getLogger(__name__)

_CURRENT: dict[str, Any] = {"task": None, "summary": {}}
_LOCK = threading.Lock()

MAX_VOICES_PER_LANGUAGE = 1000


def _max_voices_per_language() -> int:
    raw = (os.getenv("VOICE_SYNC_MAX_PER_LANGUAGE") or "").strip()
    if not raw:
        return MAX_VOICES_PER_LANGUAGE
    try:
        value = int(raw)
    except ValueError:
        log.warning("invalid VOICE_SYNC_MAX_PER_LANGUAGE=%r, fallback to %d",
                    raw, MAX_VOICES_PER_LANGUAGE)
        return MAX_VOICES_PER_LANGUAGE
    return value if value > 0 else MAX_VOICES_PER_LANGUAGE


def _get_api_key() -> str:
    from appcore.llm_provider_configs import (
        ProviderConfigError,
        require_provider_api_key,
    )
    try:
        return require_provider_api_key("elevenlabs_tts")
    except ProviderConfigError as exc:
        raise RuntimeError(str(exc)) from exc


def _emit(event: str, payload: dict) -> None:
    if not realtime_events.emit_admin(event, payload):
        log.debug("admin realtime emitter is not registered: %s", event)


def start_sync(*, language: str) -> str:
    with _LOCK:
        if _CURRENT["task"] and _CURRENT["task"].get("status") == "running":
            raise RuntimeError("another sync is running")
        sync_id = "sync_" + uuid.uuid4().hex
        _CURRENT["task"] = {
            "sync_id": sync_id,
            "language": language,
            "phase": "pull_metadata",
            "done": 0,
            "total": 0,
            "status": "running",
            "error": None,
        }
    api_key = _get_api_key()
    threading.Thread(
        target=_run_sync_sync,
        args=(sync_id, language, api_key),
        daemon=True,
        name="voice-sync",
    ).start()
    return sync_id


def get_current() -> Optional[dict]:
    with _LOCK:
        return dict(_CURRENT["task"]) if _CURRENT["task"] else None


def summarize() -> list[dict]:
    from appcore import medias
    voice_rows = query(
        "SELECT language, "
        "  COUNT(*) AS total_rows, "
        "  SUM(CASE WHEN audio_embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded_rows, "
        "  MAX(synced_at) AS last_synced_at "
        "FROM elevenlabs_voices GROUP BY language"
    )
    try:
        variant_rows = query(
            "SELECT language, "
            "  COUNT(*) AS total_rows, "
            "  SUM(CASE WHEN audio_embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded_rows, "
            "  MAX(synced_at) AS last_synced_at "
            "FROM elevenlabs_voice_variants GROUP BY language"
        )
    except Exception:
        variant_rows = []
    stats_rows = query(
        "SELECT language, total_available, last_counted_at "
        "FROM elevenlabs_voice_library_stats"
    )
    voice_stats = {r["language"]: r for r in voice_rows}
    variant_stats = {r["language"]: r for r in variant_rows}
    avail_stats = {r["language"]: r for r in stats_rows}
    out: list[dict] = []
    max_voices = _max_voices_per_language()
    for code, name in medias.list_enabled_languages_kv():
        s = variant_stats.get(code) or voice_stats.get(code, {}) or {}
        a = avail_stats.get(code, {}) or {}
        last_synced = s.get("last_synced_at")
        total_available = int(a.get("total_available") or 0)
        target_total = (
            min(max_voices, total_available)
            if total_available
            else max_voices
        )
        out.append({
            "language": code,
            "name_zh": name,
            "total_rows": int(s.get("total_rows") or 0),
            "embedded_rows": int(s.get("embedded_rows") or 0),
            "total_available": total_available,
            "target_total": target_total,
            "last_synced_at": last_synced.isoformat() if last_synced else None,
        })
    return out


def _set(**updates) -> None:
    with _LOCK:
        if _CURRENT["task"]:
            _CURRENT["task"].update(updates)
            snapshot = dict(_CURRENT["task"])
        else:
            snapshot = None
    if snapshot is not None:
        _emit("voice_library.sync.progress", snapshot)


def _run_sync_sync(sync_id: str, language: str, api_key: str) -> None:
    from pipeline.voice_library_sync import (
        sync_all_shared_voices,
        embed_missing_voices,
        upsert_library_stats,
    )
    try:
        total_pulled = [0]
        total_available_holder = [0]

        def on_total_count(n: int) -> None:
            total_available_holder[0] = int(n)
            try:
                upsert_library_stats(language, int(n))
            except Exception as exc:
                log.warning("upsert_library_stats failed: %s", exc)
            cap = min(_max_voices_per_language(), int(n)) if n else 0
            _set(phase="pull_metadata", done=total_pulled[0], total=cap)

        def on_page(idx, voices):
            total_pulled[0] += len(voices)
            cap = min(
                _max_voices_per_language(),
                total_available_holder[0] or total_pulled[0],
            )
            _set(phase="pull_metadata", done=total_pulled[0], total=cap)

        sync_all_shared_voices(
            api_key=api_key,
            language=language,
            max_voices=_max_voices_per_language(),
            on_page=on_page,
            on_total_count=on_total_count,
        )

        def on_progress(done, total, voice_id, ok):
            _set(phase="embed", done=done, total=total)

        cache_dir = os.path.join("uploads", "voice_preview_cache")
        embed_missing_voices(
            cache_dir, on_progress=on_progress, language=language,
        )

        _set(status="done", phase="done")
        _emit("voice_library.sync.summary", {"summary": summarize()})
    except Exception as exc:
        log.exception("voice sync %s failed", sync_id)
        _set(status="failed", error=str(exc))
