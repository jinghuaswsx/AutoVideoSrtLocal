from pipeline.languages.prompt_defaults import DEFAULTS


def test_defaults_cover_de_and_fr_base_slots():
    for slot in ("base_translation", "base_tts_script", "base_rewrite"):
        assert (slot, "de") in DEFAULTS, f"missing de {slot}"
        assert (slot, "fr") in DEFAULTS, f"missing fr {slot}"


def test_ecommerce_plugin_shared():
    assert ("ecommerce_plugin", None) in DEFAULTS
    entry = DEFAULTS[("ecommerce_plugin", None)]
    assert "Facebook" in entry["content"] or "short-form commerce" in entry["content"]


def test_each_entry_has_provider_model_content():
    for key, entry in DEFAULTS.items():
        assert "provider" in entry
        assert "model" in entry
        assert "content" in entry and entry["content"].strip()


def test_defaults_cover_batch2_langs():
    for lang in ("es", "it", "pt"):
        for slot in ("base_translation", "base_tts_script", "base_rewrite"):
            assert (slot, lang) in DEFAULTS, f"missing {lang} {slot}"


def test_es_translation_mentions_inverted_punct():
    entry = DEFAULTS[("base_translation", "es")]
    assert "¿" in entry["content"] and "¡" in entry["content"]


def test_defaults_cover_ja():
    for slot in ("base_translation", "base_tts_script", "base_rewrite"):
        assert (slot, "ja") in DEFAULTS, f"missing ja {slot}"


def test_defaults_cover_nl_sv_fi():
    for lang in ("nl", "sv", "fi"):
        for slot in ("base_translation", "base_tts_script", "base_rewrite"):
            assert (slot, lang) in DEFAULTS, f"missing {lang} {slot}"


def test_ja_tts_script_mentions_particle_rule():
    """日语 TTS 脚本 prompt 必须显式告诫 LLM：不要把助词放在 chunk 开头。"""
    entry = DEFAULTS[("base_tts_script", "ja")]
    # 必须提到助词（は・が・を…）和行首约束
    assert "助詞" in entry["content"] or "particle" in entry["content"].lower()


# ── Batch 5：英语（en-US）──────────────────────────────

def test_defaults_cover_en():
    for slot in ("base_translation", "base_tts_script", "base_rewrite"):
        assert (slot, "en") in DEFAULTS, f"missing en {slot}"


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
    # en-US specific: contractions count as one word
    assert "contractions" in content.lower() or "you'll" in content
