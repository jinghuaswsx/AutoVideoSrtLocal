"""pipeline.languages.prompt_defaults DEFAULTS 守护测试。"""
from pipeline.languages.prompt_defaults import DEFAULTS


def test_defaults_contains_three_en_entries():
    assert ("base_translation", "en") in DEFAULTS
    assert ("base_tts_script", "en") in DEFAULTS
    assert ("base_rewrite", "en") in DEFAULTS


def test_en_translation_prompt_targets_en_us_market():
    content = DEFAULTS[("base_translation", "en")]["content"]
    assert "US" in content or "American" in content
    # en-US specific vocabulary anchors
    for token in ("sneakers", "apartment", "elevator"):
        assert token in content
    # forbidden patterns
    assert "link in bio" in content.lower() or "no cta" in content.lower()
    # JSON schema hint
    assert "source_segment_indices" in content


def test_en_tts_script_prompt_mentions_subtitle_chunks():
    content = DEFAULTS[("base_tts_script", "en")]["content"]
    assert "subtitle_chunks" in content
    assert "blocks" in content


def test_en_rewrite_prompt_has_word_count_constraint():
    content = DEFAULTS[("base_rewrite", "en")]["content"]
    assert "{target_words}" in content
    assert "{direction}" in content
    assert "source_segment_indices" in content
