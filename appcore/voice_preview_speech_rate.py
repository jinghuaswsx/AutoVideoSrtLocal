"""Cached speech-rate metadata measured from voice preview audio."""
from __future__ import annotations

import hashlib
import re
from typing import Iterable

from appcore.db import execute, query

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?")


def hash_preview_url(preview_url: str) -> str:
    return hashlib.sha256(str(preview_url or "").strip().encode("utf-8")).hexdigest()


def _clean_voice_ids(voice_ids: Iterable[str] | None) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in voice_ids or []:
        voice_id = str(raw or "").strip()
        if not voice_id or voice_id in seen:
            continue
        seen.add(voice_id)
        cleaned.append(voice_id)
    return cleaned


def _fetch_preview_rows(table: str, *, language: str | None = None) -> list[dict]:
    sql = (
        f"SELECT voice_id, language, preview_url FROM {table} "
        "WHERE preview_url IS NOT NULL AND preview_url <> ''"
    )
    params: tuple = ()
    lang = str(language or "").strip()
    if lang:
        sql += " AND language = %s"
        params = (lang,)
    return query(sql, params) if params else query(sql)


def _target_key(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("voice_id") or "").strip(),
        str(row.get("language") or "").strip(),
        str(row.get("preview_url_hash") or "").strip(),
    )


def list_missing_preview_rate_targets(
    *,
    language: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return voice preview rows missing a rate for the current preview URL hash."""
    candidates: list[dict] = []
    candidate_keys: set[tuple[str, str, str]] = set()
    for table in ("elevenlabs_voices", "elevenlabs_voice_variants"):
        try:
            rows = _fetch_preview_rows(table, language=language)
        except Exception:
            if table == "elevenlabs_voice_variants":
                continue
            raise
        for row in rows or []:
            voice_id = str(row.get("voice_id") or "").strip()
            lang = str(row.get("language") or "").strip()
            preview_url = str(row.get("preview_url") or "").strip()
            if not voice_id or not lang or not preview_url:
                continue
            preview_url_hash = hash_preview_url(preview_url)
            key = (voice_id, lang, preview_url_hash)
            if key in candidate_keys:
                continue
            candidate_keys.add(key)
            candidates.append(
                {
                    "voice_id": voice_id,
                    "language": lang,
                    "preview_url": preview_url,
                    "preview_url_hash": preview_url_hash,
                    "source_table": table,
                }
            )

    if not candidates:
        return []

    langs = sorted({row["language"] for row in candidates})
    placeholders = ",".join(["%s"] * len(langs))
    existing_rows = query(
        "SELECT voice_id, language, preview_url_hash "
        "FROM voice_preview_speech_rate "
        f"WHERE language IN ({placeholders})",
        tuple(langs),
    )
    existing = {_target_key(row) for row in existing_rows or []}
    missing = [
        row for row in candidates
        if _target_key(row) not in existing
    ]
    if limit:
        return missing[: int(limit)]
    return missing


def get_rates_for_voices(*, language: str, voice_ids: Iterable[str]) -> dict[str, float]:
    ids = _clean_voice_ids(voice_ids)
    lang = str(language or "").strip()
    if not lang or not ids:
        return {}

    placeholders = ",".join(["%s"] * len(ids))
    rows = query(
        "SELECT voice_id, words_per_second "
        "FROM voice_preview_speech_rate "
        "WHERE language = %s "
        f"AND voice_id IN ({placeholders}) "
        "AND words_per_second IS NOT NULL "
        "ORDER BY updated_at DESC",
        tuple([lang, *ids]),
    )
    rates: dict[str, float] = {}
    for row in rows or []:
        voice_id = str(row.get("voice_id") or "").strip()
        if not voice_id or voice_id in rates:
            continue
        try:
            rate = float(row.get("words_per_second"))
        except (TypeError, ValueError):
            continue
        if rate > 0:
            rates[voice_id] = rate
    return rates


def _word_text(word: dict) -> str:
    return str(word.get("text") or word.get("word") or "").strip()


def _words_from_text(text: str) -> list[str]:
    return _WORD_RE.findall(str(text or ""))


def compute_rate_from_utterances(
    utterances: Iterable[dict] | None,
    *,
    fallback_duration: float | None = None,
) -> dict:
    """Compute preview speech rate from ASR utterances, preferring word timestamps."""
    timed_words: list[dict] = []
    text_parts: list[str] = []
    utterance_starts: list[float] = []
    utterance_ends: list[float] = []

    for utterance in utterances or []:
        if not isinstance(utterance, dict):
            continue
        text = str(utterance.get("text") or "").strip()
        if text:
            text_parts.append(text)
        try:
            start = float(utterance.get("start_time", utterance.get("start", 0.0)) or 0.0)
            end = float(utterance.get("end_time", utterance.get("end", start)) or start)
        except (TypeError, ValueError):
            start = end = 0.0
        if end > start:
            utterance_starts.append(start)
            utterance_ends.append(end)
        for word in utterance.get("words") or []:
            if not isinstance(word, dict):
                continue
            token = _word_text(word)
            if not token:
                continue
            try:
                word_start = float(word.get("start_time", word.get("start", 0.0)) or 0.0)
                word_end = float(word.get("end_time", word.get("end", word_start)) or word_start)
            except (TypeError, ValueError):
                continue
            if word_end <= word_start:
                continue
            timed_words.append({"text": token, "start": word_start, "end": word_end})

    if timed_words:
        duration = max(word["end"] for word in timed_words) - min(word["start"] for word in timed_words)
        words = [_word_text(word) for word in timed_words if _word_text(word)]
    else:
        duration = 0.0
        if utterance_starts and utterance_ends:
            duration = max(utterance_ends) - min(utterance_starts)
        if duration <= 0 and fallback_duration:
            duration = float(fallback_duration)
        words = _words_from_text(" ".join(text_parts))

    chars = sum(len(word) for word in words)
    word_count = len(words)
    sample_text = " ".join(text_parts).strip()
    if not sample_text and words:
        sample_text = " ".join(words)

    if duration <= 0 or word_count <= 0:
        return {
            "words_per_second": None,
            "chars_per_second": None,
            "sample_duration": float(fallback_duration or 0.0),
            "sample_text": sample_text,
        }

    return {
        "words_per_second": round(word_count / duration, 4),
        "chars_per_second": round(chars / duration, 4),
        "sample_duration": round(duration, 3),
        "sample_text": sample_text[:1000],
    }


def upsert_rate(
    *,
    voice_id: str,
    language: str,
    preview_url_hash: str,
    words_per_second: float | None,
    chars_per_second: float | None = None,
    sample_duration: float | None = None,
    source: str = "preview",
) -> None:
    execute(
        "INSERT INTO voice_preview_speech_rate "
        "(voice_id, language, preview_url_hash, words_per_second, chars_per_second, "
        "sample_duration, source, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP) "
        "ON DUPLICATE KEY UPDATE "
        "words_per_second = VALUES(words_per_second), "
        "chars_per_second = VALUES(chars_per_second), "
        "sample_duration = VALUES(sample_duration), "
        "source = VALUES(source), "
        "updated_at = CURRENT_TIMESTAMP",
        (
            str(voice_id or "").strip(),
            str(language or "").strip(),
            str(preview_url_hash or "").strip(),
            words_per_second,
            chars_per_second,
            sample_duration,
            source,
        ),
    )
