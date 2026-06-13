"""Block1: prompt 出厂默认文本质量断言。
Spec: docs/superpowers/specs/2026-06-12-omni-quality-block1-prompt-correctness-design.md
"""
from pipeline.languages.prompt_defaults import DEFAULTS

ALL_LANGS = ["en", "de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi"]
ACCENT_SENSITIVE = ["es", "it", "pt", "de"]


def _content(slot, lang):
    return DEFAULTS[(slot, lang)]["content"]


def test_no_ascii_only_wording_in_accent_sensitive_translation_prompts():
    for lang in ACCENT_SENSITIVE:
        assert "ASCII punctuation only" not in _content("base_translation", lang), lang


def test_accent_letters_declared_mandatory():
    assert "¿" in _content("base_translation", "es")
    assert "ñ" in _content("base_translation", "es")
    assert "à" in _content("base_translation", "it")
    assert "ã" in _content("base_translation", "pt")
    c_de = _content("base_translation", "de")
    assert ("Eszett" in c_de) or ("ß" in c_de)


def test_en_keeps_ascii_constraint():
    assert "ASCII punctuation only" in _content("base_translation", "en")


def test_all_translation_prompts_have_opening_ending_section():
    for lang in ALL_LANGS:
        assert "OPENING & ENDING" in _content("base_translation", lang), lang


def test_all_rewrite_prompts_have_protection_section():
    for lang in ALL_LANGS:
        assert "OPENING & ENDING PROTECTION" in _content("base_rewrite", lang), lang


def test_generic_template_is_source_language_neutral():
    for lang in ["nl", "sv", "fi"]:
        assert "English script" not in _content("base_translation", lang), lang


def test_cta_guidance_preserves_source_cta_intent():
    for lang in ALL_LANGS:
        for slot in ("base_translation", "base_rewrite"):
            content = _content(slot, lang)
            lowered = content.lower()
            collapsed = " ".join(lowered.split())
            assert "no cta" not in lowered, (slot, lang)
            assert "no cta at the end" not in lowered, (slot, lang)
            assert "source cta" in collapsed or "source ends with a cta" in collapsed, (
                slot,
                lang,
            )
