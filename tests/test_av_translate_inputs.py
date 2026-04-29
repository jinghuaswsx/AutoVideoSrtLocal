from __future__ import annotations

from appcore import av_translate_inputs
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


def test_av_target_language_options_filter_to_enabled_media_languages(monkeypatch):
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["en", "de", "sv"])

    options = av_translate_inputs.list_available_av_target_language_options()

    assert [item["code"] for item in options] == ["en", "de", "sv"]
    assert "fi" not in {item["code"] for item in options}


def test_available_av_translate_defaults_use_first_enabled_when_english_disabled(monkeypatch):
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["de", "fr"])

    defaults = av_translate_inputs.build_available_av_translate_inputs()

    assert defaults["target_language"] == "de"
    assert defaults["target_language_name"] == "German"

