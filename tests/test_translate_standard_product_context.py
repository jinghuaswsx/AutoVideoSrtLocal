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
