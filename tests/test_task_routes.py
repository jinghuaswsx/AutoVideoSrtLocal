from decimal import Decimal

from web import store


def test_retranslate_logs_ai_billing_on_success(authed_client_no_db, monkeypatch, tmp_path):
    task = store.create("task-retranslate-billing", "video.mp4", str(tmp_path), user_id=1)
    task["steps"]["translate"] = "done"
    task["script_segments"] = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
    ]

    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "part one")
    monkeypatch.setattr(
        "pipeline.translate.generate_localized_translation",
        lambda source_full_text_zh, script_segments, variant="normal", **kwargs: {
            "full_text": "Hook line.",
            "sentences": [{"index": 0, "text": "Hook line.", "source_segment_indices": [0]}],
            "_usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "cost_cny": Decimal("0.123456"),
            },
        },
    )
    monkeypatch.setattr("pipeline.translate.get_model_display_name", lambda provider, user_id: "google/gemini-2.5-flash")

    billing_calls = []
    monkeypatch.setattr("web.routes.task.ai_billing.log_request", lambda **kwargs: billing_calls.append(kwargs))

    resp = authed_client_no_db.post(
        "/api/tasks/task-retranslate-billing/retranslate",
        json={"prompt_text": "rewrite it", "model_provider": "openrouter"},
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["translation"]["full_text"] == "Hook line."
    assert len(billing_calls) == 1
    assert billing_calls[0]["use_case_code"] == "video_translate.localize"
    assert billing_calls[0]["user_id"] == 1
    assert billing_calls[0]["project_id"] == "task-retranslate-billing"
    assert billing_calls[0]["provider"] == "openrouter"
    assert billing_calls[0]["model"] == "google/gemini-2.5-flash"
    assert billing_calls[0]["input_tokens"] == 11
    assert billing_calls[0]["output_tokens"] == 7
    assert billing_calls[0]["response_cost_cny"] == Decimal("0.123456")
    assert billing_calls[0]["success"] is True
    assert billing_calls[0]["extra"] == {"source": "task.retranslate"}


def test_retranslate_logs_ai_billing_on_failure(authed_client_no_db, monkeypatch, tmp_path):
    task = store.create("task-retranslate-failure", "video.mp4", str(tmp_path), user_id=1)
    task["steps"]["translate"] = "done"
    task["script_segments"] = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
    ]

    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "part one")

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("pipeline.translate.generate_localized_translation", _raise)
    monkeypatch.setattr("pipeline.translate.get_model_display_name", lambda provider, user_id: "doubao-seed-1.6")

    billing_calls = []
    monkeypatch.setattr("web.routes.task.ai_billing.log_request", lambda **kwargs: billing_calls.append(kwargs))

    resp = authed_client_no_db.post(
        "/api/tasks/task-retranslate-failure/retranslate",
        json={"prompt_text": "rewrite it", "model_provider": "doubao"},
    )

    assert resp.status_code == 500
    assert len(billing_calls) == 1
    assert billing_calls[0]["use_case_code"] == "video_translate.localize"
    assert billing_calls[0]["provider"] == "doubao"
    assert billing_calls[0]["model"] == "doubao-seed-1.6"
    assert billing_calls[0]["success"] is False
    assert billing_calls[0]["extra"]["source"] == "task.retranslate"
    assert "boom" in billing_calls[0]["extra"]["error"]
