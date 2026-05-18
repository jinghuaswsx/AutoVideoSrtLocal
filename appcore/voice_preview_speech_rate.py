"""Cached speech-rate metadata measured from voice preview audio."""
from __future__ import annotations

from typing import Iterable

from appcore.db import execute, query


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
