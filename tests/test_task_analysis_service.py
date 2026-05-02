from __future__ import annotations


def test_start_task_analysis_returns_not_found_when_project_row_is_missing():
    from web.services.task_analysis import start_task_analysis

    calls = []

    outcome = start_task_analysis(
        "task-1",
        user_id=7,
        query_one=lambda sql, args: None,
        load_task=lambda task_id: calls.append(("load", task_id)),
        run_analysis=lambda task_id, user_id=None: calls.append(("run", task_id, user_id)),
    )

    assert outcome.not_found is True
    assert outcome.status_code == 404
    assert calls == []


def test_start_task_analysis_returns_busy_when_analysis_step_is_running():
    from web.services.task_analysis import start_task_analysis

    calls = []

    outcome = start_task_analysis(
        "task-1",
        user_id=7,
        query_one=lambda sql, args: {"id": "task-1"},
        load_task=lambda task_id: {"steps": {"analysis": "running"}},
        run_analysis=lambda task_id, user_id=None: calls.append(("run", task_id, user_id)),
    )

    assert outcome.not_found is False
    assert outcome.status_code == 409
    assert "正在运行" in outcome.payload["error"]
    assert calls == []


def test_start_task_analysis_starts_runner_for_owned_task():
    from web.services.task_analysis import start_task_analysis

    calls = []

    def query_one(sql, args):
        calls.append(("query", sql, args))
        return {"id": "task-1"}

    outcome = start_task_analysis(
        "task-1",
        user_id=7,
        query_one=query_one,
        load_task=lambda task_id: {"steps": {"analysis": "done"}},
        run_analysis=lambda task_id, user_id=None: calls.append(("run", task_id, user_id)) or True,
    )

    assert outcome.not_found is False
    assert outcome.status_code == 200
    assert outcome.payload == {"status": "started"}
    assert calls == [
        (
            "query",
            "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
            ("task-1", 7),
        ),
        ("run", "task-1", 7),
    ]


def test_start_task_analysis_returns_busy_when_runner_rejects_duplicate_start():
    from web.services.task_analysis import start_task_analysis

    outcome = start_task_analysis(
        "task-1",
        user_id=7,
        query_one=lambda sql, args: {"id": "task-1"},
        load_task=lambda task_id: {"steps": {}},
        run_analysis=lambda task_id, user_id=None: False,
    )

    assert outcome.not_found is False
    assert outcome.status_code == 409
    assert "正在运行" in outcome.payload["error"]
