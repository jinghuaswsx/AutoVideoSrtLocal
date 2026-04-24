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


def test_recover_project_state_keeps_waiting_ja_translate_review_state():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="ja_translate",
        task_id="ja-waiting",
        state={
            "status": "uploaded",
            "current_review_step": "voice_match",
            "steps": {
                "extract": "done",
                "asr": "done",
                "voice_match": "waiting",
                "alignment": "pending",
            },
            "step_messages": {
                "voice_match": "pick voice",
            },
        },
        active=False,
    )

    assert changed is False
    assert status is None
    assert recovered["status"] == "uploaded"
    assert recovered["current_review_step"] == "voice_match"
    assert recovered["steps"]["voice_match"] == "waiting"


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


def test_recover_project_state_marks_orphaned_link_check_as_failed():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        "link_check",
        "lc-orphan",
        {
            "status": "analyzing",
            "steps": {
                "lock_locale": "done",
                "download": "done",
                "analyze": "running",
                "summarize": "pending",
            },
            "items": [{"id": "site-1", "status": "done"}],
        },
        active=False,
    )

    assert changed is True
    assert status == "failed"
    assert recovered["items"][0]["id"] == "site-1"
    assert recovered["steps"]["analyze"] == "error"
    assert "服务重启" in recovered["error"]


def test_recover_project_state_marks_running_link_check_summary_unfinished():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        "link_check",
        "lc-summary",
        {
            "status": "analyzing",
            "summary": {"overall_decision": "running"},
            "steps": {"analyze": "running"},
        },
        active=False,
    )

    assert changed is True
    assert status == "failed"
    assert recovered["summary"]["overall_decision"] == "unfinished"


def test_recover_project_state_keeps_active_link_check_running():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        "link_check",
        "lc-live",
        {
            "status": "analyzing",
            "steps": {
                "lock_locale": "done",
                "download": "done",
                "analyze": "running",
                "summarize": "pending",
            },
        },
        active=True,
    )

    assert changed is False
    assert status is None
    assert recovered["status"] == "analyzing"
    assert recovered["steps"]["analyze"] == "running"


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


def test_recover_all_interrupted_tasks_updates_running_link_check_rows(monkeypatch):
    from appcore import task_recovery

    row = {
        "id": "lc-boot",
        "type": "link_check",
        "status": "analyzing",
        "state_json": json.dumps(
            {
                "status": "analyzing",
                "steps": {"analyze": "running"},
                "items": [{"id": "site-1", "status": "done"}],
            },
            ensure_ascii=False,
        ),
    }
    persisted = []

    def fake_db_query(sql, args=()):
        if "'link_check'" in sql and "'analyzing'" in sql:
            return [row]
        return []

    monkeypatch.setattr(task_recovery, "db_query", fake_db_query)
    monkeypatch.setattr(task_recovery, "is_task_active", lambda project_type, task_id: False)
    monkeypatch.setattr(
        task_recovery,
        "_persist_project_recovery",
        lambda task_id, recovered, status: persisted.append((task_id, recovered, status)),
    )

    recovered = task_recovery.recover_all_interrupted_tasks()

    assert recovered == 1
    assert persisted[0][0] == "lc-boot"
    assert persisted[0][2] == "failed"
    assert persisted[0][1]["steps"]["analyze"] == "error"


def test_recover_all_interrupted_tasks_skips_active_link_check_rows(monkeypatch):
    from appcore import task_recovery

    row = {
        "id": "lc-live",
        "type": "link_check",
        "status": "analyzing",
        "state_json": json.dumps(
            {
                "status": "analyzing",
                "steps": {"analyze": "running"},
            },
            ensure_ascii=False,
        ),
    }
    persisted = []

    monkeypatch.setattr(task_recovery, "db_query", lambda sql, args=(): [row])
    monkeypatch.setattr(task_recovery, "is_task_active", lambda project_type, task_id: True)
    monkeypatch.setattr(
        task_recovery,
        "_persist_project_recovery",
        lambda task_id, recovered, status: persisted.append((task_id, recovered, status)),
    )

    recovered = task_recovery.recover_all_interrupted_tasks()

    assert recovered == 0
    assert persisted == []


