from __future__ import annotations

from decimal import Decimal

from web.services.task_retranslate import retranslate_task


def test_retranslate_task_generates_translation_logs_billing_and_keeps_last_three_history():
    billing_calls = []
    updates = []
    generated = {}

    def generate_translation(source_full_text_zh, script_segments, variant="normal", **kwargs):
        generated.update(
            {
                "source_full_text_zh": source_full_text_zh,
                "script_segments": script_segments,
                "variant": variant,
                "kwargs": kwargs,
            }
        )
        return {
            "full_text": "localized",
            "sentences": [{"index": 0, "text": "localized"}],
            "_usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "cost_cny": Decimal("0.123456"),
            },
        }

    outcome = retranslate_task(
        "task-1",
        {
            "steps": {"translate": "done"},
            "script_segments": [{"index": 0, "text": "source"}],
            "translation_history": [
                {"result": {"full_text": "old-1"}},
                {"result": {"full_text": "old-2"}},
                {"result": {"full_text": "old-3"}},
            ],
        },
        {"prompt_text": "rewrite", "prompt_id": 9, "model_provider": "gpt_5_mini"},
        user_id=7,
        resolve_prompt_text=lambda prompt_text, prompt_id, user_id: f"{prompt_text}:{prompt_id}:{user_id}",
        valid_translate_prefs={"openrouter", "gpt_5_mini"},
        build_source_full_text=lambda segments: "source full text",
        generate_translation=generate_translation,
        get_model_display_name=lambda provider, user_id: "openai/gpt-5-mini",
        log_ai_request=lambda **kwargs: billing_calls.append(kwargs),
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
    )

    assert outcome.status_code == 200
    assert outcome.payload["translation"]["full_text"] == "localized"
    assert outcome.payload["history_index"] == 2
    assert [item["result"]["full_text"] for item in outcome.payload["translation_history"]] == [
        "old-2",
        "old-3",
        "localized",
    ]
    assert generated["source_full_text_zh"] == "source full text"
    assert generated["variant"] == "normal"
    assert generated["kwargs"]["custom_system_prompt"] == "rewrite:9:7"
    assert generated["kwargs"]["provider"] == "gpt_5_mini"
    assert generated["kwargs"]["user_id"] == 7
    assert generated["kwargs"]["use_case"] == "video_translate.localize"
    assert generated["kwargs"]["project_id"] == "task-1"
    assert billing_calls[0]["provider"] == "openrouter"
    assert billing_calls[0]["model"] == "openai/gpt-5-mini"
    assert billing_calls[0]["input_tokens"] == 11
    assert billing_calls[0]["output_tokens"] == 7
    assert billing_calls[0]["response_cost_cny"] == Decimal("0.123456")
    assert billing_calls[0]["success"] is True
    assert updates == [(("task-1",), {"translation_history": outcome.payload["translation_history"]})]


def test_retranslate_task_logs_failure_without_updating_history():
    billing_calls = []
    updates = []

    def generate_translation(*args, **kwargs):
        raise RuntimeError("boom")

    outcome = retranslate_task(
        "task-1",
        {"steps": {"translate": "done"}, "script_segments": [{"index": 0, "text": "source"}]},
        {"prompt_text": "rewrite", "model_provider": "doubao"},
        user_id=7,
        resolve_prompt_text=lambda prompt_text, prompt_id, user_id: prompt_text,
        valid_translate_prefs={"doubao"},
        build_source_full_text=lambda segments: "source full text",
        generate_translation=generate_translation,
        get_model_display_name=lambda provider, user_id: "doubao-seed-1.6",
        log_ai_request=lambda **kwargs: billing_calls.append(kwargs),
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
    )

    assert outcome.status_code == 500
    assert "boom" in outcome.payload["error"]
    assert billing_calls[0]["provider"] == "doubao"
    assert billing_calls[0]["model"] == "doubao-seed-1.6"
    assert billing_calls[0]["success"] is False
    assert "boom" in billing_calls[0]["extra"]["error"]
    assert updates == []
