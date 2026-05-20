"""Local archive for ElevenLabs voice preview audio and ASR metadata."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

import requests

from appcore import voice_preview_speech_rate
from appcore.db import execute, query, query_one
from appcore.safe_paths import PathSafetyError, resolve_under_allowed_roots
from config import UPLOAD_DIR

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30
DOWNLOAD_RETRIES = 3
DOWNLOAD_RETRY_SLEEP_SECONDS = 1.0

_BASE_TABLE = "elevenlabs_voices"
_VARIANTS_TABLE = "elevenlabs_voice_variants"


def hash_preview_url(preview_url: str) -> str:
    return voice_preview_speech_rate.hash_preview_url(preview_url)


def _default_archive_dir() -> str:
    return str(Path(UPLOAD_DIR) / "voice_preview_archive")


def _archive_file_path(
    *,
    archive_dir: str | None,
    language: str,
    voice_id: str,
    preview_url_hash: str,
) -> Path:
    lang = str(language or "").strip().lower() or "unknown"
    key = hashlib.sha1(
        f"{lang}:{str(voice_id or '').strip()}:{preview_url_hash}".encode("utf-8")
    ).hexdigest()
    return Path(archive_dir or _default_archive_dir()) / lang / f"{key}.mp3"


def _local_preview_url(*, language: str, voice_id: str, preview_url_hash: str) -> str:
    return (
        "/voice-library/api/preview/"
        f"{quote(str(language or '').strip(), safe='')}/"
        f"{quote(str(voice_id or '').strip(), safe='')}"
        f"?hash={quote(str(preview_url_hash or '').strip(), safe='')}"
    )


def _safe_existing_archive_file(path: str | None) -> str | None:
    raw = str(path or "").strip()
    if not raw:
        return None
    try:
        safe = resolve_under_allowed_roots(raw, [UPLOAD_DIR])
    except PathSafetyError:
        return None
    return str(safe) if safe.is_file() else None


def _clean_voice_ids(items: Iterable[dict]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        voice_id = str(item.get("voice_id") or "").strip()
        if not voice_id or voice_id in seen:
            continue
        seen.add(voice_id)
        out.append(voice_id)
    return out


def _archive_rows_for_items(items: list[dict], *, language: str) -> dict[tuple[str, str], dict]:
    ids = _clean_voice_ids(items)
    lang = str(language or "").strip().lower()
    if not lang or not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    rows = query(
        "SELECT voice_id, language, preview_url_hash, local_path, "
        "duration_seconds, transcript_text, status "
        "FROM voice_preview_archives "
        "WHERE language = %s "
        f"AND voice_id IN ({placeholders})",
        tuple([lang, *ids]),
    )
    return {
        (
            str(row.get("voice_id") or "").strip(),
            str(row.get("preview_url_hash") or "").strip(),
        ): row
        for row in rows or []
    }


def attach_local_preview_urls(items: list[dict], *, language: str) -> list[dict]:
    """Attach local preview metadata when an archive exists for current URL hash."""
    out = [dict(item) for item in (items or [])]
    lang = str(language or "").strip().lower()
    for item in out:
        preview_url = str(item.get("preview_url") or "").strip()
        if preview_url:
            item["preview_url_hash"] = hash_preview_url(preview_url)
    if not lang or not out:
        return out

    try:
        archive_rows = _archive_rows_for_items(out, language=lang)
    except Exception as exc:
        log.warning("voice preview archive lookup failed for %s: %s", lang, exc)
        return out

    for item in out:
        voice_id = str(item.get("voice_id") or "").strip()
        preview_hash = str(item.get("preview_url_hash") or "").strip()
        row = archive_rows.get((voice_id, preview_hash))
        if not row or str(row.get("status") or "").strip() != "ready":
            continue
        if not _safe_existing_archive_file(row.get("local_path")):
            continue
        item["preview_local_url"] = _local_preview_url(
            language=lang,
            voice_id=voice_id,
            preview_url_hash=preview_hash,
        )
        duration = row.get("duration_seconds")
        if duration is not None:
            try:
                item["preview_duration_seconds"] = float(duration)
            except (TypeError, ValueError):
                pass
        transcript = str(row.get("transcript_text") or "").strip()
        if transcript:
            item["preview_transcript_text"] = transcript
    return out


def resolve_local_preview_path(
    *,
    language: str,
    voice_id: str,
    preview_url_hash: str,
) -> str | None:
    lang = str(language or "").strip().lower()
    voice = str(voice_id or "").strip()
    preview_hash = str(preview_url_hash or "").strip()
    if not lang or not voice or not preview_hash:
        return None
    row = query_one(
        "SELECT local_path, status FROM voice_preview_archives "
        "WHERE language = %s AND voice_id = %s AND preview_url_hash = %s "
        "LIMIT 1",
        (lang, voice, preview_hash),
    )
    if not row or str(row.get("status") or "").strip() != "ready":
        return None
    return _safe_existing_archive_file(row.get("local_path"))


def _preview_rate_source_code(language: str | None) -> str:
    return "elevenlabs_scribe"


def _preview_rate_provider_code(language: str | None) -> str:
    return "elevenlabs_tts"


def _asr_source(language: str | None) -> str:
    return f"preview_asr:{_preview_rate_source_code(language)}"


def _download_preview(url: str, dest_path: Path) -> str:
    for attempt in range(DOWNLOAD_RETRIES):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException:
            if attempt + 1 >= DOWNLOAD_RETRIES:
                raise
            time.sleep(DOWNLOAD_RETRY_SLEEP_SECONDS * (attempt + 1))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
    return str(dest_path)


def _get_audio_duration(path: str) -> float:
    from pipeline import tts

    return float(tts.get_audio_duration(path) or 0.0)


def _transcribe_preview(path: str, language: str | None) -> list[dict]:
    from appcore.asr_providers import build_adapter

    adapter = build_adapter(_preview_rate_provider_code(language))
    return adapter.transcribe(Path(path), language=(language or None))


def _transcript_text_from_utterances(utterances: Iterable[dict] | None) -> str:
    parts = []
    for item in utterances or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def upsert_archive(
    *,
    voice_id: str,
    language: str,
    preview_url: str,
    preview_url_hash: str,
    local_path: str | None,
    duration_seconds: float | None,
    transcript_text: str | None,
    utterances_json: list[dict] | None,
    asr_source: str | None,
    status: str,
    error: str | None = None,
) -> None:
    now = datetime.utcnow()
    execute(
        "INSERT INTO voice_preview_archives "
        "(voice_id, language, preview_url_hash, preview_url, local_path, "
        "duration_seconds, transcript_text, utterances_json, asr_source, "
        "status, error, archived_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "preview_url = VALUES(preview_url), "
        "local_path = VALUES(local_path), "
        "duration_seconds = VALUES(duration_seconds), "
        "transcript_text = VALUES(transcript_text), "
        "utterances_json = VALUES(utterances_json), "
        "asr_source = VALUES(asr_source), "
        "status = VALUES(status), "
        "error = VALUES(error), "
        "updated_at = VALUES(updated_at)",
        (
            str(voice_id or "").strip(),
            str(language or "").strip().lower(),
            str(preview_url_hash or "").strip(),
            str(preview_url or "").strip(),
            str(local_path or "").strip() or None,
            duration_seconds,
            transcript_text,
            json.dumps(utterances_json or [], ensure_ascii=False),
            asr_source,
            status,
            error,
            now,
            now,
        ),
    )


def archive_preview_target(
    target: dict,
    *,
    archive_dir: str | None = None,
) -> dict:
    voice_id = str(target.get("voice_id") or "").strip()
    language = str(target.get("language") or "").strip().lower()
    preview_url = str(target.get("preview_url") or "").strip()
    preview_hash = str(target.get("preview_url_hash") or "").strip() or hash_preview_url(preview_url)
    if not voice_id or not language or not preview_url or not preview_hash:
        raise ValueError("voice_id, language, preview_url, and preview_url_hash are required")

    dest = _archive_file_path(
        archive_dir=archive_dir,
        language=language,
        voice_id=voice_id,
        preview_url_hash=preview_hash,
    )
    source = _asr_source(language)
    try:
        local_path = _download_preview(preview_url, dest)
        duration = round(_get_audio_duration(local_path), 3)
        utterances = _transcribe_preview(local_path, language)
        transcript_text = _transcript_text_from_utterances(utterances)
        upsert_archive(
            voice_id=voice_id,
            language=language,
            preview_url=preview_url,
            preview_url_hash=preview_hash,
            local_path=local_path,
            duration_seconds=duration,
            transcript_text=transcript_text,
            utterances_json=utterances,
            asr_source=source,
            status="ready",
            error=None,
        )
        rate = voice_preview_speech_rate.compute_rate_from_utterances(
            utterances,
            fallback_duration=duration,
        )
        if rate.get("words_per_second"):
            voice_preview_speech_rate.upsert_rate(
                voice_id=voice_id,
                language=language,
                preview_url_hash=preview_hash,
                words_per_second=rate.get("words_per_second"),
                chars_per_second=rate.get("chars_per_second"),
                sample_duration=rate.get("sample_duration"),
                source=source,
            )
        return {
            "voice_id": voice_id,
            "language": language,
            "preview_url_hash": preview_hash,
            "local_path": local_path,
            "duration_seconds": duration,
            "transcript_text": transcript_text,
            "status": "ready",
        }
    except Exception as exc:
        error = str(exc)
        log.warning("[voice_preview_archive] failed %s/%s: %s", language, voice_id, error)
        upsert_archive(
            voice_id=voice_id,
            language=language,
            preview_url=preview_url,
            preview_url_hash=preview_hash,
            local_path=str(dest) if dest.exists() else None,
            duration_seconds=None,
            transcript_text=None,
            utterances_json=[],
            asr_source=source,
            status="failed",
            error=error,
        )
        return {
            "voice_id": voice_id,
            "language": language,
            "preview_url_hash": preview_hash,
            "local_path": str(dest) if dest.exists() else None,
            "duration_seconds": None,
            "transcript_text": "",
            "status": "failed",
            "error": error,
        }


def _fetch_preview_rows(table: str, *, language: str | None = None) -> list[dict]:
    sql = (
        f"SELECT voice_id, language, preview_url FROM {table} "
        "WHERE preview_url IS NOT NULL AND preview_url <> ''"
    )
    params: tuple = ()
    lang = str(language or "").strip().lower()
    if lang:
        sql += " AND language = %s"
        params = (lang,)
    return query(sql, params) if params else query(sql)


def _target_key(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("voice_id") or "").strip(),
        str(row.get("language") or "").strip().lower(),
        str(row.get("preview_url_hash") or "").strip(),
    )


def list_preview_archive_targets(
    *,
    language: str | None = None,
    limit: int | None = None,
    include_ready: bool = False,
) -> list[dict]:
    candidates: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for table in (_BASE_TABLE, _VARIANTS_TABLE):
        try:
            rows = _fetch_preview_rows(table, language=language)
        except Exception:
            if table == _VARIANTS_TABLE:
                continue
            raise
        for row in rows or []:
            voice_id = str(row.get("voice_id") or "").strip()
            lang = str(row.get("language") or "").strip().lower()
            preview_url = str(row.get("preview_url") or "").strip()
            if not voice_id or not lang or not preview_url:
                continue
            preview_hash = hash_preview_url(preview_url)
            key = (voice_id, lang, preview_hash)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "voice_id": voice_id,
                "language": lang,
                "preview_url": preview_url,
                "preview_url_hash": preview_hash,
                "source_table": table,
            })
    if not candidates or include_ready:
        return candidates[: int(limit)] if limit else candidates

    langs = sorted({row["language"] for row in candidates})
    placeholders = ",".join(["%s"] * len(langs))
    existing_rows = query(
        "SELECT voice_id, language, preview_url_hash, local_path, status "
        "FROM voice_preview_archives "
        f"WHERE language IN ({placeholders})",
        tuple(langs),
    )
    ready_keys = {
        _target_key(row)
        for row in existing_rows or []
        if str(row.get("status") or "").strip() == "ready"
        and _safe_existing_archive_file(row.get("local_path"))
    }
    missing = [row for row in candidates if _target_key(row) not in ready_keys]
    return missing[: int(limit)] if limit else missing


def archive_missing_voice_previews(
    *,
    archive_dir: str | None = None,
    language: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    workers: int = 1,
    on_progress: Callable[[int, int, str, bool], None] | None = None,
) -> dict[str, int]:
    targets = list_preview_archive_targets(language=language, limit=limit)
    total = len(targets)
    if dry_run:
        return {"total": total, "archived": 0, "failed": 0, "skipped": total}

    archived = 0
    failed = 0

    def _record_progress(index: int, total_rows: int, voice_id: str, ok: bool) -> None:
        if on_progress is not None:
            try:
                on_progress(index, total_rows, voice_id, ok)
            except Exception as exc:
                log.warning("on_progress callback failed at %s: %s", voice_id, exc)

    def _process_target(target: dict) -> tuple[str, bool]:
        voice_id = str(target.get("voice_id") or "").strip()
        try:
            result = archive_preview_target(target, archive_dir=archive_dir)
            return voice_id, result.get("status") == "ready"
        except Exception as exc:
            log.warning("[voice_preview_archive] unexpected failure %s: %s", voice_id, exc)
            return voice_id, False

    worker_count = max(1, int(workers or 1))
    if worker_count == 1:
        for index, target in enumerate(targets, start=1):
            voice_id, ok = _process_target(target)
            if ok:
                archived += 1
            else:
                failed += 1
            _record_progress(index, total, voice_id, ok)
        return {"total": total, "archived": archived, "failed": failed, "skipped": 0}

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(_process_target, target) for target in targets]
        for index, future in enumerate(as_completed(futures), start=1):
            voice_id, ok = future.result()
            if ok:
                archived += 1
            else:
                failed += 1
            _record_progress(index, total, voice_id, ok)
    return {"total": total, "archived": archived, "failed": failed, "skipped": 0}
