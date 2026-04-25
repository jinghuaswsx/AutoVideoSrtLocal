"""pipeline.languages.registry 守护测试。"""
import pytest

from pipeline.languages.registry import SUPPORTED_LANGS, get_rules


def test_supported_langs_includes_existing_nine_plus_en():
    assert set(SUPPORTED_LANGS) == {"de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en"}


def test_get_rules_for_en_returns_module_with_required_attrs():
    mod = get_rules("en")
    assert mod.TTS_MODEL_ID == "eleven_multilingual_v2"
    assert mod.TTS_LANGUAGE_CODE == "en"
    assert mod.MAX_CHARS_PER_LINE == 42
    assert "the" in mod.WEAK_STARTERS
    assert mod.post_process_srt("foo\n") == "foo\n"
    assert mod.pre_process("foo") == "foo"


def test_get_rules_unknown_lang_raises():
    with pytest.raises(LookupError):
        get_rules("klingon")
