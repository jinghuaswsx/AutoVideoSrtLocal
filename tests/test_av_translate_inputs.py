from __future__ import annotations

from appcore.av_translate_inputs import build_default_av_translate_inputs, normalize_av_translate_inputs


def test_default_av_translate_inputs_use_hybrid_sync_granularity():
    defaults = build_default_av_translate_inputs()

    assert defaults["sync_granularity"] == "hybrid"


def test_normalize_av_translate_inputs_accepts_sentence_granularity():
    normalized = normalize_av_translate_inputs({"sync_granularity": "sentence"})

    assert normalized["sync_granularity"] == "sentence"


def test_normalize_av_translate_inputs_rejects_unknown_granularity_to_hybrid():
    normalized = normalize_av_translate_inputs({"sync_granularity": "paragraph"})

    assert normalized["sync_granularity"] == "hybrid"

