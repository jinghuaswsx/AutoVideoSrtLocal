import base64
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _patch_bulk_translate_startup_recovery(monkeypatch):
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)


def test_list_page_renders(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query", return_value=[]), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/multi-translate")
    assert resp.status_code == 200
    assert "多语种视频翻译".encode("utf-8") in resp.data


def test_list_filters_by_lang(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query") as m_q, \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        m_q.return_value = []
        authed_client_no_db.get("/multi-translate?lang=de")
    sql = m_q.call_args.args[0]
    assert "type = 'multi_translate'" in sql
    args = m_q.call_args.args[1]
    assert "de" in args


def test_detail_404_for_other_user(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query_one", return_value=None), \
         patch("appcore.task_recovery.recover_project_if_needed"):
        resp = authed_client_no_db.get("/multi-translate/unknown")
    assert resp.status_code == 404


def test_rematch_excludes_default_voice_from_top10(authed_client_no_db):
    state = {
        "target_lang": "de",
        "voice_match_query_embedding": base64.b64encode(b"fake-embedding").decode("ascii"),
    }
    with patch(
        "web.routes.multi_translate.db_query_one",
        return_value={"state_json": json.dumps(state, ensure_ascii=False)},
    ), patch(
        "web.routes.multi_translate.db_execute",
    ), patch(
        "appcore.video_translate_defaults.resolve_default_voice",
        return_value="default-voice-id",
    ), patch(
        "pipeline.voice_embedding.deserialize_embedding",
        return_value="decoded-embedding",
    ), patch(
        "pipeline.voice_match.match_candidates",
        return_value=[{"voice_id": "voice-b", "similarity": 0.91}],
    ) as m_match:
        resp = authed_client_no_db.post(
            "/api/multi-translate/task-1/rematch",
            json={"gender": "female"},
        )

    assert resp.status_code == 200
    assert resp.get_json()["candidates"][0]["voice_id"] == "voice-b"
    assert m_match.call_args.kwargs["exclude_voice_ids"] == {"default-voice-id"}


def test_multi_translate_start_accepts_local_multipart_and_marks_local_primary(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.multi_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.multi_translate.db_execute", lambda sql, args: None)
    started = {}
    monkeypatch.setattr(
        "web.routes.multi_translate.multi_pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "de",
            "video": (io.BytesIO(b"multi-video"), "demo.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    from web import store

    task = store.get(payload["task_id"])
    assert task["type"] == "multi_translate"
    assert task["target_lang"] == "de"
    assert task["delivery_mode"] == "local_primary"
    assert task["source_tos_key"] == ""
    assert task["source_object_info"]["content_type"] == "video/mp4"
    assert task["source_object_info"]["file_size"] == len(b"multi-video")
    assert task["source_object_info"]["storage_backend"] == "local"
    assert task["source_object_info"]["original_filename"] == "demo.mp4"
    assert task["source_object_info"]["uploaded_at"]
    assert started["task_id"] == payload["task_id"]


def test_multi_translate_list_page_uses_local_multipart_upload():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "multi_translate_list.html").read_text(encoding="utf-8")

    assert "new FormData(uploadForm)" in template
    assert "fetch('/api/multi-translate/start'" in template
    assert "/api/multi-translate/bootstrap" not in template
    assert "/api/multi-translate/complete" not in template
    assert "/api/multi-translate/compat-bootstrap" not in template
    assert "/api/multi-translate/compat-complete" not in template
    assert "xhr.open('PUT'" not in template


def test_voice_selector_multi_exposes_single_frame_subtitle_preview():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_voice_selector_multi.html").read_text(encoding="utf-8")
    script = (root / "web" / "static" / "voice_selector_multi.js").read_text(encoding="utf-8")

    assert 'id="vsPreviewFrame"' in template
    assert 'id="vsPreviewVideo"' in template
    assert 'id="vsPreviewSubtitle"' in template
    assert 'id="vsPreviewNote"' in template
    assert "tryAttachPreviewVideo" in script
    assert "vsPreviewSubtitle" in script
    assert "pointerdown" in script

def test_multi_translate_subtitle_preview_route(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.multi_translate.build_multi_translate_preview_payload",
        lambda task_id, user_id: {
            "video_url": "/media/demo.mp4",
            "subtitle_font": "Impact",
            "subtitle_size": 14,
            "subtitle_position_y": 0.68,
            "sample_lines": [
                "Tiktok and facebook shot videos!",
                "Tiktok and facebook shot videos!",
            ],
        },
    )
    monkeypatch.setattr(
        "web.routes.multi_translate.db_query_one",
        lambda sql, args: {"id": args[0], "state_json": "{}"},
    )

    resp = authed_client_no_db.get("/api/multi-translate/task-1/subtitle-preview")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["video_url"] == "/media/demo.mp4"
    assert payload["sample_lines"] == [
        "Tiktok and facebook shot videos!",
        "Tiktok and facebook shot videos!",
    ]


def test_multi_translate_detail_includes_shared_subtitle_preview_assets():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "multi_translate_detail.html").read_text(encoding="utf-8")
    preview_panel = (root / "web" / "templates" / "_subtitle_preview_panel.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")
    workbench = (root / "web" / "templates" / "_task_workbench.html").read_text(encoding="utf-8")

    assert "_subtitle_preview_panel.html" in template
    assert "subtitle_preview.js" in template
    assert "--subtitle-preview-w: 270px;" in preview_panel
    assert "--subtitle-preview-h: 480px;" in preview_panel
    assert "sharedSubtitlePreviewMount" in workbench
    assert "openPhonePickerBtn" not in scripts
    assert "phoneFrame" not in scripts
    assert "pfSubtitleBar" not in scripts
    assert "createSubtitlePreviewController" in scripts


def test_multi_translate_detail_displays_asr_result_before_extracted_audio():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "multi_translate_detail.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "#pipelineCard .steps > #step-asr" in template
    assert "#pipelineCard .steps > #step-extract" in template
    assert 'const STEP_ORDER = ["extract", "asr"' in scripts
def test_multi_translate_complete_rejects_new_pure_tos_creation(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/api/multi-translate/complete",
        json={
            "task_id": "multi-task-from-tos",
            "object_key": "uploads/1/multi-task-from-tos/demo.mp4",
            "original_filename": "demo.mp4",
            "target_lang": "de",
        },
    )

    assert resp.status_code == 410
    assert "本地" in resp.get_json()["error"]
def test_confirm_voice_resumes_parent_bulk_translate_scheduler(authed_client_no_db):
    state = {
        "target_lang": "de",
        "medias_context": {
            "parent_task_id": "bulk-parent-1",
        },
    }
    resume_calls = []
    bg_calls = []

    with patch(
        "web.routes.multi_translate.db_query_one",
        return_value={"state_json": json.dumps(state, ensure_ascii=False)},
    ), patch(
        "web.routes.multi_translate.db_execute",
    ), patch(
        "web.routes.multi_translate.task_state.update",
    ), patch(
        "web.routes.multi_translate.task_state.set_step",
    ), patch(
        "web.routes.multi_translate.task_state.set_current_review_step",
    ), patch(
        "web.routes.multi_translate.multi_pipeline_runner.resume",
        side_effect=lambda task_id, start_step, user_id=None: resume_calls.append((task_id, start_step, user_id)),
    ), patch(
        "web.background.start_background_task",
        side_effect=lambda fn, task_id: bg_calls.append((fn, task_id)),
    ):
        resp = authed_client_no_db.post(
            "/api/multi-translate/task-voice-1/confirm-voice",
            json={"voice_id": "voice-1", "voice_name": "Voice 1"},
        )

    assert resp.status_code == 200
    assert resume_calls == [("task-voice-1", "alignment", 1)]
    assert len(bg_calls) == 1
    assert callable(bg_calls[0][0])
    assert bg_calls[0][1] == "bulk-parent-1"
