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
