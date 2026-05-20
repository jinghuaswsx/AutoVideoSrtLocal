from __future__ import annotations

import numpy as np
import pytest


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


def test_rank_speed_aware_reranks_similarity_pool_by_speed():
    from pipeline.voice_match_speed import rank_speed_aware_candidates

    candidates = [
        {"voice_id": "top", "similarity": 0.94},
        {"voice_id": "fast", "similarity": 0.86},
        {"voice_id": "low", "similarity": 0.70},
    ]
    preview_rates = {"top": 3.2, "fast": 3.8, "low": 1.0}
    source_rate = {"source_words_per_second": 3.8}

    ranked = rank_speed_aware_candidates(
        candidates,
        source_rate,
        preview_rates,
        top_k=3,
    )

    assert [row["voice_id"] for row in ranked] == ["fast", "top", "low"]
    assert ranked[0]["speed_match_score"] > ranked[1]["speed_match_score"]
    assert ranked[0]["combined_score"] < ranked[1]["combined_score"]
    assert [row["similarity_rank"] for row in ranked] == [2, 1, 3]


def test_speed_aware_match_marks_missing_preview_rates_after_lazy_fill(monkeypatch):
    from pipeline import voice_match_speed

    candidates = [
        {"voice_id": "a", "similarity": 0.9},
        {"voice_id": "b", "similarity": 0.8},
    ]
    match_calls = []
    monkeypatch.setattr(
        voice_match_speed.voice_match,
        "match_candidates",
        lambda *args, **kwargs: match_calls.append(kwargs) or candidates,
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
    assert match_calls[0]["top_k"] == 20
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


def test_preview_rate_targets_include_missing_voice_and_variant(monkeypatch):
    from appcore import voice_preview_speech_rate as rates

    queries = []

    def fake_query(sql, params=()):
        queries.append((sql, params))
        if "FROM elevenlabs_voices" in sql:
            return [
                {"voice_id": "base", "language": "en", "preview_url": "https://p/base.mp3"},
            ]
        if "FROM elevenlabs_voice_variants" in sql:
            return [
                {"voice_id": "variant", "language": "nl", "preview_url": "https://p/nl.mp3"},
            ]
        if "FROM voice_preview_speech_rate" in sql:
            return [
                {
                    "voice_id": "base",
                    "language": "en",
                    "preview_url_hash": rates.hash_preview_url("https://p/base.mp3"),
                }
            ]
        return []

    monkeypatch.setattr(rates, "query", fake_query)

    targets = rates.list_missing_preview_rate_targets()

    assert [(row["voice_id"], row["language"]) for row in targets] == [("variant", "nl")]
    assert targets[0]["preview_url_hash"] == rates.hash_preview_url("https://p/nl.mp3")
    assert any("elevenlabs_voice_variants" in sql for sql, _params in queries)


def test_preview_rate_targets_deduplicate_base_and_variant(monkeypatch):
    from appcore import voice_preview_speech_rate as rates

    preview_url = "https://p/shared.mp3"

    def fake_query(sql, params=()):
        if "FROM elevenlabs_voices" in sql:
            return [
                {"voice_id": "shared", "language": "en", "preview_url": preview_url},
            ]
        if "FROM elevenlabs_voice_variants" in sql:
            return [
                {"voice_id": "shared", "language": "en", "preview_url": preview_url},
            ]
        return []

    monkeypatch.setattr(rates, "query", fake_query)

    targets = rates.list_missing_preview_rate_targets(language="en")

    assert [(row["voice_id"], row["language"]) for row in targets] == [("shared", "en")]


def test_preview_rate_computation_uses_asr_words():
    from appcore.voice_preview_speech_rate import compute_rate_from_utterances

    result = compute_rate_from_utterances(
        [
            {
                "text": "hello fast world",
                "start_time": 0.0,
                "end_time": 1.0,
                "words": [
                    {"text": "hello", "start_time": 0.0, "end_time": 0.2},
                    {"text": "fast", "start_time": 0.3, "end_time": 0.5},
                    {"text": "world", "start_time": 0.6, "end_time": 1.0},
                ],
            }
        ],
        fallback_duration=1.4,
    )

    assert result["words_per_second"] == pytest.approx(3.0)
    assert result["chars_per_second"] == pytest.approx(14.0)
    assert result["sample_duration"] == pytest.approx(1.0)
    assert result["sample_text"] == "hello fast world"


def test_backfill_preview_rates_downloads_transcribes_and_upserts(tmp_path, monkeypatch):
    from pipeline import voice_library_sync as sync
    from appcore import voice_preview_speech_rate as rates

    upserts = []
    monkeypatch.setattr(
        rates,
        "list_missing_preview_rate_targets",
        lambda language=None, limit=None: [
            {
                "voice_id": "v1",
                "language": "en",
                "preview_url": "https://p/v1.mp3",
                "preview_url_hash": "hash-v1",
            }
        ],
    )
    monkeypatch.setattr(sync, "_download_preview", lambda url, dest: dest.write_bytes(b"mp3") or str(dest))
    monkeypatch.setattr(sync.tts, "get_audio_duration", lambda path: 1.0)
    monkeypatch.setattr(
        sync,
        "_transcribe_preview_for_rate",
        lambda path, language: [
            {
                "text": "hello world",
                "start_time": 0.0,
                "end_time": 1.0,
                "words": [
                    {"text": "hello", "start_time": 0.0, "end_time": 0.4},
                    {"text": "world", "start_time": 0.5, "end_time": 1.0},
                ],
            }
        ],
    )
    monkeypatch.setattr(rates, "upsert_rate", lambda **kwargs: upserts.append(kwargs))

    result = sync.backfill_missing_preview_speech_rates(str(tmp_path), language="en")

    assert result == {"total": 1, "processed": 1, "updated": 1, "failed": 0, "skipped": 0}
    assert upserts[0]["voice_id"] == "v1"
    assert upserts[0]["language"] == "en"
    assert upserts[0]["preview_url_hash"] == "hash-v1"
    assert upserts[0]["words_per_second"] == pytest.approx(2.0)
    assert upserts[0]["source"] == "preview_asr:doubao_asr"


def test_backfill_preview_rates_dry_run_does_not_download(tmp_path, monkeypatch):
    from pipeline import voice_library_sync as sync
    from appcore import voice_preview_speech_rate as rates

    monkeypatch.setattr(
        rates,
        "list_missing_preview_rate_targets",
        lambda language=None, limit=None: [
            {
                "voice_id": "v1",
                "language": "en",
                "preview_url": "https://p/v1.mp3",
                "preview_url_hash": "hash-v1",
            }
        ],
    )
    monkeypatch.setattr(
        sync,
        "_download_preview",
        lambda url, dest: (_ for _ in ()).throw(AssertionError("dry-run must not download")),
    )

    result = sync.backfill_missing_preview_speech_rates(str(tmp_path), dry_run=True)

    assert result == {"total": 1, "processed": 0, "updated": 0, "failed": 0, "skipped": 1}


def test_backfill_preview_rates_supports_workers(tmp_path, monkeypatch):
    from pipeline import voice_library_sync as sync
    from appcore import voice_preview_speech_rate as rates

    upserts = []
    monkeypatch.setattr(
        rates,
        "list_missing_preview_rate_targets",
        lambda language=None, limit=None: [
            {
                "voice_id": f"v{index}",
                "language": "en",
                "preview_url": f"https://p/v{index}.mp3",
                "preview_url_hash": f"hash-v{index}",
            }
            for index in range(3)
        ],
    )
    monkeypatch.setattr(sync, "_download_preview", lambda url, dest: dest.write_bytes(b"mp3") or str(dest))
    monkeypatch.setattr(sync.tts, "get_audio_duration", lambda path: 1.0)
    monkeypatch.setattr(
        sync,
        "_transcribe_preview_for_rate",
        lambda path, language: [
            {
                "text": "hello world",
                "start_time": 0.0,
                "end_time": 1.0,
                "words": [
                    {"text": "hello", "start_time": 0.0, "end_time": 0.4},
                    {"text": "world", "start_time": 0.5, "end_time": 1.0},
                ],
            }
        ],
    )
    monkeypatch.setattr(rates, "upsert_rate", lambda **kwargs: upserts.append(kwargs))

    result = sync.backfill_missing_preview_speech_rates(str(tmp_path), workers=2)

    assert result == {"total": 3, "processed": 3, "updated": 3, "failed": 0, "skipped": 0}
    assert sorted(row["voice_id"] for row in upserts) == ["v0", "v1", "v2"]


def test_preview_rate_transcribe_retries_transient_failure(monkeypatch):
    from pipeline import voice_library_sync as sync

    attempts = {"count": 0}

    def flaky_transcribe(path, language):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary eof")
        return [{"text": "hello", "start_time": 0.0, "end_time": 1.0}]

    monkeypatch.setattr(sync, "_transcribe_preview_for_rate", flaky_transcribe)
    monkeypatch.setattr(sync.time, "sleep", lambda seconds: None)

    assert sync._transcribe_preview_for_rate_with_retry("preview.mp3", "en") == [
        {"text": "hello", "start_time": 0.0, "end_time": 1.0}
    ]
    assert attempts["count"] == 2
