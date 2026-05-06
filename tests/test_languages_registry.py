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
    assert "Bonjour ?" in out


def test_get_rules_unknown_raises():
    with pytest.raises(LookupError):
        registry.get_rules("xx")


# ── Batch 2 扩展 ──────────────────────────────────────

def test_supported_langs_includes_batch2():
    for lang in ("es", "it", "pt"):
        assert lang in registry.SUPPORTED_LANGS


def test_get_rules_es_has_inverted_punct_post_process():
    rules = registry.get_rules("es")
    assert rules.TTS_LANGUAGE_CODE == "es"
    assert rules.MAX_CHARS_PER_LINE == 42
    sample = "1\n00:00:00,000 --> 00:00:01,000\nSabes cómo funciona?\n"
    out = rules.post_process_srt(sample)
    assert "¿Sabes cómo funciona?" in out
    # 已有倒问号时不重复
    sample2 = "1\n00:00:00,000 --> 00:00:01,000\n¿Ya existe?\n"
    out2 = rules.post_process_srt(sample2)
    assert out2.count("¿") == 1


def test_get_rules_it_and_pt_basic():
    for lang in ("it", "pt"):
        rules = registry.get_rules(lang)
        assert rules.TTS_LANGUAGE_CODE == lang
        assert rules.MAX_CHARS_PER_LINE == 42
        assert rules.MAX_CHARS_PER_SECOND == 17
        sample = "1\n00:00:00,000 --> 00:00:01,000\nTest text.\n"
        assert rules.post_process_srt(sample) == sample


# ── Batch 3：日语 ──────────────────────────────────────

def test_supported_langs_includes_ja():
    assert "ja" in registry.SUPPORTED_LANGS


def test_supported_langs_includes_nl_sv_fi():
    for lang in ("nl", "sv", "fi"):
        assert lang in registry.SUPPORTED_LANGS
        rules = registry.get_rules(lang)
        assert rules.TTS_LANGUAGE_CODE == lang
        assert rules.TTS_MODEL_ID == "eleven_multilingual_v2"
        assert rules.MAX_LINES == 2


def test_get_rules_ja_has_full_width_line_width_and_slower_cps():
    rules = registry.get_rules("ja")
    assert rules.TTS_LANGUAGE_CODE == "ja"
    # 日语按全角字符计，行宽比拉丁语族小很多
    assert rules.MAX_CHARS_PER_LINE == 21
    # 日语阅读速度比拉丁语族慢
    assert rules.MAX_CHARS_PER_SECOND == 13
    # 助词在 WEAK_STARTERS 中，不应作为行首
    for particle in ("は", "が", "を", "に", "で", "と", "の", "も"):
        assert particle in rules.WEAK_STARTERS, f"{particle} should be weak starter"
    # 无特殊后处理
    sample = "1\n00:00:00,000 --> 00:00:01,000\n日本語のテスト\n"
    assert rules.post_process_srt(sample) == sample


# ── Batch 5：英语 ──────────────────────────────────────

def test_supported_langs_includes_en():
    assert "en" in registry.SUPPORTED_LANGS


def test_supported_langs_full_set_after_en():
    assert set(registry.SUPPORTED_LANGS) == {"de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en"}


def test_source_langs_add_zh_to_supported_targets_in_manual_order():
    assert registry.SOURCE_LANGS == (
        "zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi",
    )


def test_normalize_enabled_target_langs_filters_unknown_and_forces_en_tail():
    assert registry.normalize_enabled_target_langs(["de", "ru", "ja"]) == ("de", "ja", "en")
    assert registry.normalize_enabled_target_langs(["en", "fr"]) == ("fr", "en")
    assert registry.normalize_enabled_target_langs([]) == registry.SUPPORTED_LANGS


def test_get_rules_en_returns_module_with_required_attrs():
    rules = registry.get_rules("en")
    assert rules.TTS_MODEL_ID == "eleven_multilingual_v2"
    assert rules.TTS_LANGUAGE_CODE == "en"
    assert rules.MAX_CHARS_PER_LINE == 42
    assert rules.MAX_CHARS_PER_SECOND == 17
    assert rules.MAX_LINES == 2
    assert "the" in rules.WEAK_STARTERS
    assert rules.post_process_srt("foo\n") == "foo\n"
    assert rules.pre_process("foo") == "foo"
