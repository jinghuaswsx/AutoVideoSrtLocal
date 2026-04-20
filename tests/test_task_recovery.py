import json

from web.app import create_app


def test_register_and_unregister_active_task():
    from appcore import task_recovery

    task_recovery.unregister_active_task("video_creation", "vc-active")

    task_recovery.register_active_task("video_creation", "vc-active")
    assert task_recovery.is_task_active("video_creation", "vc-active") is True

    task_recovery.unregister_active_task("video_creation", "vc-active")
    assert task_recovery.is_task_active("video_creation", "vc-active") is False


def test_recover_project_state_marks_video_creation_orphan_running_as_error():
    from appcore import task_recovery

    state = {
        "steps": {"generate": "running"},
        "prompt": "demo",
        "result_video_path": "/tmp/generated.mp4",
    }

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="video_creation",
        task_id="vc-orphan",
        state=state,
        active=False,
    )

    assert changed is True
    assert status == "error"
    assert recovered["steps"]["generate"] == "error"
    assert recovered["result_video_path"] == "/tmp/generated.mp4"
    assert "服务重启" in recovered["error"]


def test_recover_project_state_marks_video_review_and_clears_started_at():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="video_review",
        task_id="vr-orphan",
        state={
            "steps": {"review": "running"},
            "review_started_at": 123456,
            "result": None,
        },
        active=False,
    )

    assert changed is True
    assert status == "error"
    assert recovered["steps"]["review"] == "error"
    assert recovered["review_started_at"] is None
    assert "服务重启" in recovered["error"]


def test_recover_project_state_marks_only_running_steps_for_pipeline_tasks():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="translation",
        task_id="tr-orphan",
        state={
            "status": "running",
            "current_review_step": "translate",
            "steps": {
                "extract": "done",
                "translate": "running",
                "tts": "pending",
            },
            "step_messages": {
                "extract": "ok",
                "translate": "working",
                "tts": "",
            },
            "result": {"hard_video": "/tmp/out.mp4"},
        },
        active=False,
    )

    assert changed is True
    assert status == "error"
    assert recovered["steps"]["extract"] == "done"
    assert recovered["steps"]["translate"] == "error"
    assert recovered["steps"]["tts"] == "pending"
    assert recovered["current_review_step"] == ""
    assert recovered["result"]["hard_video"] == "/tmp/out.mp4"
    assert "服务重启" in recovered["step_messages"]["translate"]


def test_recover_project_state_keeps_active_task_running():
    from appcore import task_recovery

    state = {"steps": {"generate": "running"}}

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="video_creation",
        task_id="vc-live",
        state=state,
        active=True,
    )

    assert changed is False
    assert recovered["steps"]["generate"] == "running"
    assert status is None


def test_recover_project_state_marks_interrupted_link_check_as_failed():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="link_check",
        task_id="lc-orphan",
        state={
            "status": "analyzing",
            "summary": {"overall_decision": "running"},
            "progress": {"total": 4, "analyzed": 2},
        },
        active=False,
    )

    assert changed is True
    assert status == "failed"
    assert recovered["status"] == "failed"
    assert recovered["summary"]["overall_decision"] == "unfinished"
    assert "服务重启" in recovered["error"]


def test_recover_all_interrupted_tasks_updates_running_rows(monkeypatch):
    from appcore import task_recovery

    rows = [
        {
            "id": "vc-orphan",
            "type": "video_creation",
            "status": "running",
            "state_json": json.dumps({"steps": {"generate": "running"}}, ensure_ascii=False),
        },
        {
            "id": "vc-live",
            "type": "video_creation",
            "status": "running",
            "state_json": json.dumps({"steps": {"generate": "running"}}, ensure_ascii=False),
        },
    ]
    writes = []

    monkeypatch.setattr(task_recovery, "db_query", lambda sql, args=(): rows)
    monkeypatch.setattr(
        task_recovery,
        "db_execute",
        lambda sql, args=(): writes.append((sql, args)),
    )
    monkeypatch.setattr(
        task_recovery,
        "is_task_active",
        lambda project_type, task_id: task_id == "vc-live",
    )

    recovered = task_recovery.recover_all_interrupted_tasks()

    assert recovered == 1
    assert len(writes) == 1
    assert writes[0][1][1] == "error"
    assert writes[0][1][2] == "vc-orphan"


def test_recover_all_interrupted_tasks_updates_link_check_rows(monkeypatch):
    from appcore import task_recovery

    rows = [
        {
            "id": "lc-orphan",
            "type": "link_check",
            "status": "analyzing",
            "state_json": json.dumps(
                {"status": "analyzing", "summary": {"overall_decision": "running"}},
                ensure_ascii=False,
            ),
        },
    ]
    writes = []

    monkeypatch.setattr(task_recovery, "db_query", lambda sql, args=(): rows)
    monkeypatch.setattr(
        task_recovery,
        "db_execute",
        lambda sql, args=(): writes.append((sql, args)),
    )
    monkeypatch.setattr(task_recovery, "is_task_active", lambda project_type, task_id: False)

    recovered = task_recovery.recover_all_interrupted_tasks()

    assert recovered == 1
    assert len(writes) == 1
    assert writes[0][1][1] == "failed"
    assert writes[0][1][2] == "lc-orphan"


def test_create_app_runs_interrupted_task_recovery(monkeypatch):
    import web.app as web_app

    called = []
    monkeypatch.setattr(web_app, "recover_all_interrupted_tasks", lambda: called.append(True))

    app = create_app()

    assert app
    assert called == [True]
