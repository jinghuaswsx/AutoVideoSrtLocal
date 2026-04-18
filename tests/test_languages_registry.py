import pytest

from pipeline.languages import registry


def test_supported_langs_includes_de_fr():
    assert "de" in registry.SUPPORTED_LANGS
    assert "fr" in registry.SUPPORTED_LANGS


def test_get_rules_de():
    rules = registry.get_rules("de")
    assert rules.TTS_LANGUAGE_CODE == "de"
    assert rules.TTS_MODEL_ID == "eleven_multilingual_v2"
    assert rules.MAX_CHARS_PER_LINE == 38
    assert rules.MAX_CHARS_PER_SECOND == 17
    assert "und" in rules.WEAK_STARTERS


def test_get_rules_fr_has_post_process():
    rules = registry.get_rules("fr")
    assert rules.TTS_LANGUAGE_CODE == "fr"
    assert rules.MAX_CHARS_PER_LINE == 42
    assert callable(rules.post_process_srt)
    sample = "1\n00:00:00,000 --> 00:00:01,000\nBonjour ?\n"
    out = rules.post_process_srt(sample)
    assert "Bonjour\u00A0?" in out


def test_get_rules_unknown_raises():
    with pytest.raises(LookupError):
        registry.get_rules("xx")
