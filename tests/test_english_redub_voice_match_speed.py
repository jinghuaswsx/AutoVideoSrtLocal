from __future__ import annotations

import numpy as np


def test_compute_source_speech_rate_uses_words_when_available():
    from pipeline.voice_match_speed import compute_source_speech_rate

    utterances = [
        {
            "text": "buy this now",
            "start_time": 0.0,
            "end_time": 1.0,
            "words": [
                {"word": "buy", "start": 0.0, "end": 0.2},
                {"word": "this", "start": 0.25, "end": 0.5},
                {"word": "now", "start": 0.55, "end": 0.95},
            ],
        },
        {"text": "ok", "start_time": 1.05, "end_time": 1.2},
        {"text": "it fits your daily routine", "start_time": 2.0, "end_time": 4.0},
    ]

    rate = compute_source_speech_rate(utterances)

    assert rate["sample_utterance_count"] == 2
    assert rate["ignored_utterance_count"] == 1
    assert 2.6 < rate["source_words_per_second"] < 2.9
    assert rate["source_chars_per_second"] > 10


def test_rank_speed_aware_keeps_similarity_floor():
    from pipeline.voice_match_speed import rank_speed_aware_candidates

    candidates = [
        {"voice_id": "top", "similarity": 0.90},
        {"voice_id": "fast", "similarity": 0.86},
        {"voice_id": "low", "similarity": 0.70},
    ]
    preview_rates = {"top": 2.0, "fast": 3.8, "low": 3.8}
    source_rate = {"source_words_per_second": 3.8}

    ranked = rank_speed_aware_candidates(
        candidates,
        source_rate,
        preview_rates,
        top_k=2,
    )

    assert [row["voice_id"] for row in ranked] == ["fast", "top"]
    assert "low" not in [row["voice_id"] for row in ranked]
    assert ranked[0]["speed_match_score"] > ranked[1]["speed_match_score"]
    assert ranked[0]["combined_score"] > ranked[1]["combined_score"]


def test_speed_aware_match_marks_missing_preview_rates_after_lazy_fill(monkeypatch):
    from pipeline import voice_match_speed

    candidates = [
        {"voice_id": "a", "similarity": 0.9},
        {"voice_id": "b", "similarity": 0.8},
    ]
    monkeypatch.setattr(
        voice_match_speed.voice_match,
        "match_candidates",
        lambda *args, **kwargs: candidates,
    )
    monkeypatch.setattr(
        voice_match_speed.voice_preview_speech_rate,
        "get_rates_for_voices",
        lambda *, language, voice_ids: {},
    )
    lazy_calls = []
    monkeypatch.setattr(
        voice_match_speed.voice_library_sync,
        "compute_missing_preview_speech_rates",
        lambda **kwargs: lazy_calls.append(kwargs) or 0,
    )

    ranked = voice_match_speed.match_candidates_speed_aware(
        np.array([1.0, 0.0], dtype=np.float32),
        language="en",
        source_utterances=[{"text": "hello world", "start_time": 0, "end_time": 1}],
    )

    assert [row["voice_id"] for row in ranked] == ["a", "b"]
    assert lazy_calls[0]["language"] == "en"
    assert lazy_calls[0]["voice_ids"] == ["a", "b"]
    assert ranked[0]["source_words_per_second"] == 2.0
    assert ranked[0]["preview_words_per_second"] is None
    assert ranked[0]["speed_match_score"] is None
    assert ranked[0]["combined_score"] == ranked[0]["similarity"]
    assert ranked[0]["voice_speed_status"] == "missing_preview_rate"
    assert ranked[0]["voice_match_strategy_effective"] == "legacy_fallback"


def test_preview_rate_dao_maps_voice_ids(monkeypatch):
    from appcore import voice_preview_speech_rate as rates

    monkeypatch.setattr(
        rates,
        "query",
        lambda sql, params=(): [
            {"voice_id": "a", "words_per_second": "3.5000"},
            {"voice_id": "b", "words_per_second": 2.0},
        ],
    )

    assert rates.get_rates_for_voices(language="en", voice_ids=["a", "b"]) == {
        "a": 3.5,
        "b": 2.0,
    }
