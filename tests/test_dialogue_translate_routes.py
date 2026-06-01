from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from web import store


def test_dialogue_translate_index_requires_login(authed_client_no_db):
    client = authed_client_no_db.application.test_client()

    resp = client.get("/dialogue-translate", follow_redirects=False)

    assert resp.status_code == 302


def test_dialogue_translate_detail_requires_login(authed_client_no_db):
    client = authed_client_no_db.application.test_client()

    resp = client.get("/dialogue-translate/task-dialogue", follow_redirects=False)

    assert resp.status_code == 302


def test_dialogue_translate_detail_renders_ab_panel(authed_client_no_db, monkeypatch):
    task_id = "dialogue-detail"
    store.create(task_id, "/tmp/demo.mp4", "/tmp/dialogue-detail", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        display_name="Dialogue Detail",
        target_lang="de",
        source_language="en",
        steps={
            "extract": "done",
            "asr": "done",
            "speaker_detect": "done",
            "voice_match_ab": "waiting",
            "alignment": "pending",
            "translate": "pending",
            "tts": "pending",
            "subtitle": "pending",
            "compose": "pending",
            "export": "pending",
        },
        speaker_profiles={
            "A": {"candidates": [{"voice_id": "voice-a", "name": "Voice A"}]},
            "B": {"candidates": [{"voice_id": "voice-b", "name": "Voice B"}]},
        },
        selected_voice_by_speaker={},
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_query_one",
        lambda *args, **kwargs: {
            "id": task_id,
            "user_id": 1,
            "original_filename": "demo.mp4",
            "display_name": "Dialogue Detail",
            "task_dir": "/tmp/dialogue-detail",
            "state_json": json.dumps(store.get(task_id), ensure_ascii=False),
            "status": "running",
            "thumbnail_path": "",
            "created_at": None,
            "expires_at": None,
            "deleted_at": None,
        },
    )

    resp = authed_client_no_db.get(f"/dialogue-translate/{task_id}")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Dialogue Detail" in body
    assert "A/B 音色确认" in body
    assert body.index("A/B 音色确认") < body.index("处理进度")
    assert "/api/dialogue-translate" in body
    assert 'id="forceRestartBtn"' in body
    assert 'data-api-base="/api/dialogue-translate"' in body
    assert "dialogue_translate.localize" not in body


def test_dialogue_translate_detail_deleted_task_renders_without_workbench(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-deleted-detail"
    state = {
        "id": task_id,
        "type": "dialogue_translate",
        "display_name": "Deleted Dialogue",
        "target_lang": "de",
        "source_language": "en",
        "steps": {},
    }
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_query_one",
        lambda *args, **kwargs: {
            "id": task_id,
            "user_id": 1,
            "original_filename": "deleted.mp4",
            "display_name": "Deleted Dialogue",
            "task_dir": "/tmp/dialogue-deleted-detail",
            "state_json": json.dumps(state, ensure_ascii=False),
            "status": "deleted",
            "thumbnail_path": "",
            "created_at": None,
            "expires_at": None,
            "deleted_at": "2026-05-28T10:00:00",
        },
    )

    resp = authed_client_no_db.get(f"/dialogue-translate/{task_id}")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Deleted Dialogue" in body
    assert "_task_workbench_scripts" not in body


def test_dialogue_translate_workbench_endpoint_surface_exists(authed_client_no_db):
    app = authed_client_no_db.application
    rules = {(rule.rule, tuple(sorted(rule.methods))) for rule in app.url_map.iter_rules()}
    expected = {
        ("/api/dialogue-translate/<task_id>", ("DELETE", "OPTIONS")),
        ("/api/dialogue-translate/<task_id>/duplicate", ("OPTIONS", "POST")),
        ("/api/dialogue-translate/<task_id>/source-language", ("OPTIONS", "PUT")),
        ("/api/dialogue-translate/<task_id>/subtitle-preview", ("GET", "HEAD", "OPTIONS")),
        ("/api/dialogue-translate/<task_id>/alignment", ("OPTIONS", "PUT")),
        ("/api/dialogue-translate/<task_id>/segments", ("OPTIONS", "PUT")),
        ("/api/dialogue-translate/<task_id>/resume", ("OPTIONS", "POST")),
        ("/api/dialogue-translate/<task_id>/artifact/<name>", ("GET", "HEAD", "OPTIONS")),
        ("/api/dialogue-translate/<task_id>/artifact-path", ("GET", "HEAD", "OPTIONS")),
        ("/api/dialogue-translate/<task_id>/download/<file_type>", ("GET", "HEAD", "OPTIONS")),
        ("/api/dialogue-translate/<task_id>/start-translate", ("OPTIONS", "POST")),
        ("/api/dialogue-translate/<task_id>/retranslate", ("OPTIONS", "POST")),
        ("/api/dialogue-translate/<task_id>/select-translation", ("OPTIONS", "PUT")),
        ("/api/dialogue-translate/<task_id>/voice-library", ("GET", "HEAD", "OPTIONS")),
        ("/api/dialogue-translate/<task_id>/llm-debug/<step>", ("GET", "HEAD", "OPTIONS")),
        ("/api/dialogue-translate/<task_id>/round-file/<int:round_index>/attempt/<int:attempt>", ("GET", "HEAD", "OPTIONS")),
        ("/api/dialogue-translate/<task_id>/round-file/<int:round_index>/<kind>", ("GET", "HEAD", "OPTIONS")),
        ("/api/dialogue-translate/<task_id>/restart", ("OPTIONS", "POST")),
        ("/api/dialogue-translate/<task_id>/visible-to-all", ("OPTIONS", "PUT")),
        ("/api/dialogue-translate/<task_id>/analysis/run", ("OPTIONS", "POST")),
        ("/api/dialogue-translate/<task_id>/loudness-profile", ("OPTIONS", "POST")),
    }

    assert expected <= rules


