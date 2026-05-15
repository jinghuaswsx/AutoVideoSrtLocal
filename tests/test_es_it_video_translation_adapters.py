from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner


def _runner():
    return MultiTranslateRunner(bus=EventBus(), user_id=1)


def test_multi_resolves_es_it_to_dedicated_localization_modules():
    runner = _runner()

    es_adapter = runner._get_language_adapter({"target_lang": "es"})
    it_adapter = runner._get_language_adapter({"target_lang": "it"})

    assert es_adapter.__name__ == "pipeline.localization_es"
    assert it_adapter.__name__ == "pipeline.localization_it"
    assert es_adapter.validate_tts_script.__module__ == "pipeline.localization_es"
    assert it_adapter.validate_tts_script.__module__ == "pipeline.localization_it"


def test_es_it_module_builders_keep_admin_prompt_resolver(monkeypatch):
    import pipeline.localization_es as loc_es
    import pipeline.localization_it as loc_it

    calls = []

    def fake_resolve(slot, lang):
        calls.append((slot, lang))
        return {"content": f"{slot}:{lang}:{{target_words}}:{{direction}}"}

    monkeypatch.setattr(loc_es, "resolve_prompt_config", fake_resolve)
    monkeypatch.setattr(loc_it, "resolve_prompt_config", fake_resolve)

    es_adapter = _runner()._get_language_adapter({"target_lang": "es"})
    it_adapter = _runner()._get_language_adapter({"target_lang": "it"})

    es_adapter.build_tts_script_messages({"full_text": "Hola"})
    es_adapter.build_localized_rewrite_messages(
        "source", {"full_text": "Hola", "sentences": []}, 10, "shrink",
        source_language="en",
    )
    it_adapter.build_tts_script_messages({"full_text": "Ciao"})
    it_adapter.build_localized_rewrite_messages(
        "source", {"full_text": "Ciao", "sentences": []}, 12, "expand",
        source_language="en",
    )

    assert ("base_tts_script", "es") in calls
    assert ("base_rewrite", "es") in calls
    assert ("base_tts_script", "it") in calls
    assert ("base_rewrite", "it") in calls


def test_spanish_validator_restores_missing_inverted_punctuation():
    from pipeline.localization_es import validate_tts_script

    result = validate_tts_script(
        {
            "full_text": "Sabes como funciona?",
            "blocks": [
                {
                    "index": 0,
                    "text": "Sabes como funciona?",
                    "sentence_indices": [0],
                    "source_segment_indices": [0],
                }
            ],
            "subtitle_chunks": [],
        },
        sentences=[
            {
                "index": 0,
                "text": "Sabes como funciona?",
                "source_segment_indices": [0],
            }
        ],
    )

    assert result["full_text"].startswith("\u00bf")
    assert result["blocks"][0]["text"].startswith("\u00bf")


def test_italian_validator_attaches_apostrophe_elisions():
    from pipeline.localization_it import validate_tts_script

    result = validate_tts_script(
        {
            "full_text": "L' amica lo usa.",
            "blocks": [
                {
                    "index": 0,
                    "text": "L' amica lo usa.",
                    "sentence_indices": [0],
                    "source_segment_indices": [0],
                }
            ],
            "subtitle_chunks": [],
        },
        sentences=[
            {
                "index": 0,
                "text": "L' amica lo usa.",
                "source_segment_indices": [0],
            }
        ],
    )

    assert "L'amica" in result["full_text"]
    assert "L' amica" not in result["full_text"]


def test_omni_es_it_use_module_backed_adapters_with_source_anchored_rewrite(monkeypatch):
    import appcore.runtime_omni as runtime_omni
    import pipeline.localization_es as loc_es
    import pipeline.localization_it as loc_it

    def fake_resolve(slot, lang):
        return {"content": f"{slot}:{lang}:{{target_words}}:{{direction}}"}

    monkeypatch.setattr(runtime_omni, "_resolve_prompt_anchor", fake_resolve)
    monkeypatch.setattr(loc_es, "resolve_prompt_config", fake_resolve)
    monkeypatch.setattr(loc_it, "resolve_prompt_config", fake_resolve)

    runner = runtime_omni.OmniTranslateRunner(bus=EventBus(), user_id=1)
    base_task = {
        "source_language": "es",
        "utterances": [{"text": "texto original del video"}],
    }

    es_adapter = runner._get_localization_module({**base_task, "target_lang": "es"})
    it_adapter = runner._get_localization_module({**base_task, "target_lang": "it"})

    assert es_adapter.__name__ == "pipeline.localization_es"
    assert it_adapter.__name__ == "pipeline.localization_it"
    assert es_adapter.validate_tts_script.__module__ == "pipeline.localization_es"
    assert it_adapter.validate_tts_script.__module__ == "pipeline.localization_it"

    messages = es_adapter.build_localized_rewrite_messages(
        "normalized source",
        {"full_text": "Hola", "sentences": []},
        8,
        "shrink",
        source_language="es",
    )
    assert "ORIGINAL VIDEO TRANSCRIPT" in messages[1]["content"]
    assert "texto original del video" in messages[1]["content"]
