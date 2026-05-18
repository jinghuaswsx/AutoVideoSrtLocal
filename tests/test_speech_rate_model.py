import pytest
from unittest.mock import patch
from pipeline.speech_rate_model import (
    get_rate, update_rate, initialize_baseline, BENCHMARK_TEXT,
)


def test_get_rate_returns_none_when_no_record():
    with patch("pipeline.speech_rate_model._query_rate", return_value=None):
        rate = get_rate("v1", "en")
    assert rate is None


def test_get_rate_returns_float_when_record_exists():
    with patch("pipeline.speech_rate_model._query_rate",
               return_value={"chars_per_second": 14.5, "sample_count": 3}):
        rate = get_rate("v1", "en")
    assert rate == 14.5


def test_get_rate_with_source_prefers_actual_tts_over_preview_prior(monkeypatch):
    from pipeline import speech_rate_model

    monkeypatch.setattr(
        speech_rate_model,
        "_query_rate",
        lambda voice_id, language: {"chars_per_second": 14.5, "sample_count": 3},
    )
    monkeypatch.setattr(
        speech_rate_model,
        "_query_preview_prior_rate",
        lambda voice_id, language: 19.0,
        raising=False,
    )

    info = speech_rate_model.get_rate_with_source("v1", "en")

    assert info["chars_per_second"] == 14.5
    assert info["source"] == "actual_tts"


def test_get_rate_with_source_uses_preview_prior_when_actual_missing(monkeypatch):
    from pipeline import speech_rate_model

    monkeypatch.setattr(speech_rate_model, "_query_rate", lambda voice_id, language: None)
    monkeypatch.setattr(
        speech_rate_model,
        "_query_preview_prior_rate",
        lambda voice_id, language: 18.25,
        raising=False,
    )

    info = speech_rate_model.get_rate_with_source("v1", "en")

    assert info["chars_per_second"] == 18.25
    assert info["source"] == "preview_prior"
    assert speech_rate_model.get_effective_rate("v1", "en", fallback=14.0) == 18.25


def test_preview_prior_rate_matches_current_preview_url_hash(monkeypatch):
    import hashlib
    from pipeline import speech_rate_model

    preview_url = "https://example.com/current.mp3"
    expected_hash = hashlib.sha256(preview_url.encode("utf-8")).hexdigest()

    def fake_query_one(sql, args):
        if "FROM elevenlabs_voice_variants" in sql:
            return {"preview_url": preview_url}
        if "FROM voice_preview_speech_rate" in sql:
            assert args == ("v1", "en", expected_hash)
            return {"chars_per_second": 17.5}
        raise AssertionError(sql)

    monkeypatch.setattr(speech_rate_model, "query_one", fake_query_one)

    assert speech_rate_model._query_preview_prior_rate("v1", "en") == 17.5


def test_update_rate_inserts_first_sample():
    captured = {}
    def fake_upsert(voice_id, language, cps, count):
        captured.update(voice_id=voice_id, language=language,
                        cps=cps, count=count)
    with patch("pipeline.speech_rate_model._query_rate", return_value=None), \
         patch("pipeline.speech_rate_model._upsert_rate",
               side_effect=fake_upsert):
        update_rate("v1", "en", chars=90, duration_seconds=6.0)
    assert captured["cps"] == 15.0
    assert captured["count"] == 1


def test_update_rate_averages_incrementally():
    # existing: 20 char/s, count=2  => 新样本 60 chars / 4s = 15 char/s
    # 加权平均 = (20*2 + 15) / 3 = 18.333
    captured = {}
    def fake_upsert(voice_id, language, cps, count):
        captured.update(cps=cps, count=count)
    with patch("pipeline.speech_rate_model._query_rate",
               return_value={"chars_per_second": 20.0, "sample_count": 2}), \
         patch("pipeline.speech_rate_model._upsert_rate",
               side_effect=fake_upsert):
        update_rate("v1", "en", chars=60, duration_seconds=4.0)
    assert round(captured["cps"], 3) == 18.333
    assert captured["count"] == 3


def test_update_rate_ignores_invalid_inputs():
    called = {"n": 0}
    def fake_upsert(*args, **kwargs):
        called["n"] += 1
    with patch("pipeline.speech_rate_model._query_rate", return_value=None), \
         patch("pipeline.speech_rate_model._upsert_rate",
               side_effect=fake_upsert):
        update_rate("v1", "en", chars=0, duration_seconds=1.0)
        update_rate("v1", "en", chars=10, duration_seconds=0.0)
        update_rate("v1", "en", chars=10, duration_seconds=-1.0)
    assert called["n"] == 0


def test_initialize_baseline_uses_benchmark_text_and_updates_rate(tmp_path):
    updates = []
    def fake_update(voice_id, language, chars, duration_seconds):
        updates.append({"voice_id": voice_id, "language": language,
                        "chars": chars, "duration": duration_seconds})
    with patch("pipeline.speech_rate_model._generate_tts",
               return_value=("/tmp/out.mp3", 4.8)) as gen, \
         patch("pipeline.speech_rate_model.update_rate",
               side_effect=fake_update):
        cps = initialize_baseline("v1", "en", api_key="k",
                                    work_dir=str(tmp_path))
    benchmark = BENCHMARK_TEXT["en"]
    # 生成调用传入的 text 必须是基准文本
    assert gen.call_args.kwargs["text"] == benchmark
    # 返回的 cps = len / 4.8
    assert cps == pytest.approx(len(benchmark) / 4.8, rel=0.01)
    # 确保调了一次 update_rate
    assert len(updates) == 1
    assert updates[0]["chars"] == len(benchmark)
    assert updates[0]["duration"] == 4.8


def test_initialize_baseline_falls_back_to_english_for_unknown_language(tmp_path):
    with patch("pipeline.speech_rate_model._generate_tts",
               return_value=("/tmp/out.mp3", 3.0)) as gen, \
         patch("pipeline.speech_rate_model.update_rate"):
        initialize_baseline("v1", "xyz", api_key="k", work_dir=str(tmp_path))
    assert gen.call_args.kwargs["text"] == BENCHMARK_TEXT["en"]