def test_dialogue_list_query_includes_visible_to_all_for_permitted_users(
    authed_user_client_no_db,
):
    with patch("web.routes.dialogue_translate.db_query", return_value=[]) as mock_query, \
         patch("web.routes.dialogue_translate.medias.list_enabled_language_codes", return_value=["de", "en"]), \
         patch("appcore.settings.get_retention_hours", return_value=72):
        resp = authed_user_client_no_db.get("/dialogue-translate")

    assert resp.status_code == 200
    sql, args = mock_query.call_args.args
    assert (
        "p.user_id = %s OR JSON_UNQUOTE(JSON_EXTRACT(p.state_json, '$.visible_to_all')) = 'true'"
        in sql
    )
    assert args == (2,)


def test_dialogue_detail_allows_visible_to_all_for_permitted_user(
    authed_user_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-visible-to-all"
    state = {
        "id": task_id,
        "_user_id": 1,
        "type": "dialogue_translate",
        "display_name": "Shared Dialogue",
        "source_language": "en",
        "target_lang": "de",
        "visible_to_all": True,
        "steps": {"speaker_detect": "done", "voice_match_ab": "waiting"},
    }
    monkeypatch.setattr(
        "web.routes.dialogue_translate.recover_project_if_needed",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate._dialogue_pipeline_step_names",
        lambda task, include_analysis=False: [
            "extract",
            "speaker_detect",
            "voice_match_ab",
            "alignment",
            "export",
        ],
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_query_one",
        lambda *args, **kwargs: {
            "id": task_id,
            "user_id": 1,
            "original_filename": "shared.mp4",
            "display_name": "Shared Dialogue",
            "task_dir": "/tmp/dialogue-visible-to-all",
            "state_json": json.dumps(state, ensure_ascii=False),
            "status": "running",
            "thumbnail_path": "",
            "created_at": None,
            "expires_at": None,
            "deleted_at": None,
        },
    )

    resp = authed_user_client_no_db.get(f"/dialogue-translate/{task_id}")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Shared Dialogue" in body
    assert "A/B 音色确认" in body


def test_dialogue_translate_list_template_uses_omni_project_management_shell():
    html = Path("web/templates/dialogue_translate.html").read_text(encoding="utf-8")

    assert "新建任务" not in html
    assert "最近任务" not in html
    assert "+ 新建项目" in html
    assert 'id="gridView"' in html
    assert 'id="listView"' in html
    assert 'id="uploadOverlay"' in html
    assert 'id="modalLangPills"' in html
    assert 'id="modalSourceLangPills"' in html
    assert "复制项目" in html
    assert "删除" in html
    assert "duplicateTask(event" in html
    assert "deleteTask(event" in html
    assert "/api/dialogue-translate/' + taskId + '/duplicate" in html
    assert "/api/dialogue-translate/' + taskId" in html
    assert "formData.set('plugin_config'," in html
    assert "/api/omni-translate" not in html


def test_dialogue_translate_restart_uses_dialogue_step_order(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-restart-steps"
    task = {
        "id": task_id,
        "_user_id": 1,
        "type": "dialogue_translate",
        "video_path": "/tmp/dialogue-restart.mp4",
        "task_dir": "/tmp/dialogue-restart-steps",
        "source_language": "en",
        "target_lang": "de",
        "steps": {},
    }
    store.create(task_id, task["video_path"], task["task_dir"], user_id=1)
    store.update(task_id, **task)
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_query_one",
        lambda *args, **kwargs: {
            "id": task_id,
            "user_id": 1,
            "original_filename": "dialogue.mp4",
            "display_name": "Dialogue Restart",
            "task_dir": task["task_dir"],
            "state_json": json.dumps(task, ensure_ascii=False),
            "status": "running",
            "thumbnail_path": "",
            "created_at": None,
            "expires_at": None,
            "deleted_at": None,
        },
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.recover_task_if_needed",
        lambda *args, **kwargs: None,
    )
    dialogue_steps = [
        "extract",
        "asr",
        "asr_clean",
        "speaker_detect",
        "voice_match_ab",
        "alignment",
        "translate",
        "tts",
        "subtitle",
        "compose",
        "export",
    ]
    monkeypatch.setattr(
        "web.routes.dialogue_translate._dialogue_pipeline_step_names",
        lambda *args, **kwargs: dialogue_steps,
    )
    captured: dict[str, object] = {}

    def fake_restart_task(*args, **kwargs):
        captured.update(kwargs)
        return {"id": task_id, "steps": {step: "pending" for step in kwargs["step_order"]}}

    monkeypatch.setattr("web.services.task_restart.restart_task", fake_restart_task)

    resp = authed_client_no_db.post(
        f"/api/dialogue-translate/{task_id}/restart",
        json={"source_language": "en"},
    )

    assert resp.status_code == 200
    assert captured["runner"] is not None
    assert captured["step_order"] == tuple(dialogue_steps)
    assert captured["extra_reset_fields"] == {
        "dialogue_segments": [],
        "speaker_summary": {},
        "speaker_sample_specs": [],
        "speaker_profiles": {},
        "selected_voice_by_speaker": {},
    }
    assert "speaker_detect" in captured["step_order"]
    assert "voice_match_ab" in captured["step_order"]
    assert "voice_match" not in captured["step_order"]


def test_dialogue_translate_restart_clears_dialogue_state_before_start(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-restart-clears-speakers"
    store.create(task_id, "/tmp/dialogue-restart-clear.mp4", "/tmp/dialogue-restart-clear", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        source_language="en",
        target_lang="de",
        steps={"speaker_detect": "done", "voice_match_ab": "done"},
        dialogue_segments=[{"speaker_id": "A"}],
        speaker_summary={"A": {"segment_count": 1}},
        speaker_sample_specs=[{"speaker_id": "A"}],
        speaker_profiles={"A": {"selected_voice": {"voice_id": "voice-a"}}},
        selected_voice_by_speaker={"A": {"voice_id": "voice-a"}},
    )
    monkeypatch.setattr("web.routes.dialogue_translate.db_query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.dialogue_translate.recover_task_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.services.task_restart.ensure_local_source_video",
        lambda task_id, task=None: "/tmp/dialogue-restart-clear.mp4",
    )
    monkeypatch.setattr("web.services.task_restart._purge_task_dir", lambda *args, **kwargs: None)
    started: dict[str, object] = {}
    monkeypatch.setattr(
        "web.routes.dialogue_translate.dialogue_pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    resp = authed_client_no_db.post(
        f"/api/dialogue-translate/{task_id}/restart",
        json={"source_language": "en"},
    )

    assert resp.status_code == 200
    updated = store.get(task_id)
    assert updated["dialogue_segments"] == []
    assert updated["speaker_summary"] == {}
    assert updated["speaker_sample_specs"] == []
    assert updated["speaker_profiles"] == {}
    assert updated["selected_voice_by_speaker"] == {}
    assert "speaker_detect" in updated["steps"]
    assert "voice_match_ab" in updated["steps"]
    assert started == {"task_id": task_id, "user_id": 1}


def test_dialogue_translate_deleted_task_api_is_not_resumable(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-deleted-api"
    store.create(task_id, "/tmp/deleted.mp4", "/tmp/dialogue-deleted-api", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        status="deleted",
        deleted_at="2026-05-28T10:00:00",
        steps={"speaker_detect": "done"},
    )
    monkeypatch.setattr("web.routes.dialogue_translate.db_query_one", lambda *args, **kwargs: None)
    resumed: dict[str, object] = {}
    monkeypatch.setattr(
        "web.routes.dialogue_translate.dialogue_pipeline_runner.resume",
        lambda *args, **kwargs: resumed.update({"called": True}),
    )

    resp = authed_client_no_db.post(
        f"/api/dialogue-translate/{task_id}/resume",
        json={"start_step": "speaker_detect"},
    )

    assert resp.status_code == 404
    assert resumed == {}


def test_dialogue_translate_db_deleted_task_blocks_memory_fallback(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-db-deleted-memory-active"
    memory_state = {
        "id": task_id,
        "_user_id": 1,
        "type": "dialogue_translate",
        "status": "running",
        "steps": {"speaker_detect": "done", "voice_match_ab": "pending"},
    }
    store.create(task_id, "/tmp/active.mp4", "/tmp/dialogue-db-deleted-memory-active", user_id=1)
    store.update(task_id, **memory_state)

    def fake_query_one(sql, args):
        if "deleted_at IS NULL" in sql:
            return None
        deleted_state = dict(memory_state)
        deleted_state["status"] = "expired"
        return {
            "id": task_id,
            "user_id": 1,
            "original_filename": "deleted.mp4",
            "display_name": "DB Deleted Dialogue",
            "task_dir": "/tmp/dialogue-db-deleted-memory-active",
            "state_json": json.dumps(deleted_state, ensure_ascii=False),
            "status": "expired",
            "thumbnail_path": "",
            "created_at": None,
            "expires_at": None,
            "deleted_at": "2026-05-28T10:00:00",
        }

    monkeypatch.setattr("web.routes.dialogue_translate.db_query_one", fake_query_one)
    resumed: dict[str, object] = {}
    monkeypatch.setattr(
        "web.routes.dialogue_translate.dialogue_pipeline_runner.resume",
        lambda *args, **kwargs: resumed.update({"called": True}),
    )

    resp = authed_client_no_db.post(
        f"/api/dialogue-translate/{task_id}/resume",
        json={"start_step": "speaker_detect"},
    )

    assert resp.status_code == 404
    assert resumed == {}


def test_dialogue_translate_post_requires_csrf_when_enabled(monkeypatch):
    monkeypatch.setenv("WTF_CSRF_ENABLED", "1")
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.scheduled_tasks.query", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: {
            "id": 1,
            "username": "admin",
            "role": "admin",
            "is_active": 1,
        }
        if int(user_id) == 1
        else None,
    )
    from web.app import create_app

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True

    resp = client.post("/api/dialogue-translate/task-csrf/resume", json={})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "csrf_required"


def test_dialogue_translate_detail_rejects_admin_without_permission(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.scheduled_tasks.query", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: {
            "id": 1,
            "username": "admin-without-dialogue",
            "role": "admin",
            "is_active": 1,
            "permissions": json.dumps({"dialogue_translate": False}),
        }
        if int(user_id) == 1
        else None,
    )
    from web.app import create_app

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True

    resp = client.get("/dialogue-translate/forbidden-task", follow_redirects=False)

    assert resp.status_code != 200


def test_dialogue_translate_api_rejects_admin_without_permission(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.scheduled_tasks.query", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: {
            "id": 1,
            "username": "admin-without-dialogue",
            "role": "admin",
            "is_active": 1,
            "permissions": json.dumps({"dialogue_translate": False}),
        }
        if int(user_id) == 1
        else None,
    )
    from web.app import create_app

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True

    resp = client.get("/api/dialogue-translate/forbidden-task")

    assert resp.status_code == 403
    assert resp.get_json()["error"] == "Forbidden"


