from decimal import Decimal

from web import store


def test_start_translate_accepts_openrouter_gpt_5_mini(authed_client_no_db, monkeypatch, tmp_path):
    task = store.create("task-start-gpt5-mini", "video.mp4", str(tmp_path), user_id=1)
    store.update("task-start-gpt5-mini", _translate_pre_select=True)

    resume_calls = []
    monkeypatch.setattr(
        "web.routes.task.pipeline_runner.resume",
        lambda task_id, step, user_id=None: resume_calls.append((task_id, step, user_id)),
    )

    resp = authed_client_no_db.post(
        "/api/tasks/task-start-gpt5-mini/start-translate",
        json={"prompt_text": "rewrite it", "model_provider": "gpt_5_mini"},
    )

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "started"
    updated = store.get("task-start-gpt5-mini")
    assert updated["custom_translate_provider"] == "gpt_5_mini"
    assert resume_calls == [("task-start-gpt5-mini", "translate", 1)]


def test_start_translate_accepts_openrouter_gpt_5_5(authed_client_no_db, monkeypatch, tmp_path):
    task = store.create("task-start-gpt5-5", "video.mp4", str(tmp_path), user_id=1)
    store.update("task-start-gpt5-5", _translate_pre_select=True)

    resume_calls = []
    monkeypatch.setattr(
        "web.routes.task.pipeline_runner.resume",
        lambda task_id, step, user_id=None: resume_calls.append((task_id, step, user_id)),
    )

    resp = authed_client_no_db.post(
        "/api/tasks/task-start-gpt5-5/start-translate",
        json={"prompt_text": "rewrite it", "model_provider": "gpt_5_5"},
    )

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "started"
    updated = store.get("task-start-gpt5-5")
    assert updated["custom_translate_provider"] == "gpt_5_5"
    assert resume_calls == [("task-start-gpt5-5", "translate", 1)]


def test_av_resume_clears_stale_error_and_keeps_db_type_translation(authed_client_no_db, monkeypatch, tmp_path):
    task_id = "task-av-resume-clear-error"
    task = store.create(task_id, "video.mp4", str(tmp_path), user_id=1)
    task["steps"].update({"translate": "error", "tts": "pending"})
    store.update(
        task_id,
        pipeline_version="av",
        error="stale interrupted error",
    )
    resume_calls = []
    monkeypatch.setattr(
        "web.services.task_resume.pipeline_runner.resume",
        lambda task_id, step, user_id=None: resume_calls.append((task_id, step, user_id)),
    )
    monkeypatch.setattr("web.routes.task.db_query_one", lambda sql, args: {"id": task_id})
    monkeypatch.setattr("web.services.task_resume.ensure_local_source_video", lambda task_id, task: None)

    resp = authed_client_no_db.post(
        f"/api/tasks/{task_id}/resume",
        json={"start_step": "translate"},
    )

    assert resp.status_code == 200
    updated = store.get(task_id)
    assert updated["status"] == "running"
    assert updated["error"] == ""
    assert updated["type"] == "translation"
    assert updated["steps"]["translate"] == "pending"
    assert resume_calls == [(task_id, "translate", 1)]


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


def test_retranslate_accepts_openrouter_gpt_5_mini(authed_client_no_db, monkeypatch, tmp_path):
    task = store.create("task-retranslate-gpt5-mini", "video.mp4", str(tmp_path), user_id=1)
    task["steps"]["translate"] = "done"
    task["script_segments"] = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
    ]

    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "part one")

    captured = {}

    def _fake_generate(source_full_text_zh, script_segments, variant="normal", **kwargs):
        captured["provider"] = kwargs["provider"]
        return {
            "full_text": "Hook line.",
            "sentences": [{"index": 0, "text": "Hook line.", "source_segment_indices": [0]}],
            "_usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "cost_cny": Decimal("0.123456"),
            },
        }

    monkeypatch.setattr("pipeline.translate.generate_localized_translation", _fake_generate)
    monkeypatch.setattr("pipeline.translate.get_model_display_name", lambda provider, user_id: "openai/gpt-5-mini")

    billing_calls = []
    monkeypatch.setattr("web.routes.task.ai_billing.log_request", lambda **kwargs: billing_calls.append(kwargs))

    resp = authed_client_no_db.post(
        "/api/tasks/task-retranslate-gpt5-mini/retranslate",
        json={"prompt_text": "rewrite it", "model_provider": "gpt_5_mini"},
    )

    assert resp.status_code == 200
    assert captured["provider"] == "gpt_5_mini"
    assert len(billing_calls) == 1
    assert billing_calls[0]["provider"] == "openrouter"
    assert billing_calls[0]["model"] == "openai/gpt-5-mini"


def test_retranslate_accepts_openrouter_gpt_5_5(authed_client_no_db, monkeypatch, tmp_path):
    task = store.create("task-retranslate-gpt5-5", "video.mp4", str(tmp_path), user_id=1)
    task["steps"]["translate"] = "done"
    task["script_segments"] = [
        {"index": 0, "text": "part one", "start_time": 0.0, "end_time": 1.0},
    ]

    monkeypatch.setattr("pipeline.localization.build_source_full_text_zh", lambda segments: "part one")

    captured = {}

    def _fake_generate(source_full_text_zh, script_segments, variant="normal", **kwargs):
        captured["provider"] = kwargs["provider"]
        return {
            "full_text": "Hook line.",
            "sentences": [{"index": 0, "text": "Hook line.", "source_segment_indices": [0]}],
            "_usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "cost_cny": Decimal("0.123456"),
            },
        }

    monkeypatch.setattr("pipeline.translate.generate_localized_translation", _fake_generate)
    monkeypatch.setattr("pipeline.translate.get_model_display_name", lambda provider, user_id: "openai/gpt-5.5")

    billing_calls = []
    monkeypatch.setattr("web.routes.task.ai_billing.log_request", lambda **kwargs: billing_calls.append(kwargs))

    resp = authed_client_no_db.post(
        "/api/tasks/task-retranslate-gpt5-5/retranslate",
        json={"prompt_text": "rewrite it", "model_provider": "gpt_5_5"},
    )

    assert resp.status_code == 200
    assert captured["provider"] == "gpt_5_5"
    assert len(billing_calls) == 1
    assert billing_calls[0]["provider"] == "openrouter"
    assert billing_calls[0]["model"] == "openai/gpt-5.5"


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
