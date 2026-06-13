class _FakeTaskState:
    def __init__(self, task):
        self.task = dict(task)

    def get(self, task_id):
        return dict(self.task)

    def update(self, task_id, **fields):
        self.task.update(fields)

    def add_llm_debug_ref(self, *args, **kwargs):
        pass

    def set_artifact(self, *args, **kwargs):
        pass

    def set_current_review_step(self, *args, **kwargs):
        pass


class _FakeRunner:
    user_id = 7

    def _complete_original_video_passthrough(self, *args, **kwargs):
        return False

    def _resolve_target_lang(self, task):
        return task.get("target_lang") or "de"

    def _build_system_prompt(self, lang):
        return f"BASE PROMPT {lang}"

    def _set_step(self, *args, **kwargs):
        pass

    def _emit(self, *args, **kwargs):
        pass


def _run_step(monkeypatch, tmp_path, *, product_context=None, source_anchored=True):
    from appcore import runtime_omni_steps as steps

    task = {
        "task_dir": str(tmp_path),
        "video_path": str(tmp_path / "source.mp4"),
        "target_lang": "de",
        "source_language": "en",
        "script_segments": [{"index": 0, "text": "This is the ice mold."}],
        "utterances": [],
    }
    if product_context is not None:
        task["product_context"] = product_context

    captured = {}

    def fake_generate(source, segs, **kwargs):
        captured["prompt"] = kwargs["custom_system_prompt"]
        return {
            "full_text": "Das ist die Form.",
            "sentences": [
                {"index": 0, "text": "Das ist die Form.", "source_segment_indices": [0]}
            ],
        }

    monkeypatch.setattr(steps, "task_state", _FakeTaskState(task))
    monkeypatch.setattr(steps, "_resolve_translate_use_case_binding", lambda use_case: ("provider", "model"))
    monkeypatch.setattr(steps, "_ensure_source_transcript_is_actionable", lambda **kwargs: None)
    monkeypatch.setattr(steps, "generate_localized_translation", fake_generate)
    monkeypatch.setattr(steps, "_save_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(steps, "_llm_request_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(steps, "_llm_response_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(steps, "_log_translate_billing", lambda *args, **kwargs: None)
    monkeypatch.setattr(steps, "_build_review_segments", lambda script, localized: [])
    monkeypatch.setattr(steps, "build_asr_artifact", lambda *args, **kwargs: {})
    monkeypatch.setattr(steps, "build_translate_artifact", lambda *args, **kwargs: {})

    from pipeline import extract

    monkeypatch.setattr(extract, "get_video_duration", lambda path: 10.0)

    steps.step_translate_standard(_FakeRunner(), "task-1", source_anchored=source_anchored)
    return captured["prompt"]


def test_system_prompt_carries_product_context(monkeypatch, tmp_path):
    prompt = _run_step(
        monkeypatch,
        tmp_path,
        product_context={"name": "Ice Ball Mold", "name_target_lang": "Eisball-Form"},
        source_anchored=True,
    )

    assert "PRODUCT CONTEXT" in prompt
    assert "Eisball-Form" in prompt
    assert prompt.index("INPUT NOTICE") < prompt.index("PRODUCT CONTEXT")


def test_no_context_prompt_unchanged(monkeypatch, tmp_path):
    prompt = _run_step(monkeypatch, tmp_path, product_context=None, source_anchored=False)

    assert prompt == "BASE PROMPT de"
    assert "PRODUCT CONTEXT" not in prompt


def test_rewrite_messages_carry_product_context(monkeypatch):
    from appcore import runtime_omni

    monkeypatch.setattr(
        runtime_omni,
        "_resolve_prompt_anchor",
        lambda slot, lang: {"content": "Rewrite to {target_words} words and {direction}."},
    )
    adapter = runtime_omni.OmniLocalizationAdapter(
        lang="de",
        source_language="en",
        original_asr_text="This mold makes clear ice.",
        product_context={"name": "Ice Ball Mold", "name_target_lang": "Eisball-Form"},
    )

    messages = adapter.build_localized_rewrite_messages(
        "normalized source",
        {"full_text": "Localized text", "sentences": []},
        8,
        "shrink",
        source_language="en",
    )
    user_content = messages[1]["content"]

    assert "PRODUCT CONTEXT" in user_content
    assert "Eisball-Form" in user_content
    assert user_content.index("PRODUCT CONTEXT") < user_content.index("ORIGINAL VIDEO TRANSCRIPT")


def test_rewrite_messages_without_context_keep_existing_shape(monkeypatch):
    from appcore import runtime_omni

    monkeypatch.setattr(
        runtime_omni,
        "_resolve_prompt_anchor",
        lambda slot, lang: {"content": "Rewrite to {target_words} words and {direction}."},
    )
    adapter = runtime_omni.OmniLocalizationAdapter(
        lang="de",
        source_language="en",
        original_asr_text="This mold makes clear ice.",
    )

    messages = adapter.build_localized_rewrite_messages(
        "normalized source",
        {"full_text": "Localized text", "sentences": []},
        8,
        "shrink",
        source_language="en",
    )
    user_content = messages[1]["content"]

    assert "PRODUCT CONTEXT" not in user_content
    assert user_content.startswith(
        "ORIGINAL VIDEO TRANSCRIPT (English, ground truth — what the video actually says):"
    )


def test_runner_passes_product_context_to_module_backed_rewrite_adapter(monkeypatch):
    from appcore import runtime_omni
    from appcore.events import EventBus
    import pipeline.localization_es as loc_es

    monkeypatch.setattr(
        loc_es,
        "resolve_prompt_config",
        lambda slot, lang: {"content": "Rewrite to {target_words} words and {direction}."},
    )
    runner = runtime_omni.OmniTranslateRunner(bus=EventBus(), user_id=1)
    adapter = runner._get_localization_module({
        "target_lang": "es",
        "source_language": "en",
        "utterances": [{"text": "This mold makes clear ice."}],
        "product_context": {"name": "Ice Ball Mold", "name_target_lang": "Molde de hielo"},
    })

    messages = adapter.build_localized_rewrite_messages(
        "normalized source",
        {"full_text": "Texto localizado", "sentences": []},
        8,
        "shrink",
        source_language="en",
    )
    user_content = messages[1]["content"]

    assert "PRODUCT CONTEXT" in user_content
    assert "Molde de hielo" in user_content
    assert user_content.index("PRODUCT CONTEXT") < user_content.index("ORIGINAL VIDEO TRANSCRIPT")


def test_japanese_duration_rewrite_receives_product_context(monkeypatch):
    from appcore import runtime_omni
    from pipeline import ja_translate

    captured = {}

    def fake_rewrite_ja_localized_translation(**kwargs):
        captured.update(kwargs)
        return {"full_text": "帽子キーパーです。", "sentences": []}

    monkeypatch.setattr(
        ja_translate,
        "rewrite_ja_localized_translation",
        fake_rewrite_ja_localized_translation,
    )
    adapter = runtime_omni.OmniJapaneseLocalizationAdapter(
        source_language="en",
        original_asr_text="This hat keeper saves space.",
        product_context={"name": "Hat Keeper", "name_target_lang": "帽子キーパー"},
    )

    adapter.generate_duration_rewrite(
        source_full_text="This hat keeper saves space.",
        prev_localized_translation={"full_text": "帽子収納に便利です。", "sentences": []},
        target_units=16,
        direction="expand",
        source_language="en",
        script_segments=[
            {"index": 0, "start_time": 0.0, "end_time": 2.0, "text": "Great for hats."},
        ],
        last_audio_duration=1.2,
        video_duration=2.0,
        user_id=1,
        project_id="task-ja",
        temperature=0.2,
        feedback_notes=None,
    )

    assert captured["product_context"] == {
        "name": "Hat Keeper",
        "name_target_lang": "帽子キーパー",
    }
