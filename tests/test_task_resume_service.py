from __future__ import annotations


def test_resume_task_from_step_does_not_recover_when_project_row_is_missing():
    from web.services.task_resume import resume_task_from_step

    calls = []

    outcome = resume_task_from_step(
        "task-1",
        user_id=7,
        start_step="translate",
        resumable_steps=["extract", "translate", "tts"],
        query_one=lambda sql, args: None,
        recover_task=lambda task_id: calls.append(("recover", task_id)),
        load_task=lambda task_id: calls.append(("load", task_id)),
        resume_runner=lambda task_id, step, user_id=None: calls.append(("resume", task_id, step, user_id)),
    )

    assert outcome.not_found is True
    assert outcome.status_code == 404
    assert calls == []


def test_resume_task_from_step_rejects_invalid_start_step_before_state_mutation():
    from web.services.task_resume import resume_task_from_step

    calls = []

    outcome = resume_task_from_step(
        "task-1",
        user_id=7,
        start_step="missing",
        resumable_steps=["extract", "translate", "tts"],
        query_one=lambda sql, args: {"id": "task-1"},
        recover_task=lambda task_id: calls.append(("recover", task_id)),
        load_task=lambda task_id: {"steps": {"translate": "done"}},
        set_step=lambda *args: calls.append(("set_step", args)),
        update_task=lambda *args, **kwargs: calls.append(("update", args, kwargs)),
    )

    assert outcome.not_found is False
    assert outcome.status_code == 400
    assert "start_step must be one of" in outcome.payload["error"]
    assert calls == [("recover", "task-1")]


def test_resume_task_from_step_resets_selected_steps_and_starts_runner():
    from web.services.task_resume import resume_task_from_step

    calls = []
    task = {
        "id": "task-1",
        "pipeline_version": "av",
        "steps": {"extract": "done", "translate": "error", "tts": "pending"},
    }

    outcome = resume_task_from_step(
        "task-1",
        user_id=7,
        start_step="translate",
        resumable_steps=["extract", "translate", "tts"],
        query_one=lambda sql, args: calls.append(("query", sql, args)) or {"id": "task-1"},
        recover_task=lambda task_id: calls.append(("recover", task_id)),
        load_task=lambda task_id: task,
        refresh_task=lambda task_id, current_task: calls.append(("refresh", task_id, current_task)) or current_task,
        ensure_source=lambda task_id, current_task: calls.append(("ensure", task_id, current_task)),
        resume_runner=lambda task_id, step, user_id=None: calls.append(("resume", task_id, step, user_id)),
        set_step=lambda task_id, step, status: calls.append(("set_step", task_id, step, status)),
        set_step_message=lambda task_id, step, message: calls.append(("set_step_message", task_id, step, message)),
        update_task=lambda task_id, **updates: calls.append(("update", task_id, updates)),
    )

    assert outcome.not_found is False
    assert outcome.status_code == 200
    assert outcome.payload == {"status": "started", "start_step": "translate"}
    assert calls == [
        (
            "query",
            "SELECT id, user_id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
            ("task-1", 7),
        ),
        ("recover", "task-1"),
        ("set_step", "task-1", "translate", "pending"),
        ("set_step_message", "task-1", "translate", "等待中..."),
        ("set_step", "task-1", "tts", "pending"),
        ("set_step_message", "task-1", "tts", "等待中..."),
        ("update", "task-1", {"status": "running", "error": "", "current_review_step": "", "type": "translation"}),
        ("refresh", "task-1", task),
        ("ensure", "task-1", task),
        ("resume", "task-1", "translate", 7),
    ]


def test_resume_task_from_step_returns_conflict_when_source_video_is_missing():
    from web.services.task_resume import resume_task_from_step

    def ensure_source(task_id, task):
        raise FileNotFoundError("source missing")

    outcome = resume_task_from_step(
        "task-1",
        user_id=7,
        start_step="translate",
        resumable_steps=["extract", "translate"],
        query_one=lambda sql, args: {"id": "task-1"},
        load_task=lambda task_id: {"steps": {}, "pipeline_version": "av"},
        refresh_task=lambda task_id, task: task,
        ensure_source=ensure_source,
        resume_runner=lambda task_id, step, user_id=None: None,
    )

    assert outcome.not_found is False
    assert outcome.status_code == 409
    assert outcome.payload == {"error": "source missing"}