def test_dialogue_translate_detail_js_does_not_interpolate_task_state_with_inner_html():
    script = Path("web/static/js/dialogue_translate_detail.js").read_text(
        encoding="utf-8",
    )

    assert "card.innerHTML" not in script


def test_dialogue_translate_layout_and_workbench_labels_are_registered():
    layout = Path("web/templates/layout.html").read_text(encoding="utf-8")
    workbench = Path("web/templates/_task_workbench_scripts.html").read_text(
        encoding="utf-8",
    )
    shell = Path("web/templates/_translate_detail_shell.html").read_text(
        encoding="utf-8",
    )

    assert "has_permission('dialogue_translate')" in layout
    assert "/dialogue-translate" in layout
    assert 'speaker_detect: "说话人识别"' in workbench
    assert 'voice_match_ab: "A/B 音色确认"' in workbench
    assert "restartHeaders['X-CSRFToken']" in shell


def test_dialogue_translate_resume_from_speaker_detect_clears_speaker_state(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-resume-speaker-detect"
    store.create(task_id, "/tmp/demo.mp4", "/tmp/dialogue-resume-speaker-detect", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        steps={
            "speaker_detect": "done",
            "voice_match_ab": "done",
            "alignment": "done",
            "translate": "done",
        },
        dialogue_segments=[{"speaker_id": "A"}],
        speaker_summary={"A": {"segment_count": 1}},
        speaker_sample_specs=[{"speaker_id": "A"}],
        speaker_profiles={"A": {"selected_voice": {"voice_id": "voice-a"}}},
        selected_voice_by_speaker={"A": {"voice_id": "voice-a"}},
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate._dialogue_pipeline_step_names",
        lambda task, include_analysis=False: [
            "speaker_detect",
            "voice_match_ab",
            "alignment",
            "translate",
        ],
    )
    resumed = {}
    monkeypatch.setattr(
        "web.routes.dialogue_translate.dialogue_pipeline_runner.resume",
        lambda task_id, start_step, user_id=None: resumed.update(
            {"task_id": task_id, "start_step": start_step, "user_id": user_id}
        ),
    )

    resp = authed_client_no_db.post(
        f"/api/dialogue-translate/{task_id}/resume",
        json={"start_step": "speaker_detect"},
    )

    assert resp.status_code == 200
    updated = store.get(task_id)
    assert updated["dialogue_segments"] == []
    assert updated["speaker_summary"] == {}
    assert updated["speaker_sample_specs"] == []
    assert updated["speaker_profiles"] == {}
    assert updated["selected_voice_by_speaker"] == {}
    assert resumed == {"task_id": task_id, "start_step": "speaker_detect", "user_id": 1}


def test_dialogue_translate_resume_after_alignment_preserves_confirmed_voices(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-resume-translate"
    selected = {"A": {"voice_id": "voice-a"}, "B": {"voice_id": "voice-b"}}
    store.create(task_id, "/tmp/demo.mp4", "/tmp/dialogue-resume-translate", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        steps={
            "speaker_detect": "done",
            "voice_match_ab": "done",
            "alignment": "done",
            "translate": "done",
            "tts": "done",
        },
        dialogue_segments=[{"speaker_id": "A"}, {"speaker_id": "B"}],
        speaker_summary={"A": {"segment_count": 1}, "B": {"segment_count": 1}},
        speaker_sample_specs=[{"speaker_id": "A"}],
        speaker_profiles={
            "A": {"selected_voice": selected["A"]},
            "B": {"selected_voice": selected["B"]},
        },
        selected_voice_by_speaker=selected,
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate._dialogue_pipeline_step_names",
        lambda task, include_analysis=False: [
            "speaker_detect",
            "voice_match_ab",
            "alignment",
            "translate",
            "tts",
        ],
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.dialogue_pipeline_runner.resume",
        lambda *args, **kwargs: None,
    )

    resp = authed_client_no_db.post(
        f"/api/dialogue-translate/{task_id}/resume",
        json={"start_step": "translate"},
    )

    assert resp.status_code == 200
    updated = store.get(task_id)
    assert updated["dialogue_segments"] == [{"speaker_id": "A"}, {"speaker_id": "B"}]
    assert updated["speaker_profiles"]["A"]["selected_voice"]["voice_id"] == "voice-a"
    assert updated["speaker_profiles"]["B"]["selected_voice"]["voice_id"] == "voice-b"
    assert updated["selected_voice_by_speaker"] == selected


def test_dialogue_translate_segments_updates_normal_localized_translation(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-segments"
    store.create(task_id, "/tmp/demo.mp4", "/tmp/dialogue-segments", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        variants={"normal": {"localized_translation": {"sentences": []}}},
        steps={"translate": "waiting", "tts": "pending"},
    )
    resumed = {}
    monkeypatch.setattr(
        "web.routes.dialogue_translate.dialogue_pipeline_runner.resume",
        lambda task_id, start_step, user_id=None: resumed.update(
            {"task_id": task_id, "start_step": start_step, "user_id": user_id}
        ),
    )

    resp = authed_client_no_db.put(
        f"/api/dialogue-translate/{task_id}/segments",
        json={
            "segments": [
                {
                    "index": 3,
                    "translated": "Hallo",
                    "source_segment_indices": [1, 2],
                },
                {"translated": "Welt"},
            ]
        },
    )

    assert resp.status_code == 200
    updated = store.get(task_id)
    translation = updated["variants"]["normal"]["localized_translation"]
    assert translation["full_text"] == "Hallo Welt"
    assert translation["sentences"] == [
        {"index": 3, "text": "Hallo", "source_segment_indices": [1, 2]},
        {"index": 1, "text": "Welt", "source_segment_indices": [1]},
    ]
    assert updated["localized_translation"] == translation
    assert updated["_segments_confirmed"] is True
    assert resumed == {"task_id": task_id, "start_step": "tts", "user_id": 1}


def test_dialogue_translate_start_creates_task_and_starts_runner(
    tmp_path,
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "web.routes.dialogue_translate.OUTPUT_DIR",
        str(tmp_path / "output"),
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.UPLOAD_DIR",
        str(tmp_path / "uploads"),
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_query_one",
        lambda sql, args: None,
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_execute",
        lambda sql, args: None,
    )
    monkeypatch.setattr(
        "web.upload_util.validate_video_extension",
        lambda filename: True,
    )
    monkeypatch.setattr(
        "web.upload_util.save_uploaded_video",
        lambda file, upload_dir, task_id, original_filename: (
            str(tmp_path / "uploads" / f"{task_id}.mp4"),
            len(b"dialogue-video"),
            "video/mp4",
        ),
    )
    monkeypatch.setattr(
        "web.upload_util.build_source_object_info",
        lambda **kwargs: {
            "original_filename": kwargs["original_filename"],
            "content_type": kwargs["content_type"],
            "file_size": kwargs["file_size"],
            "storage_backend": kwargs["storage_backend"],
            "uploaded_at": kwargs["uploaded_at"],
        },
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate._list_enabled_target_langs",
        lambda: ("en", "de"),
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.current_fixed_plugin_config",
        lambda: {
            "asr_post": "asr_clean",
            "shot_decompose": False,
            "translate_algo": "standard",
            "source_anchored": True,
            "tts_strategy": "five_round_rewrite",
            "subtitle": "asr_realign",
            "voice_separation": True,
            "loudness_match": True,
            "av_sync_audit": "off",
        },
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate._ensure_uploaded_video_thumbnail",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate._resolve_name_conflict",
        lambda user_id, desired_name: desired_name,
    )
    started: dict[str, object] = {}
    monkeypatch.setattr(
        "web.routes.dialogue_translate.dialogue_pipeline_runner.start",
        lambda task_id, user_id=None: started.update(
            {"task_id": task_id, "user_id": user_id}
        ),
    )

    resp = authed_client_no_db.post(
        "/api/dialogue-translate/start",
        data={
            "source_language": "en",
            "target_lang": "de",
            "video": (io.BytesIO(b"dialogue-video"), "dialogue.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 201
    payload = resp.get_json()
    task = store.get(payload["task_id"])
    assert task["type"] == "dialogue_translate"
    assert task["status"] == "running"
    assert task["source_language"] == "en"
    assert task["target_lang"] == "de"
    assert list(task["steps"].keys()) == [
        "extract",
        "asr",
        "separate",
        "asr_clean",
        "speaker_detect",
        "voice_match_ab",
        "alignment",
        "translate",
        "tts",
        "loudness_match",
        "subtitle",
        "compose",
        "export",
    ]
    assert task["dialogue_segments"] == []
    assert task["speaker_profiles"] == {}
    assert task["selected_voice_by_speaker"] == {}
    assert payload["redirect_url"] == f"/dialogue-translate/{payload['task_id']}"
    assert started == {"task_id": payload["task_id"], "user_id": 1}


def test_dialogue_translate_start_accepts_plugin_config_snapshot(
    tmp_path,
    authed_client_no_db,
    monkeypatch,
):
    plugin_config = {
        "asr_post": "asr_normalize",
        "shot_decompose": False,
        "translate_algo": "av_sentence",
        "source_anchored": False,
        "tts_strategy": "sentence_reconcile",
        "subtitle": "sentence_units",
        "voice_separation": False,
        "loudness_match": False,
        "av_sync_audit": "off",
    }
    monkeypatch.setattr("web.routes.dialogue_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.dialogue_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.dialogue_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.dialogue_translate.db_execute", lambda sql, args: None)
    monkeypatch.setattr("web.upload_util.validate_video_extension", lambda filename: True)
    monkeypatch.setattr(
        "web.upload_util.save_uploaded_video",
        lambda file, upload_dir, task_id, original_filename: (
            str(tmp_path / "uploads" / f"{task_id}.mp4"),
            len(b"dialogue-video"),
            "video/mp4",
        ),
    )
    monkeypatch.setattr(
        "web.upload_util.build_source_object_info",
        lambda **kwargs: {
            "original_filename": kwargs["original_filename"],
            "content_type": kwargs["content_type"],
            "file_size": kwargs["file_size"],
            "storage_backend": kwargs["storage_backend"],
            "uploaded_at": kwargs["uploaded_at"],
        },
    )
    monkeypatch.setattr("web.routes.dialogue_translate._list_enabled_target_langs", lambda: ("en", "de"))
    monkeypatch.setattr("web.routes.dialogue_translate._ensure_uploaded_video_thumbnail", lambda *args, **kwargs: "")
    monkeypatch.setattr("web.routes.dialogue_translate._resolve_name_conflict", lambda user_id, desired_name: desired_name)
    monkeypatch.setattr("web.routes.dialogue_translate.dialogue_pipeline_runner.start", lambda *args, **kwargs: None)

    resp = authed_client_no_db.post(
        "/api/dialogue-translate/start",
        data={
            "source_language": "en",
            "target_lang": "de",
            "plugin_config": json.dumps(plugin_config),
            "video": (io.BytesIO(b"dialogue-video"), "dialogue.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 201
    task = store.get(resp.get_json()["task_id"])
    assert task["plugin_config"] == plugin_config
    assert list(task["steps"].keys()) == [
        "extract",
        "asr",
        "asr_normalize",
        "speaker_detect",
        "voice_match_ab",
        "alignment",
        "translate",
        "tts",
        "subtitle",
        "compose",
        "export",
    ]


def test_dialogue_translate_duplicate_copies_source_config_and_restarts(
    tmp_path,
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-duplicate-source"
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"source-video")
    source_state = {
        "id": task_id,
        "_user_id": 1,
        "type": "dialogue_translate",
        "video_path": str(source_video),
        "task_dir": str(tmp_path / "old-task"),
        "original_filename": "source.mp4",
        "display_name": "Dialogue Source",
        "source_language": "en",
        "target_lang": "de",
        "plugin_config": {
            "asr_post": "asr_clean",
            "shot_decompose": False,
            "translate_algo": "standard",
            "source_anchored": True,
            "tts_strategy": "five_round_rewrite",
            "subtitle": "asr_realign",
            "voice_separation": True,
            "loudness_match": True,
            "av_sync_audit": "off",
        },
        "dialogue_segments": [{"speaker_id": "A"}],
        "speaker_profiles": {"A": {"selected_voice": {"voice_id": "old"}}},
        "selected_voice_by_speaker": {"A": {"voice_id": "old"}},
    }
    store.create(task_id, str(source_video), source_state["task_dir"], user_id=1)
    store.update(task_id, **source_state)
    monkeypatch.setattr("web.routes.dialogue_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.dialogue_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.dialogue_translate.db_query_one", lambda *args, **kwargs: {
        "id": task_id,
        "user_id": 1,
        "original_filename": "source.mp4",
        "display_name": "Dialogue Source",
        "task_dir": source_state["task_dir"],
        "state_json": json.dumps(source_state, ensure_ascii=False),
    })
    monkeypatch.setattr("web.routes.dialogue_translate.db_execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.tos_backup_storage.ensure_remote_copy_for_local_path", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.dialogue_translate._ensure_uploaded_video_thumbnail", lambda *args, **kwargs: "")
    monkeypatch.setattr("web.routes.dialogue_translate._resolve_name_conflict", lambda user_id, desired_name: desired_name)
    started: dict[str, object] = {}
    monkeypatch.setattr(
        "web.routes.dialogue_translate.dialogue_pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    resp = authed_client_no_db.post(f"/api/dialogue-translate/{task_id}/duplicate")

    assert resp.status_code == 201
    payload = resp.get_json()
    new_task = store.get(payload["task_id"])
    assert payload["redirect_url"] == f"/dialogue-translate/{payload['task_id']}"
    assert new_task["type"] == "dialogue_translate"
    assert new_task["display_name"] == "Dialogue Source 副本"
    assert new_task["source_language"] == "en"
    assert new_task["target_lang"] == "de"
    assert new_task["plugin_config"] == source_state["plugin_config"]
    assert new_task["dialogue_segments"] == []
    assert new_task["speaker_profiles"] == {}
    assert new_task["selected_voice_by_speaker"] == {}
    assert started == {"task_id": payload["task_id"], "user_id": 1}


def test_dialogue_translate_delete_soft_deletes_project(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-delete"
    store.create(task_id, "/tmp/source.mp4", "/tmp/dialogue-delete", user_id=1)
    store.update(task_id, type="dialogue_translate", status="running")
    monkeypatch.setattr(
        "web.routes.dialogue_translate.translation_route_store.get_active_project_storage",
        lambda *args, **kwargs: {
            "task_dir": "/tmp/dialogue-delete",
            "state_json": json.dumps(store.get(task_id), ensure_ascii=False),
        },
    )
    soft_deleted: dict[str, object] = {}
    monkeypatch.setattr(
        "web.routes.dialogue_translate.translation_route_store.soft_delete_project",
        lambda task_id, user_id, project_type, execute_func=None: soft_deleted.update(
            {"task_id": task_id, "user_id": user_id, "project_type": project_type}
        ),
    )
    monkeypatch.setattr("appcore.cleanup.delete_task_storage", lambda *args, **kwargs: None)

    resp = authed_client_no_db.delete(f"/api/dialogue-translate/{task_id}")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    assert soft_deleted == {
        "task_id": task_id,
        "user_id": 1,
        "project_type": "dialogue_translate",
    }
    assert store.get(task_id)["status"] == "deleted"


def test_dialogue_translate_confirm_voices_requires_both_a_and_b(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-confirm-missing-b"
    store.create(task_id, "/tmp/demo.mp4", "/tmp/dialogue-confirm-missing-b", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        speaker_profiles={
            "A": {"candidates": [{"voice_id": "voice-a", "name": "Voice A"}]},
            "B": {"candidates": [{"voice_id": "voice-b", "name": "Voice B"}]},
        },
        selected_voice_by_speaker={},
        current_review_step="voice_match_ab",
        steps={"voice_match_ab": "waiting", "alignment": "pending"},
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_query_one",
        lambda *args, **kwargs: {
            "state_json": json.dumps(store.get(task_id), ensure_ascii=False),
            "user_id": 1,
        },
    )

    resp = authed_client_no_db.post(
        f"/api/dialogue-translate/{task_id}/confirm-voices",
        json={"selected_voice_by_speaker": {"A": "voice-a"}},
    )

    assert resp.status_code == 400
    assert "A" in resp.get_json()["error"]
    assert "B" in resp.get_json()["error"]


def test_dialogue_translate_confirm_voices_requires_waiting_step(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-confirm-not-waiting"
    store.create(task_id, "/tmp/demo.mp4", "/tmp/dialogue-confirm-not-waiting", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        speaker_profiles={
            "A": {"candidates": [{"voice_id": "voice-a", "name": "Voice A"}]},
            "B": {"candidates": [{"voice_id": "voice-b", "name": "Voice B"}]},
        },
        selected_voice_by_speaker={},
        steps={"voice_match_ab": "done", "alignment": "pending"},
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_query_one",
        lambda *args, **kwargs: {
            "state_json": json.dumps(store.get(task_id), ensure_ascii=False),
            "user_id": 1,
        },
    )

    resp = authed_client_no_db.post(
        f"/api/dialogue-translate/{task_id}/confirm-voices",
        json={"selected_voice_by_speaker": {"A": "voice-a", "B": "voice-b"}},
    )

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "voice_match_ab is not waiting"


def test_dialogue_translate_confirm_voices_rejects_non_candidate_voice(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-confirm-invalid-voice"
    store.create(task_id, "/tmp/demo.mp4", "/tmp/dialogue-confirm-invalid-voice", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        speaker_profiles={
            "A": {"candidates": [{"voice_id": "voice-a", "name": "Voice A"}]},
            "B": {"candidates": [{"voice_id": "voice-b", "name": "Voice B"}]},
        },
        selected_voice_by_speaker={},
        current_review_step="voice_match_ab",
        steps={"voice_match_ab": "waiting", "alignment": "pending"},
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_query_one",
        lambda *args, **kwargs: {
            "state_json": json.dumps(store.get(task_id), ensure_ascii=False),
            "user_id": 1,
        },
    )

    resp = authed_client_no_db.post(
        f"/api/dialogue-translate/{task_id}/confirm-voices",
        json={"selected_voice_by_speaker": {"A": "voice-x", "B": "voice-b"}},
    )

    assert resp.status_code == 400
    assert "Speaker A" in resp.get_json()["error"]


def test_dialogue_translate_voice_library_returns_speaker_candidates(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-voice-library"
    store.create(task_id, "/tmp/demo.mp4", "/tmp/dialogue-voice-library", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        target_lang="de",
        speaker_profiles={
            "A": {"candidates": [{"voice_id": "voice-a", "name": "Candidate A"}]},
            "B": {"candidates": [{"voice_id": "voice-b", "name": "Candidate B"}]},
        },
        selected_voice_by_speaker={"A": {"voice_id": "voice-a", "name": "Candidate A"}},
        steps={"extract": "done", "asr": "done", "voice_match_ab": "waiting"},
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_query_one",
        lambda *args, **kwargs: {
            "state_json": json.dumps(store.get(task_id), ensure_ascii=False),
            "user_id": 1,
        },
    )

    def fake_list_voices(**kwargs):
        assert kwargs["language"] == "de"
        assert kwargs["gender"] == "female"
        assert kwargs["q"] == "anna"
        return {
            "items": [{"voice_id": "voice-a", "name": "Candidate A"}],
            "total": 1,
            "page": kwargs["page"],
            "page_size": kwargs["page_size"],
        }

    monkeypatch.setattr("appcore.voice_library_browse.list_voices", fake_list_voices)

    resp = authed_client_no_db.get(
        f"/api/dialogue-translate/{task_id}/voice-library?speaker=A&gender=female&q=anna",
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["speaker"] == "A"
    assert payload["selected_voice_id"] == "voice-a"
    assert payload["voice_match_ready"] is True
    assert payload["candidates"] == [{"voice_id": "voice-a", "name": "Candidate A"}]


def test_dialogue_translate_confirm_voices_accepts_library_voice(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-confirm-library-voice"
    store.create(task_id, "/tmp/demo.mp4", "/tmp/dialogue-confirm-library-voice", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        target_lang="de",
        speaker_profiles={
            "A": {"candidates": []},
            "B": {"candidates": [{"voice_id": "voice-b", "name": "Voice B"}]},
        },
        selected_voice_by_speaker={},
        current_review_step="voice_match_ab",
        steps={"voice_match_ab": "waiting", "alignment": "pending"},
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_query_one",
        lambda *args, **kwargs: {
            "state_json": json.dumps(store.get(task_id), ensure_ascii=False),
            "user_id": 1,
        },
    )
    monkeypatch.setattr(
        "appcore.voice_library_browse.fetch_voice_by_id",
        lambda language, voice_id: (
            {"voice_id": voice_id, "name": "Library A", "gender": "female"}
            if language == "de" and voice_id == "voice-library-a"
            else None
        ),
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.save_project_state",
        lambda saved_task_id, state, **kwargs: store.update(saved_task_id, **state),
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.dialogue_pipeline_runner.resume",
        lambda *args, **kwargs: None,
    )

    resp = authed_client_no_db.post(
        f"/api/dialogue-translate/{task_id}/confirm-voices",
        json={"selected_voice_by_speaker": {"A": "voice-library-a", "B": "voice-b"}},
    )

    assert resp.status_code == 200
    updated = store.get(task_id)
    assert updated["selected_voice_by_speaker"]["A"]["voice_id"] == "voice-library-a"
    assert updated["selected_voice_by_speaker"]["A"]["name"] == "Library A"
    assert updated["speaker_profiles"]["A"]["candidates"][0]["voice_id"] == "voice-library-a"


def test_dialogue_translate_confirm_voices_persists_selection_and_resumes_alignment(
    authed_client_no_db,
    monkeypatch,
):
    task_id = "dialogue-confirm-ok"
    store.create(task_id, "/tmp/demo.mp4", "/tmp/dialogue-confirm-ok", user_id=1)
    store.update(
        task_id,
        type="dialogue_translate",
        target_lang="de",
        speaker_profiles={
            "A": {
                "candidates": [
                    {"voice_id": "voice-a", "name": "Voice A"},
                    {"voice_id": "voice-a-2", "name": "Voice A2"},
                ],
                "selected_voice": None,
            },
            "B": {
                "candidates": [{"voice_id": "voice-b", "name": "Voice B"}],
                "selected_voice": None,
            },
        },
        selected_voice_by_speaker={},
        current_review_step="voice_match_ab",
        steps={
            "speaker_detect": "done",
            "voice_match_ab": "waiting",
            "alignment": "pending",
        },
    )
    monkeypatch.setattr(
        "web.routes.dialogue_translate.db_query_one",
        lambda *args, **kwargs: {
            "state_json": json.dumps(store.get(task_id), ensure_ascii=False),
            "user_id": 1,
        },
    )
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        "web.routes.dialogue_translate.save_project_state",
        lambda saved_task_id, state, **kwargs: saved.update(
            {"task_id": saved_task_id, "state": json.loads(json.dumps(state))}
        ),
    )
    resumed: dict[str, object] = {}
    monkeypatch.setattr(
        "web.routes.dialogue_translate.dialogue_pipeline_runner.resume",
        lambda resumed_task_id, start_step, user_id=None: resumed.update(
            {
                "task_id": resumed_task_id,
                "start_step": start_step,
                "user_id": user_id,
            }
        ),
    )

    resp = authed_client_no_db.post(
        f"/api/dialogue-translate/{task_id}/confirm-voices",
        json={
            "selected_voice_by_speaker": {
                "A": "voice-a-2",
                "B": "voice-b",
            }
        },
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["selected_voice_by_speaker"] == {"A": "voice-a-2", "B": "voice-b"}
    assert saved["task_id"] == task_id
    assert saved["state"]["speaker_profiles"]["A"]["selected_voice"]["voice_id"] == "voice-a-2"
    assert saved["state"]["speaker_profiles"]["B"]["selected_voice"]["voice_id"] == "voice-b"
    assert saved["state"]["selected_voice_by_speaker"]["A"]["voice_id"] == "voice-a-2"
    assert saved["state"]["selected_voice_by_speaker"]["B"]["voice_id"] == "voice-b"
    assert saved["state"]["steps"]["voice_match_ab"] == "done"
    assert saved["state"]["current_review_step"] == ""
    updated = store.get(task_id)
    assert updated["speaker_profiles"]["A"]["selected_voice"]["voice_id"] == "voice-a-2"
    assert updated["speaker_profiles"]["B"]["selected_voice"]["voice_id"] == "voice-b"
    assert updated["selected_voice_by_speaker"]["A"]["voice_id"] == "voice-a-2"
    assert updated["selected_voice_by_speaker"]["B"]["voice_id"] == "voice-b"
    assert updated["steps"]["voice_match_ab"] == "done"
    assert updated["current_review_step"] == ""
    assert resumed == {"task_id": task_id, "start_step": "alignment", "user_id": 1}
