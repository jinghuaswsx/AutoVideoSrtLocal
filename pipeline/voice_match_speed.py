"""Speed-aware voice matching helpers for English redub."""
from __future__ import annotations

import math
import re
import statistics
from typing import Iterable

import numpy as np

from appcore import voice_preview_speech_rate
from pipeline import voice_match

DEFAULT_CANDIDATE_POOL_SIZE = 100
DEFAULT_RESULT_TOP_K = 10
TIMBRE_WEIGHT = 0.75
SPEED_WEIGHT = 0.25
MIN_SIMILARITY_DELTA = 0.08
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?")


def _float_value(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(str(text or "")))


def _word_timing_rate(utterance: dict) -> tuple[int, int, float] | None:
    words = [
        word
        for word in (utterance.get("words") or [])
        if isinstance(word, dict) and str(word.get("word") or word.get("text") or "").strip()
    ]
    if not words:
        return None
    starts = [
        _float_value(word.get("start", word.get("start_time")), -1.0)
        for word in words
    ]
    ends = [
        _float_value(word.get("end", word.get("end_time")), -1.0)
        for word in words
    ]
    valid_starts = [value for value in starts if value >= 0]
    valid_ends = [value for value in ends if value >= 0]
    if not valid_starts or not valid_ends:
        return None
    duration = max(valid_ends) - min(valid_starts)
    if duration <= 0:
        return None
    char_count = sum(len(str(word.get("word") or word.get("text") or "")) for word in words)
    return len(words), char_count, duration


def _utterance_rate_sample(utterance: dict) -> tuple[float, float] | None:
    timing = _word_timing_rate(utterance)
    if timing is None:
        start = _float_value(utterance.get("start_time", utterance.get("start")), 0.0)
        end = _float_value(utterance.get("end_time", utterance.get("end")), start)
        duration = end - start
        text = str(utterance.get("text") or "")
        words = _word_count(text)
        chars = len(text.replace(" ", ""))
    else:
        words, chars, duration = timing
    if duration <= 0 or words <= 0:
        return None
    if duration < 0.35 and words <= 2:
        return None
    return words / duration, chars / duration if chars > 0 else 0.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def compute_source_speech_rate(utterances: Iterable[dict] | None) -> dict:
    word_rates: list[float] = []
    char_rates: list[float] = []
    ignored = 0
    for utterance in utterances or []:
        if not isinstance(utterance, dict):
            ignored += 1
            continue
        sample = _utterance_rate_sample(utterance)
        if sample is None:
            ignored += 1
            continue
        word_rate, char_rate = sample
        word_rates.append(word_rate)
        char_rates.append(char_rate)
    return {
        "source_words_per_second": round(_median(word_rates), 4),
        "source_chars_per_second": round(_median(char_rates), 4),
        "sample_utterance_count": len(word_rates),
        "ignored_utterance_count": ignored,
    }


def _speed_score(source_wps: float, candidate_wps: float | None) -> float | None:
    if source_wps <= 0 or candidate_wps is None or candidate_wps <= 0:
        return None
    ratio = max(source_wps, candidate_wps) / max(0.001, min(source_wps, candidate_wps))
    # 2.5x mismatch maps to zero; exact match maps to one.
    score = 1.0 - (math.log(ratio) / math.log(2.5))
    return max(0.0, min(1.0, score))


def rank_speed_aware_candidates(
    candidates: list[dict],
    source_rate: dict,
    preview_rates: dict[str, float],
    *,
    top_k: int = DEFAULT_RESULT_TOP_K,
) -> list[dict]:
    if not candidates:
        return []
    try:
        top_similarity = max(float(row.get("similarity") or 0.0) for row in candidates)
    except ValueError:
        return []
    similarity_floor = top_similarity - MIN_SIMILARITY_DELTA
    source_wps = float(source_rate.get("source_words_per_second") or 0.0)
    ranked: list[dict] = []
    for candidate in candidates:
        similarity = float(candidate.get("similarity") or 0.0)
        if similarity < similarity_floor:
            continue
        voice_id = str(candidate.get("voice_id") or "").strip()
        speed_score = _speed_score(source_wps, preview_rates.get(voice_id))
        combined = similarity
        if speed_score is not None:
            combined = similarity * TIMBRE_WEIGHT + speed_score * SPEED_WEIGHT
        row = dict(candidate)
        row["similarity"] = similarity
        row["source_words_per_second"] = source_wps or None
        row["preview_words_per_second"] = preview_rates.get(voice_id)
        row["speed_match_score"] = speed_score
        row["combined_score"] = combined
        ranked.append(row)
    ranked.sort(
        key=lambda row: (
            float(row.get("combined_score") or 0.0),
            float(row.get("similarity") or 0.0),
        ),
        reverse=True,
    )
    return ranked[:top_k]


def match_candidates_speed_aware(
    query_embedding: np.ndarray,
    *,
    language: str,
    source_utterances: Iterable[dict] | None,
    gender: str | None = None,
    top_k: int = DEFAULT_RESULT_TOP_K,
    candidate_pool_size: int = DEFAULT_CANDIDATE_POOL_SIZE,
    exclude_voice_ids: Iterable[str] | None = None,
) -> list[dict]:
    candidates = voice_match.match_candidates(
        query_embedding,
        language=language,
        gender=gender,
        top_k=candidate_pool_size,
        exclude_voice_ids=exclude_voice_ids,
    )
    if not candidates:
        return []
    source_rate = compute_source_speech_rate(source_utterances)
    preview_rates = voice_preview_speech_rate.get_rates_for_voices(
        language=language,
        voice_ids=[
            str(candidate.get("voice_id") or "").strip()
            for candidate in candidates
            if candidate.get("voice_id")
        ],
    )
    if (
        not preview_rates
        or float(source_rate.get("source_words_per_second") or 0.0) <= 0
    ):
        return candidates[:top_k]
    return rank_speed_aware_candidates(
        candidates,
        source_rate,
        preview_rates,
        top_k=top_k,
    )
