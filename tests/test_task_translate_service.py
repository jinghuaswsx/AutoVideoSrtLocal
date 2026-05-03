from __future__ import annotations

from web.services.task_translate import start_task_translate


class _Runner:
    def __init__(self):
        self.resumed = []

    def resume(self, task_id, step, user_id=None):
        self.resumed.append((task_id, step, user_id))


def test_start_task_translate_saves_choices_and_resumes_pipeline():
    updates = []
    review_steps = []
    runner = _Runner()

    outcome = start_task_translate(
        "task-1",
        {"_translate_pre_select": True},
        {"prompt_text": "rewrite it", "prompt_id": 42, "model_provider": "gpt_5_mini"},
        user_id=7,
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
        set_current_review_step=lambda task_id, step: review_steps.append((task_id, step)),
        resolve_prompt_text=lambda prompt_text, prompt_id, user_id: f"{prompt_text}:{prompt_id}:{user_id}",
        valid_translate_prefs={"openrouter", "gpt_5_mini"},
        runner=runner,
    )

    assert outcome.status_code == 200
    assert outcome.payload == {"status": "started"}
    assert updates == [
        (
            ("task-1",),
            {
                "_translate_pre_select": False,
                "custom_translate_provider": "gpt_5_mini",
                "custom_translate_prompt": "rewrite it:42:7",
            },
        )
    ]
    assert review_steps == [("task-1", "")]
    assert runner.resumed == [("task-1", "translate", 7)]


def test_start_task_translate_rejects_when_not_in_preselect_state():
    runner = _Runner()

    outcome = start_task_translate(
        "task-1",
        {"_translate_pre_select": False},
        {"prompt_text": "rewrite it", "model_provider": "gpt_5_mini"},
        user_id=7,
        update_task=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected update")),
        set_current_review_step=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected review step")
        ),
        resolve_prompt_text=lambda prompt_text, prompt_id, user_id: prompt_text,
        valid_translate_prefs={"gpt_5_mini"},
        runner=runner,
    )

    assert outcome.status_code == 400
    assert "error" in outcome.payload
    assert runner.resumed == []
