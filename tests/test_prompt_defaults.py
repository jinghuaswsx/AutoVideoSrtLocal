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


def test_ja_tts_script_mentions_particle_rule():
    """日语 TTS 脚本 prompt 必须显式告诫 LLM：不要把助词放在 chunk 开头。"""
    entry = DEFAULTS[("base_tts_script", "ja")]
    # 必须提到助词（は・が・を…）和行首约束
    assert "助詞" in entry["content"] or "particle" in entry["content"].lower()