def test_recover_project_state_marks_image_translate_running_as_interrupted():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="image_translate",
        task_id="it-orphan",
        state={
            "type": "image_translate",
            "status": "running",
            "steps": {"process": "running"},
            "items": [
                {"idx": 0, "status": "done"},
                {"idx": 1, "status": "running", "attempts": 2, "error": "mid-flight"},
                {"idx": 2, "status": "pending"},
                {"idx": 3, "status": "failed", "error": "boom"},
            ],
        },
        active=False,
    )

    assert changed is True
    assert status == "interrupted"
    assert recovered["status"] == "interrupted"
    assert recovered["steps"]["process"] == "interrupted"
    # item 级 running 必须退回 pending 才能被 retry-unfinished 拣起
    assert recovered["items"][1]["status"] == "pending"
    assert recovered["items"][1]["attempts"] == 0
    assert recovered["items"][1]["error"] == ""
    # 其他状态原样保留
    assert recovered["items"][0]["status"] == "done"
    assert recovered["items"][2]["status"] == "pending"
    assert recovered["items"][3]["status"] == "failed"
    assert recovered["progress"] == {"total": 4, "done": 1, "failed": 1, "running": 0}
    assert "中断" in recovered["error"]


def test_recover_project_state_marks_queued_image_translate_as_interrupted():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="image_translate",
        task_id="it-queued",
        state={
            "type": "image_translate",
            "status": "queued",
            "items": [{"idx": 0, "status": "pending"}],
        },
        active=False,
    )

    assert changed is True
    assert status == "interrupted"
    assert recovered["status"] == "interrupted"
    assert recovered["progress"]["total"] == 1


def test_recover_project_state_auto_resumes_image_translate_with_recent_apimart_task():
    """刚提交的 APIMART 任务（窗口内）应保持 running 并自动拉起 worker 继续轮询。"""
    import time
    from appcore import task_recovery

    now = time.time()
    changed, recovered, status = task_recovery.recover_project_state(
        project_type="image_translate",
        task_id="it-resumable",
        state={
            "type": "image_translate",
            "status": "running",
            "steps": {"process": "running"},
            "items": [
                {"idx": 0, "status": "done"},
                {
                    "idx": 1,
                    "status": "running",
                    "attempts": 1,
                    "apimart_task_id": "task_abc",
                    "apimart_submitted_at": now - 60,  # 提交 60 秒前
                },
                {"idx": 2, "status": "pending"},
            ],
        },
        active=False,
    )

    assert changed is True
    assert status == "running"
    assert recovered["status"] == "running"
    # running item 不能被退回 pending，否则会走重新提交路径
    assert recovered["items"][1]["status"] == "running"
    assert recovered["items"][1]["apimart_task_id"] == "task_abc"
    assert recovered["error"] == ""
    assert recovered["progress"]["running"] == 1


def test_recover_project_state_expired_apimart_task_falls_back_to_interrupted():
    """提交已超过 10 分钟的 APIMART 任务不再自动恢复，走默认中断路径。"""
    import time
    from appcore import task_recovery

    now = time.time()
    changed, recovered, status = task_recovery.recover_project_state(
        project_type="image_translate",
        task_id="it-expired",
        state={
            "type": "image_translate",
            "status": "running",
            "items": [
                {
                    "idx": 0,
                    "status": "running",
                    "apimart_task_id": "task_old",
                    "apimart_submitted_at": now - 3600,  # 1 小时前提交
                },
            ],
        },
        active=False,
    )

    assert changed is True
    assert status == "interrupted"
    assert recovered["status"] == "interrupted"
    # 过期任务 running 退回 pending
    assert recovered["items"][0]["status"] == "pending"


def test_recover_all_interrupted_tasks_auto_starts_resumable_image_translate():
    """recover_all_interrupted_tasks 对 running 状态的 image_translate 任务
    自动调用 start_image_translate_runner 继续处理。"""
    import time
    import json as _json
    from unittest.mock import patch
    from appcore import task_recovery

    now = time.time()
    rows = [
        {
            "id": "t-resumable",
            "type": "image_translate",
            "status": "running",
            "state_json": _json.dumps({
                "type": "image_translate",
                "status": "running",
                "_user_id": 42,
                "items": [
                    {
                        "idx": 0,
                        "status": "running",
                        "apimart_task_id": "task_keep",
                        "apimart_submitted_at": now - 30,
                    },
                ],
            }),
        },
    ]
    with patch.object(task_recovery, "db_query", return_value=rows), \
         patch.object(task_recovery, "_persist_project_recovery"), \
         patch("web.routes.image_translate.start_image_translate_runner", return_value=True) as m_start:
        count = task_recovery.recover_all_interrupted_tasks()

    assert count == 1
    m_start.assert_called_once_with("t-resumable", 42)


def test_recover_project_state_marks_subtitle_removal_as_interrupted():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="subtitle_removal",
        task_id="sr-orphan",
        state={
            "type": "subtitle_removal",
            "status": "running",
            "steps": {
                "prepare": "done",
                "submit": "done",
                "poll": "running",
                "download_result": "pending",
            },
        },
        active=False,
    )

    assert changed is True
    assert status == "interrupted"
    assert recovered["status"] == "interrupted"
    assert recovered["steps"]["poll"] == "interrupted"
    assert "中断" in recovered["error"]


def test_recover_project_state_marks_translate_lab_running_as_interrupted():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="translate_lab",
        task_id="lab-orphan",
        state={
            "type": "translate_lab",
            "status": "running",
            "current_review_step": "tts",
            "steps": {
                "extract": "done",
                "tts": "running",
                "subtitle": "pending",
            },
        },
        active=False,
    )

    assert changed is True
    assert status == "interrupted"
    assert recovered["status"] == "interrupted"
    assert recovered["current_review_step"] == ""
    assert recovered["steps"]["tts"] == "interrupted"
    assert recovered["steps"]["subtitle"] == "pending"


def test_recover_project_state_keeps_waiting_multi_translate_review_state():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="multi_translate",
        task_id="multi-waiting",
        state={
            "status": "uploaded",
            "current_review_step": "voice_match",
            "steps": {
                "extract": "done",
                "asr": "done",
                "voice_match": "waiting",
                "alignment": "pending",
            },
        },
        active=False,
    )

    assert changed is False
    assert status is None
    assert recovered["status"] == "uploaded"
    assert recovered["current_review_step"] == "voice_match"
    assert recovered["steps"]["voice_match"] == "waiting"


def test_recover_project_state_keeps_active_image_translate_running():
    from appcore import task_recovery

    changed, recovered, status = task_recovery.recover_project_state(
        project_type="image_translate",
        task_id="it-live",
        state={
            "type": "image_translate",
            "status": "running",
            "items": [{"idx": 0, "status": "running"}],
        },
        active=True,
    )

    assert changed is False
    assert status is None
    assert recovered["status"] == "running"
    assert recovered["items"][0]["status"] == "running"


def test_recover_all_interrupted_tasks_picks_up_image_translate_rows(monkeypatch):
    from appcore import task_recovery

    row = {
        "id": "it-boot",
        "type": "image_translate",
        "status": "running",
        "state_json": json.dumps(
            {
                "type": "image_translate",
                "status": "running",
                "items": [
                    {"idx": 0, "status": "done"},
                    {"idx": 1, "status": "running"},
                ],
            },
            ensure_ascii=False,
        ),
    }
    persisted = []

    def fake_db_query(sql, args=()):
        # 确认 SQL 覆盖了 image_translate 的 queued/running 两种状态
        assert "'image_translate'" in sql
        assert "'queued'" in sql
        assert "'running'" in sql
        return [row]

    monkeypatch.setattr(task_recovery, "db_query", fake_db_query)
    monkeypatch.setattr(task_recovery, "is_task_active", lambda project_type, task_id: False)
    monkeypatch.setattr(
        task_recovery,
        "_persist_project_recovery",
        lambda task_id, recovered, status: persisted.append((task_id, recovered, status)),
    )

    recovered = task_recovery.recover_all_interrupted_tasks()

    assert recovered == 1
    assert persisted[0][0] == "it-boot"
    assert persisted[0][2] == "interrupted"
    assert persisted[0][1]["items"][1]["status"] == "pending"


def test_startup_recovery_does_not_resume_runners(monkeypatch):
    import web.app as web_app

    calls = []
    monkeypatch.delenv("DISABLE_STARTUP_RECOVERY", raising=False)
    monkeypatch.setattr(web_app, "recover_all_interrupted_tasks", lambda: calls.append("generic"))
    monkeypatch.setattr(web_app, "mark_interrupted_bulk_translate_tasks", lambda: calls.append("bulk"))
    monkeypatch.setattr(
        "web.routes.subtitle_removal.resume_inflight_tasks",
        lambda: calls.append("subtitle_resume"),
    )
    monkeypatch.setattr(
        "web.services.translate_lab_runner.resume",
        lambda **_kwargs: calls.append("translate_lab_resume"),
    )

    web_app._run_startup_recovery()

    assert calls == ["generic", "bulk"]


def test_create_app_runs_interrupted_task_recovery(monkeypatch):
    import web.app as web_app

    called = {"generic": 0, "bulk": 0}
    monkeypatch.setattr(web_app, "recover_all_interrupted_tasks", lambda: called.update({"generic": called["generic"] + 1}))
    monkeypatch.setattr(
        web_app,
        "mark_interrupted_bulk_translate_tasks",
        lambda: called.update({"bulk": called["bulk"] + 1}),
        raising=False,
    )

    app = create_app()

    assert app
    assert called == {"generic": 1, "bulk": 1}
