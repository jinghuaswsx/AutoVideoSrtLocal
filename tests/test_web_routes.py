from datetime import datetime
from pathlib import Path
import json
import io
import subprocess

import pytest

from web import store
from web.app import create_app
from web.extensions import socketio
from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT


def test_index_page_contains_alignment_and_voice_controls(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "voiceList" in body
    assert "alignmentReview" in body


def test_index_page_contains_step_preview_container(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "step-preview" in body
    assert "renderStepPreviews" in body


def test_index_page_contains_active_task_refresh_fallback(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "startActiveRefreshLoop" in body
    assert "stopActiveRefreshLoop" in body
    assert "setInterval(refreshTaskState" in body


def test_index_page_supports_new_localization_preview_types(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "item.type === \"sentences\"" in body
    assert "item.type === \"tts_blocks\"" in body
    assert "item.type === \"subtitle_chunks\"" in body


def test_index_page_supports_variant_compare_layout(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "artifact.layout === \"variant_compare\"" in body
    assert "renderVariantCompareArtifact" in body


def test_index_page_supports_action_preview_items(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "item.type === \"action\"" in body
    assert "triggerAction(" in body


def test_index_page_uses_local_multipart_upload_entrypoint(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'formData.append("video", file);' in body
    assert 'fetch("/api/tasks", {' in body
    assert "/api/tos-upload/bootstrap" not in body
    assert "/api/tos-upload/complete" not in body
    assert "/api/tos-upload/compat-bootstrap" not in body
    assert "/api/tos-upload/compat-complete" not in body
    assert 'xhr.open("PUT", bootstrap.upload_url, true)' not in body


def test_task_upload_route_accepts_local_multipart_and_marks_local_primary(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.task.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.task.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.task.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.task.db_execute", lambda sql, args: None)
    started = {}
    monkeypatch.setattr(
        "web.routes.task.pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    response = authed_client_no_db.post(
        "/api/tasks",
        data={"video": (io.BytesIO(b"video-bytes"), "demo.mp4")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    task = store.get(payload["task_id"])
    assert task["delivery_mode"] == "local_primary"
    assert task["source_tos_key"] == ""
    assert task["source_object_info"]["original_filename"] == "demo.mp4"
    assert task["source_object_info"]["content_type"] == "video/mp4"
    assert task["source_object_info"]["file_size"] == len(b"video-bytes")
    assert task["source_object_info"]["storage_backend"] == "local"
    assert task["source_object_info"]["uploaded_at"]
    assert task["video_path"].endswith(f'{payload["task_id"]}.mp4')
    assert Path(task["video_path"]).read_bytes() == b"video-bytes"
    assert started == {}


def test_task_upload_route_accepts_av_sync_list_form_inputs(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.task.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.task.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.task.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.task.db_execute", lambda sql, args: None)

    response = authed_client_no_db.post(
        "/api/tasks",
        data={
            "video": (io.BytesIO(b"video-bytes"), "demo.mp4"),
            "target_lang": "de",
            "target_market": "OTHER",
            "sync_granularity": "sentence",
            "display_name": "德语项目",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    task = store.get(response.get_json()["task_id"])
    assert task["display_name"] == "德语项目"
    assert task["target_lang"] == "de"
    assert task["av_translate_inputs"]["target_language"] == "de"
    assert task["av_translate_inputs"]["target_language_name"] == "German"
    assert task["av_translate_inputs"]["target_market"] == "OTHER"
    assert task["av_translate_inputs"]["sync_granularity"] == "sentence"


def test_de_translate_list_page_uses_local_multipart_upload():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "de_translate_list.html").read_text(encoding="utf-8")

    assert "new FormData(uploadForm)" in template
    assert "fetch('/api/de-translate/start'" in template
    assert "/api/de-translate/bootstrap" not in template
    assert "/api/de-translate/complete" not in template
    assert "/api/de-translate/compat-bootstrap" not in template
    assert "/api/de-translate/compat-complete" not in template
    assert "xhr.open('PUT'" not in template


def test_fr_translate_list_page_uses_local_multipart_upload():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "fr_translate_list.html").read_text(encoding="utf-8")

    assert "new FormData(uploadForm)" in template
    assert "fetch('/api/fr-translate/start'" in template
    assert "/api/fr-translate/bootstrap" not in template
    assert "/api/fr-translate/complete" not in template
    assert "/api/fr-translate/compat-bootstrap" not in template
    assert "/api/fr-translate/compat-complete" not in template
    assert "xhr.open('PUT'" not in template


def test_subtitle_removal_upload_template_exposes_real_upload_entrypoints():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "subtitle_removal_upload.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "templates" / "_subtitle_removal_scripts.html").read_text(encoding="utf-8")

    assert 'id="srUploadInput"' in template
    assert 'type="file"' in template
    assert 'accept="video/*"' in template
    assert 'id="srPickVideoButton"' in template
    assert 'data-subtitle-removal-page="upload"' in template
    assert 'disabled' not in template
    assert "/api/subtitle-removal/upload/bootstrap" in scripts
    assert "/api/subtitle-removal/upload/complete" in scripts
    assert 'xhr.open("PUT", bootstrapData.upload_url, true)' in scripts
    assert "window.location.href = `/subtitle-removal/${data.task_id}`;" in scripts
    assert "if (!uploadInput || !uploadButton || !uploadDropzone)" in scripts


def test_subtitle_removal_list_page_uses_90x160_first_frame_thumbnails_with_centered_row_content(authed_client_no_db):
    response = authed_client_no_db.get("/subtitle-removal")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "grid-template-columns: 90px 1fr 140px 140px 160px 180px 220px;" in body
    assert "align-items: center;" in body
    assert ".sr-list-thumb { width: 90px; height: 160px;" in body


def test_subtitle_removal_scripts_normalize_persisted_selection_box_protocols():
    root = Path(__file__).resolve().parents[1]
    scripts = (root / "web" / "templates" / "_subtitle_removal_scripts.html").read_text(encoding="utf-8")

    assert "function normalizeSelectionBox(selectionBox, positionPayload)" in scripts
    assert "selectionBox.x1 != null ? selectionBox.x1 : selectionBox.l" in scripts
    assert "positionPayload.l" in scripts
    assert "x2 = x1 + width;" in scripts
    assert "y2 = y1 + height;" in scripts
    assert "selectionState.box = normalizeSelectionBox(state.selection_box, state.position_payload);" in scripts
    assert "window.normalizeSubtitleRemovalSelectionBox = normalizeSelectionBox;" in scripts
    assert "return selectionState.box || normalizeSelectionBox(bootstrap.selection_box, bootstrap.position_payload) || null;" in scripts


def test_index_page_uses_simple_start_button_loading_state(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "加载视频中..." in body
    assert "startPreparationOverlay" not in body
    assert "setStartButtonBusy" in body


def test_index_page_contains_confirmation_mode_control(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "interactiveReviewToggle" in body
    assert "全自动" in body
    assert "手动确认" in body


def test_index_page_contains_av_sync_controls(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "音画同步配置" in body
    assert 'id="avTargetLanguage"' in body
    assert 'id="avTargetMarket"' in body
    assert 'id="avOverridesPanel"' in body
    assert 'id="avOverrideSellingPoints"' in body
    assert "getAvTranslateInputs()" in body


def test_project_detail_page_contains_shared_workbench_hooks(authed_client_no_db, monkeypatch):
    task = store.create("task-project-workbench", "video.mp4", "output/task-project-workbench")
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "video.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(task, ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.projects.query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/projects/task-project-workbench")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "voiceList" in body
    assert "interactiveReviewToggle" in body
    assert "renderStepPreviews" in body
    assert "pipelineCard" in body


def test_project_detail_page_contains_av_insight_cards_and_rewrite_modal(authed_client_no_db, monkeypatch):
    task = store.create("task-project-av-insights", "video.mp4", "output/task-project-av-insights")
    store.update(
        task["id"],
        pipeline_version="av",
        shot_notes={
            "global": {
                "product_name": "Glow Serum",
                "category": "护肤精华",
                "overall_theme": "海边清透护肤",
                "hook_range": [0, 1],
                "demo_range": [2, 3],
                "proof_range": [4, 4],
                "cta_range": [5, 5],
                "observed_selling_points": ["清爽", "夜间修护"],
                "price_mentioned": "$29.9",
                "on_screen_persistent_text": ["7-day glow"],
                "pacing_note": "快节奏",
            },
            "sentences": [
                {
                    "asr_index": 0,
                    "start_time": 0.0,
                    "end_time": 1.2,
                    "scene": "女生举起产品瓶",
                    "action": "展示滴管质地",
                    "on_screen_text": ["7-day glow"],
                    "product_visible": True,
                    "shot_type": "close_up",
                    "emotion_hint": "兴奋",
                }
            ],
        },
    )
    store.update_variant(
        task["id"],
        "av",
        sentences=[
            {
                "asr_index": 0,
                "start_time": 0.0,
                "end_time": 1.2,
                "target_duration": 1.2,
                "tts_duration": 1.8,
                "target_chars_range": [8, 14],
                "text": "Too long for this shot",
                "status": "warning_overshoot",
                "speed": 1.12,
                "rewrite_rounds": 2,
            }
        ],
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "video.mp4",
        "status": "done",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(store.get(task["id"]), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.projects.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: "openrouter")
    monkeypatch.setattr("web.routes.projects.query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/projects/task-project-av-insights")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="avInsightsPanel"' in body
    assert 'id="avShotNotesCard"' in body
    assert 'id="avWarningsCard"' in body
    assert 'id="avRewriteModal"' in body
    assert "renderAvInsights()" in body
    assert "submitAvRewrite()" in body


def test_project_detail_page_contains_av_convergence_panel(authed_client_no_db, monkeypatch):
    task = store.create("task-project-av-convergence", "video.mp4", "output/task-project-av-convergence")
    store.update(
        task["id"],
        pipeline_version="av",
        av_debug={
            "model": "GPT-5.5",
            "sentence_convergence": {
                "model": "GPT-5.5",
                "sentences": [
                    {
                        "asr_index": 0,
                        "attempts": [
                            {
                                "round": 1,
                                "action": "rewrite",
                                "status": "too_long",
                                "reason": "too slow",
                                "before_text": "Try one",
                                "after_text": "Try shorter",
                                "target_duration": 1.2,
                                "tts_duration": 1.42,
                                "duration_ratio": 1.18,
                            },
                            {
                                "round": 2,
                                "action": "speed_adjust",
                                "status": "ok",
                                "reason": "within tolerance",
                                "before_text": "Try shorter",
                                "after_text": "Final",
                                "target_duration": 1.2,
                                "tts_duration": 1.22,
                                "duration_ratio": 1.02,
                            },
                        ],
                    }
                ],
            },
        },
    )
    store.update_variant(
        task["id"],
        "av",
        subtitle_units=[
            {
                "unit_index": 0,
                "asr_indices": [0],
                "start_time": 0.0,
                "end_time": 1.22,
                "text": "This serum feels fresh.",
                "source_text": "这款精华很清爽",
                "status": "ok",
            }
        ],
        sentences=[
            {
                "asr_index": 0,
                "source_text": "这款精华很清爽",
                "final_text": "This serum feels fresh.",
                "target_duration": 1.2,
                "tts_duration": 1.22,
                "speed": 1.03,
                "status": "converged",
                "attempts": [{"round": 1, "tts_duration": 1.42}, {"round": 2, "tts_duration": 1.22}],
            }
        ],
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "video.mp4",
        "status": "done",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(store.get(task["id"]), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.projects.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: "openrouter")
    monkeypatch.setattr("web.routes.projects.query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/projects/task-project-av-convergence")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="avConvergencePanel"' in body
    assert 'id="avSubtitleUnitsPanel"' in body
    assert "句级收敛" in body
    assert "字幕编排" in body
    assert "目标时长" in body
    assert "偏差" in body
    assert "GPT-5.5" in body
    assert "renderAvConvergence()" in body
    assert "renderAvSubtitleUnits()" in body

    scripts = (Path(__file__).resolve().parents[1] / "web" / "templates" / "_task_workbench_scripts.html").read_text(
        encoding="utf-8"
    )
    assert "before_text" in scripts
    assert "after_text" in scripts
    assert "duration_ratio" in scripts
    assert "reason" in scripts
    assert "avSyncGranularity" in scripts
    assert "subtitle_units" in scripts


def test_av_project_detail_uses_multilingual_detail_shell(authed_client_no_db, monkeypatch):
    task = store.create("task-project-av-shell", "video.mp4", "output/task-project-av-shell")
    store.update(
        task["id"],
        pipeline_version="av",
        type="av_translate",
        av_translate_inputs={
            "target_language": "de",
            "target_language_name": "German",
            "target_market": "DE",
            "sync_granularity": "sentence",
            "product_overrides": {},
        },
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "display_name": "demo",
        "original_filename": "video.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(store.get(task["id"]), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.projects.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: "openrouter")
    monkeypatch.setattr("web.routes.projects.query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/projects/task-project-av-shell")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="taskStatusCard"' in body
    assert 'id="voice-selector-multi"' in body
    assert 'class="vs-preview-card"' in body
    assert 'id="avConvergencePanel"' in body
    assert 'id="avSubtitleUnitsPanel"' in body
    assert 'apiBase: "/api/tasks"' in body
    assert 'detailMode: "av_sync"' in body
    assert 'voiceLanguage: "de"' in body
    assert 'href="/video-translate-av-sync"' in body


def test_av_task_subtitle_preview_supports_shared_detail_shell(authed_client_no_db):
    task = store.create("task-av-preview-shell", "video.mp4", "output/task-av-preview-shell", user_id=1)
    store.update(
        task["id"],
        pipeline_version="av",
        subtitle_font="Impact",
        subtitle_size=18,
        subtitle_position_y=0.72,
    )

    response = authed_client_no_db.get("/api/tasks/task-av-preview-shell/subtitle-preview")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["video_url"] == "/api/tasks/task-av-preview-shell/artifact/source_video"
    assert payload["subtitle_font"] == "Impact"
    assert payload["subtitle_size"] == 18
    assert payload["subtitle_position_y"] == 0.72


def test_av_task_voice_library_supports_shared_detail_shell(authed_client_no_db, monkeypatch):
    task = store.create("task-av-voice-shell", "video.mp4", "output/task-av-voice-shell", user_id=1)
    store.update(
        task["id"],
        pipeline_version="av",
        target_lang="de",
        av_translate_inputs={"target_language": "de"},
        selected_voice_id="voice-a",
        steps={"extract": "done", "asr": "done", "voice_match": "waiting"},
        voice_match_candidates=[{"voice_id": "voice-a", "similarity": 0.91}],
    )
    monkeypatch.setattr(
        "appcore.voice_library_browse.list_voices",
        lambda **kwargs: {"items": [{"voice_id": "voice-a", "name": "A"}], "total": 1},
    )
    monkeypatch.setattr("appcore.video_translate_defaults.resolve_default_voice", lambda *args, **kwargs: None)

    response = authed_client_no_db.get("/api/tasks/task-av-voice-shell/voice-library")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["items"] == [{"voice_id": "voice-a", "name": "A"}]
    assert payload["selected_voice_id"] == "voice-a"
    assert payload["voice_match_ready"] is True


def test_av_task_confirm_voice_starts_pipeline_from_shared_detail_shell(tmp_path, authed_client_no_db, monkeypatch):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake")
    task = store.create("task-av-confirm-shell", str(video_path), str(tmp_path), user_id=1)
    store.update(
        task["id"],
        pipeline_version="av",
        target_lang="de",
        av_translate_inputs={"target_language": "de", "target_market": "DE", "sync_granularity": "sentence"},
    )
    started = {}
    monkeypatch.setattr(
        "web.routes.task.pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    response = authed_client_no_db.post(
        "/api/tasks/task-av-confirm-shell/confirm-voice",
        json={
            "voice_id": "voice-a",
            "voice_name": "A",
            "subtitle_font": "Impact",
            "subtitle_size": 18,
            "subtitle_position_y": 0.72,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["voice_id"] == "voice-a"
    assert started == {"task_id": "task-av-confirm-shell", "user_id": 1}
    updated = store.get("task-av-confirm-shell")
    assert updated["voice_id"] == "voice-a"
    assert updated["selected_voice_id"] == "voice-a"
    assert updated["subtitle_size"] == 18
    assert updated["subtitle_position_y"] == 0.72


def test_av_rewrite_warning_filter_includes_warning_long():
    scripts = (Path(__file__).resolve().parents[1] / "web" / "templates" / "_task_workbench_scripts.html").read_text(
        encoding="utf-8"
    )

    assert 'status === "warning_long"' in scripts
    assert 'status === "warning_short"' in scripts
    assert 'status === "warning_overshoot"' in scripts
    assert 'const statusLabel = isShort ? "偏短" : "超时";' in scripts


def test_project_detail_page_bootstraps_persisted_task_state(authed_client_no_db, monkeypatch):
    task = store.create("task-project-state", "video.mp4", "output/task-project-state")
    store.update(
        "task-project-state",
        interactive_review=True,
        current_review_step="alignment",
        steps={
            "extract": "done",
            "asr": "done",
            "alignment": "waiting",
            "translate": "pending",
            "tts": "pending",
            "subtitle": "pending",
            "compose": "pending",
            "export": "pending",
        },
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "video.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(store.get("task-project-state"), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.projects.query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/projects/task-project-state")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "task-project-state" in body
    assert "\"interactive_review\": true" in body.lower()
    assert "\"current_review_step\": \"alignment\"" in body.lower()


def test_project_detail_page_defaults_source_language_to_zh(authed_client_no_db, monkeypatch):
    task = store.create("task-project-default-lang", "video.mp4", "output/task-project-default-lang")
    row = {
        "id": task["id"],
        "user_id": 1,
        "display_name": "demo",
        "original_filename": "video.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(task, ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.projects.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr("web.routes.projects.query_one", lambda sql, args: row)
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: "openrouter")

    response = authed_client_no_db.get("/projects/task-project-default-lang")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-default-source-language="zh"' in body
    assert 'data-lang="zh" class="sl-btn sl-active"' in body


def test_project_detail_page_renders_gpt_5_mini_translate_option(authed_client_no_db, monkeypatch):
    task = store.create("task-project-gpt-option", "video.mp4", "output/task-project-gpt-option")
    row = {
        "id": task["id"],
        "user_id": 1,
        "display_name": "demo",
        "original_filename": "video.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(task, ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.projects.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr("web.routes.projects.query_one", lambda sql, args: row)
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: "gpt_5_mini")

    response = authed_client_no_db.get("/projects/task-project-gpt-option")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'value="gpt_5_mini"' in body
    assert "GPT 5-mini (OpenRouter)" in body


def test_project_detail_page_renders_gpt_5_5_translate_option(authed_client_no_db, monkeypatch):
    task = store.create("task-project-gpt55-option", "video.mp4", "output/task-project-gpt55-option")
    row = {
        "id": task["id"],
        "user_id": 1,
        "display_name": "demo",
        "original_filename": "video.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(task, ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.projects.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr("web.routes.projects.query_one", lambda sql, args: row)
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: "gpt_5_5")

    response = authed_client_no_db.get("/projects/task-project-gpt55-option")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'value="gpt_5_5"' in body
    assert "GPT-5.5 (OpenRouter)" in body


def test_de_translate_detail_page_defaults_source_language_to_en(authed_client_no_db, monkeypatch):
    task = store.create("task-de-default-lang", "video.mp4", "output/task-de-default-lang", user_id=1)
    store.update("task-de-default-lang", type="de_translate")
    row = {
        "id": task["id"],
        "user_id": 1,
        "display_name": "demo",
        "original_filename": "video.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(store.get("task-de-default-lang"), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.de_translate.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr("web.routes.de_translate.db_query_one", lambda sql, args: row)
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: "openrouter")

    response = authed_client_no_db.get("/de-translate/task-de-default-lang")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-default-source-language="en"' in body
    assert 'data-lang="en" class="sl-btn sl-active"' in body


def test_fr_translate_detail_page_defaults_source_language_to_en(authed_client_no_db, monkeypatch):
    task = store.create("task-fr-default-lang", "video.mp4", "output/task-fr-default-lang", user_id=1)
    store.update("task-fr-default-lang", type="fr_translate")
    row = {
        "id": task["id"],
        "user_id": 1,
        "display_name": "demo",
        "original_filename": "video.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(store.get("task-fr-default-lang"), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.fr_translate.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr("web.routes.fr_translate.db_query_one", lambda sql, args: row)
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: "openrouter")

    response = authed_client_no_db.get("/fr-translate/task-fr-default-lang")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-default-source-language="en"' in body
    assert 'data-lang="en" class="sl-btn sl-active"' in body


def test_subtitle_removal_pages_render(authed_client_no_db, monkeypatch):
    def fake_create_subtitle_removal(task_id, video_path, task_dir, original_filename=None, user_id=None):
        task = store.create(task_id, video_path, task_dir, original_filename=original_filename, user_id=user_id)
        store.update(task_id, type="subtitle_removal")
        return task

    monkeypatch.setattr(store, "create_subtitle_removal", fake_create_subtitle_removal, raising=False)
    task = store.create_subtitle_removal("sr-page", "uploads/sr-page.mp4", "output/sr-page", original_filename="demo.mp4", user_id=1)
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "demo.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "type": "subtitle_removal",
        "state_json": json.dumps(task, ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args: row)

    upload_response = authed_client_no_db.get("/subtitle-removal")
    detail_response = authed_client_no_db.get("/subtitle-removal/sr-page")

    assert upload_response.status_code == 200
    upload_body = upload_response.get_data(as_text=True)
    assert "字幕移除" in upload_body
    assert 'id="srUploadInput"' in upload_body
    assert 'id="srPickVideoButton"' in upload_body
    assert 'id="srUploadDropzone"' in upload_body
    assert "暂不支持文件选择" not in upload_body
    assert detail_response.status_code == 200
    detail_body = detail_response.get_data(as_text=True)
    assert "全屏去除" in detail_body
    assert "框选去除" in detail_body
    assert "join_subtitle_removal_task" in detail_body
    assert 'socket.on("connect", joinFn);' in detail_body
    assert "连接任务房间" not in detail_body


def test_subtitle_removal_detail_shell_is_read_only(authed_client_no_db, monkeypatch):
    task = store.create("sr-readonly", "uploads/sr-readonly.mp4", "output/sr-readonly", original_filename="demo.mp4", user_id=1)
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "demo.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "type": "subtitle_removal",
        "state_json": json.dumps(task, ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/subtitle-removal/sr-readonly")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "disabled" in body
    assert "sr-mode-readonly" in body


def test_subtitle_removal_detail_shell_exposes_selection_stage_hooks(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal("sr-selection", "uploads/sr-selection.mp4", "output/sr-selection", original_filename="demo.mp4", user_id=1)
    store.update(
        task["id"],
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
        remove_mode="box",
        selection_box={"l": 0, "t": 0, "w": 720, "h": 1280},
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "demo.mp4",
        "status": "ready",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "type": "subtitle_removal",
        "state_json": json.dumps(store.get(task["id"]), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/subtitle-removal/sr-selection")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "srSelectionOverlay" in body
    assert "computeSelectionBox" in body
    assert "提交去字幕任务" in body
    assert "全屏去除" in body
    assert "框选去除" in body
    assert "Task 4" not in body


def test_subtitle_removal_detail_shell_exposes_result_action_hooks(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-result-shell",
        "uploads/sr-result-shell.mp4",
        "output/sr-result-shell",
        original_filename="demo.mp4",
        user_id=1,
    )
    store.update(
        task["id"],
        status="done",
        result_tos_key="artifacts/1/sr-result-shell/subtitle_removal/result.cleaned.mp4",
        result_video_path="",
        provider_task_id="provider-task-1",
        provider_status="success",
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "demo.mp4",
        "status": "done",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "type": "subtitle_removal",
        "state_json": json.dumps(store.get(task["id"]), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/subtitle-removal/sr-result-shell")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "srResultPanel" in body
    assert "srResumeSubtitleRemoval" in body
    assert "srResubmitSubtitleRemoval" in body
    assert "srDeleteSubtitleRemoval" in body
    assert "结果操作" in body
    assert '<button id="srResumeSubtitleRemoval" type="button" class="btn btn-secondary">继续轮询</button>' in body
    assert '<button id="srResubmitSubtitleRemoval" type="button" class="btn btn-secondary">重提</button>' in body
    assert '<button id="srDeleteSubtitleRemoval" type="button" class="btn btn-danger">删除</button>' in body
    assert "artifact/result" in body
    assert "download/result" in body


def test_subtitle_removal_detail_shell_shows_result_actions_for_local_result_only(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-result-local",
        "uploads/sr-result-local.mp4",
        "output/sr-result-local",
        original_filename="demo.mp4",
        user_id=1,
    )
    store.update(
        task["id"],
        status="done",
        result_tos_key="",
        result_video_path="/tmp/result.cleaned.mp4",
        provider_task_id="provider-task-1",
        provider_status="success",
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "demo.mp4",
        "status": "done",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "type": "subtitle_removal",
        "state_json": json.dumps(store.get(task["id"]), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/subtitle-removal/sr-result-local")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "srResultPanel" in body
    assert "artifact/result" in body
    assert "download/result" in body


def test_subtitle_removal_detail_shell_limits_result_player_to_360x640(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-result-size",
        "uploads/sr-result-size.mp4",
        "output/sr-result-size",
        original_filename="demo.mp4",
        user_id=1,
    )
    store.update(
        task["id"],
        status="done",
        result_tos_key="artifacts/1/sr-result-size/subtitle_removal/result.cleaned.mp4",
        result_video_path="",
        provider_task_id="provider-task-1",
        provider_status="success",
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "demo.mp4",
        "status": "done",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "type": "subtitle_removal",
        "state_json": json.dumps(store.get(task["id"]), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/subtitle-removal/sr-result-size")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert ".sr-result-preview {" in body
    assert "width: min(360px, 100%);" in body
    assert "aspect-ratio: 9 / 16;" in body
    assert "max-height: 640px;" in body
    assert ".sr-result-video {" in body
    assert "object-fit: contain;" in body


def test_subtitle_removal_detail_shell_exposes_back_to_list_entry(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-back-link",
        "uploads/sr-back-link.mp4",
        "output/sr-back-link",
        original_filename="demo.mp4",
        user_id=1,
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "demo.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "type": "subtitle_removal",
        "state_json": json.dumps(store.get(task["id"]), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/subtitle-removal/sr-back-link")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'class="sr-back-link"' in body
    assert 'href="/subtitle-removal"' in body
    assert "返回列表" in body


def test_subtitle_removal_detail_shell_renders_bottom_compare_previews(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-compare-shell",
        "uploads/sr-compare-shell.mp4",
        "output/sr-compare-shell",
        original_filename="demo.mp4",
        user_id=1,
    )
    store.update(
        task["id"],
        status="done",
        source_tos_key="uploads/1/sr-compare-shell/demo.mp4",
        result_tos_key="artifacts/1/sr-compare-shell/subtitle_removal/result.cleaned.mp4",
        provider_task_id="provider-task-1",
        provider_status="success",
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "original_filename": "demo.mp4",
        "status": "done",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "type": "subtitle_removal",
        "state_json": json.dumps(store.get(task["id"]), ensure_ascii=False),
    }
    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args: row)

    response = authed_client_no_db.get("/subtitle-removal/sr-compare-shell")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="srCompareCard"' in body
    assert 'id="srCompareGrid"' in body
    assert "grid-template-columns: repeat(2, minmax(0, 360px));" in body
    assert "srCompareOriginalPanel" in body
    assert "srCompareResultPanel" in body
    assert "source_video_url" in body


def test_subtitle_removal_join_uses_persisted_task_state_when_memory_is_cold(authed_client_no_db, monkeypatch):
    joined_rooms = []

    monkeypatch.setattr("web.app.join_room", lambda room: joined_rooms.append(room))
    monkeypatch.setattr("web.store.get", lambda task_id: None)
    monkeypatch.setattr(
        "appcore.db.query_one",
        lambda sql, args: {
            "state_json": json.dumps({"id": "sr-db", "type": "subtitle_removal"}, ensure_ascii=False),
            "user_id": 1,
            "display_name": "",
            "original_filename": "demo.mp4",
        },
    )

    sio_client = socketio.test_client(authed_client_no_db.application, flask_test_client=authed_client_no_db)
    try:
        sio_client.emit("join_subtitle_removal_task", {"task_id": "sr-db"})
        assert joined_rooms == ["sr-db"]
    finally:
        sio_client.disconnect()


def test_create_app_does_not_resume_subtitle_removal_on_startup(monkeypatch):
    called = []

    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    monkeypatch.delenv("DISABLE_STARTUP_RECOVERY", raising=False)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: called.append("mark_generic"))
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: called.append("mark_bulk"))
    monkeypatch.setattr(
        "web.routes.subtitle_removal.resume_inflight_tasks",
        lambda: called.append("resume"),
    )

    app = create_app()

    assert app is not None
    assert called == ["mark_generic", "mark_bulk"]


def test_layout_contains_subtitle_removal_nav_icon(authed_client_no_db):
    response = authed_client_no_db.get("/subtitle-removal")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'href="/subtitle-removal"' in body
    assert '<span class="nav-icon">🧽</span>' in body


def test_layout_hides_api_config_nav_for_normal_user(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/subtitle-removal")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "API 配置" not in body
    assert 'href="/settings"' not in body


def test_layout_contains_user_settings_nav_for_normal_user(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/subtitle-removal")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "用户设置" in body
    assert 'href="/user-settings"' in body


def test_user_settings_page_contains_default_jianying_project_root(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.user_settings.resolve_extra", lambda user_id, service: {})

    response = authed_client_no_db.get("/user-settings")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "jianying_project_root" in body
    assert DEFAULT_JIANYING_PROJECT_ROOT in body
    assert "data-settings-copy" in body


def test_user_settings_page_saves_custom_jianying_project_root(authed_client_no_db, monkeypatch):
    custom_root = r"D:\JianyingDrafts"
    captured = []

    def fake_set_key(user_id, service, key_value, extra=None):
        captured.append((user_id, service, key_value, extra))

    monkeypatch.setattr("web.routes.user_settings.resolve_extra", lambda user_id, service: {"project_root": custom_root})
    monkeypatch.setattr("web.routes.user_settings.set_key", fake_set_key)

    response = authed_client_no_db.post(
        "/user-settings",
        data={"jianying_project_root": custom_root},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (1, "jianying", "", {"project_root": custom_root}) in captured


def test_settings_page_no_longer_contains_jianying_project_root(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.settings.get_all", lambda user_id: {})
    monkeypatch.setattr("web.routes.settings.llm_bindings.list_all", lambda: [])
    monkeypatch.setattr("appcore.pushes.get_push_target_url", lambda: "")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_base_url", lambda: "")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_authorization", lambda: "")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_cookie", lambda: "")

    response = authed_client_no_db.get("/settings")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "jianying_project_root" not in body
    assert "导出目录设置" not in body


def test_task_detail_returns_artifacts_structure(authed_client_no_db):
    store.create("task-preview", "video.mp4", "output/task-preview", user_id=1)

    response = authed_client_no_db.get("/api/tasks/task-preview")

    assert response.status_code == 200
    payload = response.get_json()
    assert "artifacts" in payload
    assert payload["artifacts"] == {}


def test_store_create_initializes_default_variants():
    task = store.create("task-variants", "video.mp4", "output/task-variants")

    assert set(task["variants"].keys()) == {"normal", "hook_cta"}
    assert "CTA" in task["variants"]["hook_cta"]["label"]
    assert task["variants"]["normal"]["label"] == "普通版"


def test_artifact_route_serves_whitelisted_preview_file(tmp_path, authed_client_no_db):
    audio_path = tmp_path / "preview.mp3"
    audio_path.write_bytes(b"audio-preview")
    store.create("task-file", "video.mp4", str(tmp_path), user_id=1)
    store.update("task-file", preview_files={"audio_extract": str(audio_path)})

    response = authed_client_no_db.get("/api/tasks/task-file/artifact/audio_extract")

    assert response.status_code == 200
    assert response.data == b"audio-preview"


def test_artifact_route_serves_variant_preview_file(tmp_path, authed_client_no_db):
    video_path = tmp_path / "preview.mp4"
    video_path.write_bytes(b"video-preview")
    store.create("task-variant-file", "video.mp4", str(tmp_path), user_id=1)
    store.update_variant("task-variant-file", "normal", preview_files={"soft_video": str(video_path)})

    response = authed_client_no_db.get("/api/tasks/task-variant-file/artifact/soft_video?variant=normal")

    assert response.status_code == 200
    assert response.data == b"video-preview"


def test_artifact_route_rejects_unknown_name(tmp_path, authed_client_no_db):
    store.create("task-bad", "video.mp4", str(tmp_path), user_id=1)

    response = authed_client_no_db.get("/api/tasks/task-bad/artifact/not_allowed")

    assert response.status_code == 404


def test_artifact_route_falls_back_to_output_dir_when_task_state_is_missing(tmp_path, authed_client_no_db, monkeypatch):
    task_id = "task-restored"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    preview_path = task_dir / f"{task_id}_soft.mp4"
    preview_path.write_bytes(b"soft-video-preview")
    store.create(task_id, "video.mp4", str(task_dir), user_id=1)
    monkeypatch.setattr("web.routes.task.OUTPUT_DIR", str(tmp_path))

    response = authed_client_no_db.get(f"/api/tasks/{task_id}/artifact/soft_video")

    assert response.status_code == 200
    assert response.data == b"soft-video-preview"


def test_alignment_route_compiles_script_segments(authed_client_no_db):
    task = store.create("task-1", "video.mp4", "output/task-1", user_id=1)
    task["utterances"] = [
        {"text": "浣犲ソ", "start_time": 0.0, "end_time": 0.8, "words": []},
        {"text": "涓栫晫", "start_time": 0.8, "end_time": 1.6, "words": []},
    ]

    response = authed_client_no_db.put(
        "/api/tasks/task-1/alignment",
        json={"break_after": [False, True]},
    )

    assert response.status_code == 200
    saved = store.get("task-1")
    assert saved["_alignment_confirmed"] is True
    assert saved["script_segments"][0]["text"] == "浣犲ソ涓栫晫"
    assert saved["artifacts"]["alignment"]["items"][1]["segments"][0]["text"] == "浣犲ソ涓栫晫"


def test_segments_route_updates_translate_artifact(authed_client_no_db):
    store.create("task-translate", "video.mp4", "output/task-translate", user_id=1)
    store.update(
        "task-translate",
        script_segments=[{"text": "你好世界", "translated": "Hello world", "start_time": 0.0, "end_time": 1.6}],
        segments=[{"text": "你好世界", "translated": "Hello world", "start_time": 0.0, "end_time": 1.6}],
    )

    response = authed_client_no_db.put(
        "/api/tasks/task-translate/segments",
        json={"segments": [{"text": "你好世界", "translated": "Hello there", "start_time": 0.0, "end_time": 1.6}]},
    )

    assert response.status_code == 200
    saved = store.get("task-translate")
    assert saved["_segments_confirmed"] is True
    normal_translate = saved["variants"]["normal"]["artifacts"]["translate"]
    # The translate artifact now uses text_item + sentences layout, not segments
    assert normal_translate["items"][1]["content"] == "Hello there"  # "整段本土化英文" text_item


def test_segments_route_updates_localized_translation_for_future_tts(authed_client_no_db):
    store.create("task-translate-localized", "video.mp4", "output/task-translate-localized", user_id=1)
    store.update(
        "task-translate-localized",
        source_full_text_zh="你好世界",
        script_segments=[{"index": 0, "text": "你好世界", "start_time": 0.0, "end_time": 1.6}],
        segments=[{"index": 0, "text": "你好世界", "translated": "Hello world", "start_time": 0.0, "end_time": 1.6}],
    )

    response = authed_client_no_db.put(
        "/api/tasks/task-translate-localized/segments",
        json={"segments": [{"index": 0, "text": "你好世界", "translated": "Hello there", "start_time": 0.0, "end_time": 1.6}]},
    )

    assert response.status_code == 200
    saved = store.get("task-translate-localized")
    assert saved["script_segments"][0]["text"] == "你好世界"
    assert saved["localized_translation"]["full_text"] == "Hello there"
    assert saved["localized_translation"]["sentences"][0]["source_segment_indices"] == [0]


def test_task_payload_exposes_tts_script_and_corrected_subtitle(authed_client_no_db):
    store.create("task-payload", "video.mp4", "output/task-payload", user_id=1)
    store.update(
        "task-payload",
        tts_script={"full_text": "Say it smooth.", "blocks": [], "subtitle_chunks": []},
        corrected_subtitle={"chunks": [], "srt_content": "1\n00:00:00,000 --> 00:00:01,000\nSay it smooth.\n"},
    )

    response = authed_client_no_db.get("/api/tasks/task-payload")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tts_script"]["full_text"] == "Say it smooth."
    assert "Say it smooth." in payload["corrected_subtitle"]["srt_content"]


def test_voice_routes_support_crud(authed_client_no_db, monkeypatch):
    _voices: dict[int, dict] = {}
    _next_id = [1]

    class FakeVoiceLibrary:
        def ensure_defaults(self, user_id):
            pass

        def list_voices(self, user_id):
            return [v for v in _voices.values() if v.get("user_id") == user_id]

        def create_voice(self, user_id, body):
            vid = _next_id[0]
            _next_id[0] += 1
            voice = {"id": vid, "user_id": user_id, **body}
            _voices[vid] = voice
            return voice

        def get_voice(self, voice_id, user_id):
            v = _voices.get(voice_id)
            return v if v and v.get("user_id") == user_id else None

        def update_voice(self, voice_id, user_id, body):
            v = _voices[voice_id]
            v.update(body)
            return v

        def delete_voice(self, voice_id, user_id):
            _voices.pop(voice_id, None)

    monkeypatch.setattr("pipeline.voice_library.get_voice_library", lambda: FakeVoiceLibrary())
    monkeypatch.setattr("web.routes.voice.get_voice_library", lambda: FakeVoiceLibrary())

    # Use a single fake instance for consistent state
    fake_lib = FakeVoiceLibrary()
    monkeypatch.setattr("web.routes.voice.get_voice_library", lambda: fake_lib)

    created = authed_client_no_db.post(
        "/api/voices",
        json={
            "name": "Taylor",
            "gender": "female",
            "elevenlabs_voice_id": "voice_1",
            "description": "Warm and bright",
            "style_tags": ["warm", "beauty"],
        },
    )
    assert created.status_code == 201
    voice_id = created.get_json()["voice"]["id"]

    updated = authed_client_no_db.put(
        f"/api/voices/{voice_id}",
        json={"description": "Warm and updated"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["voice"]["description"] == "Warm and updated"

    listed = authed_client_no_db.get("/api/voices")
    assert listed.status_code == 200
    assert listed.get_json()["voices"][0]["id"] == voice_id

    deleted = authed_client_no_db.delete(f"/api/voices/{voice_id}")
    assert deleted.status_code == 200


def test_download_route_can_return_normal_capcut_archive(tmp_path, authed_client_no_db, monkeypatch):
    archive_path = tmp_path / "capcut_normal.zip"
    archive_path.write_bytes(b"capcut-archive")
    store.create("task-download-variant", "video.mp4", str(tmp_path), user_id=1)
    store.update("task-download-variant", display_name="example")
    store.update_variant(
        "task-download-variant",
        "normal",
        exports={"capcut_archive": str(archive_path)},
    )
    monkeypatch.setattr(
        "web.services.artifact_download.upload_capcut_archive_for_current_user",
        lambda *a, **kw: None,
    )

    response = authed_client_no_db.get("/api/tasks/task-download-variant/download/capcut?variant=normal")

    assert response.status_code == 200
    assert response.data == b"capcut-archive"
    assert 'filename=example_capcut_normal.zip' in response.headers["Content-Disposition"]


def test_download_route_redirects_to_tos_when_uploaded_artifact_exists(authed_client_no_db, monkeypatch):
    store.create("task-download-tos", "video.mp4", "output/task-download-tos", user_id=1)
    store.update(
        "task-download-tos",
        tos_uploads={
            "normal:soft_video": {
                "tos_key": "artifacts/1/task-download-tos/normal/example_soft.mp4",
                "artifact_kind": "soft_video",
                "variant": "normal",
            }
        },
    )

    monkeypatch.setattr(
        "web.routes.task.tos_clients.generate_signed_download_url",
        lambda object_key: f"https://signed.example.com/{object_key}",
    )

    response = authed_client_no_db.get("/api/tasks/task-download-tos/download/soft")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://signed.example.com/artifacts/1/task-download-tos/normal/example_soft.mp4"


def test_download_route_prefers_local_file_for_local_primary_task(tmp_path, authed_client_no_db, monkeypatch):
    result_path = tmp_path / "hard.mp4"
    result_path.write_bytes(b"video")

    store.create("task-download-local-primary", "video.mp4", str(tmp_path), user_id=1)
    store.update(
        "task-download-local-primary",
        delivery_mode="local_primary",
        result={"hard_video": str(result_path)},
        tos_uploads={
            "normal:hard_video": {
                "tos_key": "artifacts/1/task-download-local-primary/normal/example_hard.mp4",
                "artifact_kind": "hard_video",
                "variant": "normal",
            }
        },
    )

    monkeypatch.setattr(
        "web.services.artifact_download.tos_clients.generate_signed_download_url",
        lambda object_key: f"https://signed.example.com/{object_key}",
    )

    response = authed_client_no_db.get("/api/tasks/task-download-local-primary/download/hard")

    assert response.status_code == 200
    assert response.data == b"video"


def test_download_route_rejects_local_capcut_fallback_for_pure_tos_task(tmp_path, authed_client_no_db, monkeypatch):
    archive_path = tmp_path / "capcut_normal.zip"
    archive_path.write_bytes(b"capcut-archive")
    store.create("task-download-pure-tos-missing", "video.mp4", str(tmp_path), user_id=1)
    store.update(
        "task-download-pure-tos-missing",
        delivery_mode="pure_tos",
        display_name="example",
    )
    store.update_variant(
        "task-download-pure-tos-missing",
        "normal",
        exports={"capcut_archive": str(archive_path)},
    )
    monkeypatch.setattr(
        "web.services.artifact_download.upload_capcut_archive_for_current_user",
        lambda *a, **kw: None,
    )

    response = authed_client_no_db.get("/api/tasks/task-download-pure-tos-missing/download/capcut?variant=normal")

    assert response.status_code == 409
    assert "TOS" in response.get_json()["error"]


def test_download_route_redirects_capcut_for_pure_tos_task(tmp_path, authed_client_no_db, monkeypatch):
    archive_path = tmp_path / "capcut_normal.zip"
    archive_path.write_bytes(b"capcut-archive")
    store.create("task-download-pure-tos-capcut", "video.mp4", str(tmp_path), user_id=1)
    store.update(
        "task-download-pure-tos-capcut",
        delivery_mode="pure_tos",
        display_name="example",
    )
    store.update_variant(
        "task-download-pure-tos-capcut",
        "normal",
        exports={"capcut_archive": str(archive_path)},
    )
    monkeypatch.setattr(
        "web.services.artifact_download.upload_capcut_archive_for_current_user",
        lambda *a, **kw: {
            "tos_key": "artifacts/1/task-download-pure-tos-capcut/normal/example_capcut_normal.zip",
        },
    )
    monkeypatch.setattr(
        "web.services.artifact_download.tos_clients.generate_signed_download_url",
        lambda object_key: f"https://signed.example.com/{object_key}",
    )

    response = authed_client_no_db.get("/api/tasks/task-download-pure-tos-capcut/download/capcut?variant=normal")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://signed.example.com/artifacts/1/task-download-pure-tos-capcut/normal/example_capcut_normal.zip"


def test_download_route_rewrites_capcut_project_paths_for_current_user(tmp_path, authed_client_no_db, monkeypatch):
    project_dir = tmp_path / "capcut_normal"
    resources_dir = project_dir / "Resources" / "auto_generated"
    resources_dir.mkdir(parents=True)
    (resources_dir / "tts_full.normal.mp3").write_bytes(b"audio")
    (project_dir / "draft_content.json").write_text(
        json.dumps(
            {
                "materials": {
                    "audios": [
                        {
                            "path": str(resources_dir / "tts_full.normal.mp3"),
                        }
                    ]
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (project_dir / "draft_meta_info.json").write_text(
        json.dumps({"draft_fold_path": "", "draft_name": ""}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_path = project_dir / "codex_export_manifest.json"
    manifest_path.write_text(
        json.dumps({"timeline_manifest": {"segments": [{"tts_path": "/opt/autovideosrt/output/seg_0001.mp3"}]}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    archive_path = tmp_path / "capcut_normal.zip"
    archive_path.write_bytes(b"stale-archive")

    store.create("task-download-rewrite", "video.mp4", str(tmp_path), user_id=1)
    store.update_variant(
        "task-download-rewrite",
        "normal",
        exports={
            "capcut_project": str(project_dir),
            "capcut_archive": str(archive_path),
            "capcut_manifest": str(manifest_path),
        },
    )

    monkeypatch.setattr(
        "web.services.artifact_download.resolve_jianying_project_root",
        lambda user_id: DEFAULT_JIANYING_PROJECT_ROOT,
    )
    monkeypatch.setattr(
        "web.services.artifact_download.upload_capcut_archive_for_current_user",
        lambda *a, **kw: None,
    )

    response = authed_client_no_db.get("/api/tasks/task-download-rewrite/download/capcut?variant=normal")

    assert response.status_code == 200
    draft_content = json.loads((project_dir / "draft_content.json").read_text(encoding="utf-8"))
    assert draft_content["materials"]["audios"][0]["path"].startswith(DEFAULT_JIANYING_PROJECT_ROOT)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["timeline_manifest"]["segments"][0]["tts_path"] == ""
    assert store.get("task-download-rewrite")["variants"]["normal"]["exports"]["jianying_project_dir"].startswith(DEFAULT_JIANYING_PROJECT_ROOT)


def test_delete_route_cleans_source_and_artifact_tos_objects(tmp_path, authed_client_no_db, monkeypatch):
    task_dir = tmp_path / "task-delete"
    task_dir.mkdir()
    store.create("task-delete-tos", "video.mp4", str(task_dir), user_id=1)
    store.update(
        "task-delete-tos",
        source_tos_key="uploads/1/task-delete-tos/source.mp4",
        tos_uploads={
            "normal:soft_video": {
                "tos_key": "artifacts/1/task-delete-tos/normal/example_soft.mp4",
                "artifact_kind": "soft_video",
                "variant": "normal",
            }
        },
    )

    monkeypatch.setattr(
        "web.routes.task.db_query_one",
        lambda sql, args: {"id": "task-delete-tos", "task_dir": str(task_dir), "state_json": "{}", "user_id": 1},
    )
    monkeypatch.setattr("web.routes.task.db_execute", lambda sql, args: None)
    deleted_keys = []
    monkeypatch.setattr("web.routes.task.cleanup.delete_task_storage", lambda task: deleted_keys.extend(sorted(task["tos_keys"])))

    response = authed_client_no_db.delete("/api/tasks/task-delete-tos")

    assert response.status_code == 200
    assert deleted_keys == [
        "artifacts/1/task-delete-tos/normal/example_soft.mp4",
        "uploads/1/task-delete-tos/source.mp4",
    ]
    assert store.get("task-delete-tos")["status"] == "deleted"


def test_delete_route_cleans_persisted_tos_objects_when_store_is_cold(authed_client_no_db, monkeypatch):
    state = {
        "source_tos_key": "uploads/1/task-delete-cold/source.mp4",
        "tos_uploads": {
            "normal:soft_video": {
                "tos_key": "artifacts/1/task-delete-cold/normal/example_soft.mp4",
                "artifact_kind": "soft_video",
                "variant": "normal",
            }
        },
    }

    monkeypatch.setattr(
        "web.routes.task.db_query_one",
        lambda sql, args: {
            "id": "task-delete-cold",
            "task_dir": "",
            "state_json": json.dumps(state),
            "user_id": 1,
        },
    )
    monkeypatch.setattr("web.routes.task.db_execute", lambda sql, args: None)
    deleted_keys = []
    monkeypatch.setattr("web.routes.task.cleanup.delete_task_storage", lambda task: deleted_keys.extend(sorted(task["tos_keys"])))

    response = authed_client_no_db.delete("/api/tasks/task-delete-cold")

    assert response.status_code == 200
    assert deleted_keys == [
        "artifacts/1/task-delete-cold/normal/example_soft.mp4",
        "uploads/1/task-delete-cold/source.mp4",
    ]


def test_start_route_materializes_source_video_from_tos_before_processing(tmp_path, authed_client_no_db, monkeypatch):
    task_dir = tmp_path / "task-start-tos"
    task_dir.mkdir()
    video_path = tmp_path / "uploads" / "task-start-tos.mp4"
    store.create("task-start-tos", str(video_path), str(task_dir), user_id=1)
    store.update("task-start-tos", source_tos_key="uploads/1/task-start-tos/demo.mp4")

    downloaded = []
    started = []

    def fake_download_file(object_key, local_path):
        downloaded.append((object_key, local_path))
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_bytes(b"video")
        return local_path

    monkeypatch.setattr("web.routes.task.tos_clients.download_file", fake_download_file)
    monkeypatch.setattr("web.routes.task._extract_thumbnail", lambda video_path, task_dir: None)
    monkeypatch.setattr("web.routes.task.db_execute", lambda sql, args: None)
    monkeypatch.setattr("web.services.pipeline_runner.start", lambda task_id, user_id=None: started.append((task_id, user_id)))

    first_response = authed_client_no_db.post("/api/tasks/task-start-tos/start", json={})

    assert first_response.status_code == 200
    first_payload = first_response.get_json()
    assert first_payload["status"] == "source_ready"
    assert downloaded == [("uploads/1/task-start-tos/demo.mp4", str(video_path))]
    assert started == []

    second_response = authed_client_no_db.post("/api/tasks/task-start-tos/start", json={})
    assert second_response.status_code == 200
    second_payload = second_response.get_json()
    assert second_payload["status"] == "started"
    assert started == [("task-start-tos", 1)]


def test_admin_can_fetch_other_users_task_thumbnail(tmp_path, authed_client_no_db, monkeypatch):
    thumb = tmp_path / "foreign-thumbnail.jpg"
    thumb.write_bytes(b"jpeg-thumbnail")

    def fake_query_one(sql, args):
        if "user_id" in sql.lower():
            return None
        return {"thumbnail_path": str(thumb)}

    monkeypatch.setattr("web.routes.task.db_query_one", fake_query_one)

    response = authed_client_no_db.get("/api/tasks/foreign-task/thumbnail")

    assert response.status_code == 200
    assert response.data == b"jpeg-thumbnail"


def test_normal_user_cannot_fetch_other_users_task_thumbnail(tmp_path, authed_user_client_no_db, monkeypatch):
    thumb = tmp_path / "foreign-thumbnail.jpg"
    thumb.write_bytes(b"jpeg-thumbnail")

    def fake_query_one(sql, args):
        assert "user_id" in sql.lower()
        return None

    monkeypatch.setattr("web.routes.task.db_query_one", fake_query_one)

    response = authed_user_client_no_db.get("/api/tasks/foreign-task/thumbnail")

    assert response.status_code == 404


def test_start_route_persists_av_translate_inputs_and_pipeline_version(tmp_path, authed_client_no_db, monkeypatch):
    task_id = "task-start-av-inputs"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    video_path = tmp_path / "uploads" / f"{task_id}.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    store.create(task_id, str(video_path), str(task_dir), user_id=1)
    started = []

    monkeypatch.setattr(
        "web.services.pipeline_runner.start",
        lambda task_id, user_id=None: started.append((task_id, user_id)),
    )

    response = authed_client_no_db.post(
        f"/api/tasks/{task_id}/start",
        json={
            "voice_id": "auto",
            "target_language": "ja",
            "target_market": "JP",
            "sync_granularity": "sentence",
            "override_product_name": "Glow Serum",
            "override_brand": "Ocean Lab",
            "override_selling_points": "轻薄不黏腻\n夜间修护",
            "override_price": "¥299",
            "override_target_audience": "熬夜肌人群",
            "override_extra_info": "避免直译品牌 slogan",
        },
    )

    assert response.status_code == 200
    assert started == [(task_id, 1)]
    task = store.get(task_id)
    assert task["pipeline_version"] == "av"
    assert task["av_translate_inputs"]["target_language"] == "ja"
    assert task["av_translate_inputs"]["target_language_name"] == "Japanese"
    assert task["av_translate_inputs"]["target_market"] == "JP"
    assert task["av_translate_inputs"]["sync_granularity"] == "sentence"
    assert task["av_translate_inputs"]["product_overrides"] == {
        "product_name": "Glow Serum",
        "brand": "Ocean Lab",
        "selling_points": ["轻薄不黏腻", "夜间修护"],
        "price": "¥299",
        "target_audience": "熬夜肌人群",
        "extra_info": "避免直译品牌 slogan",
    }


def test_av_rewrite_sentence_route_updates_outputs_and_invalidates_compose(
    tmp_path,
    authed_client_no_db,
    monkeypatch,
):
    task_id = "task-av-rewrite"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    video_path = tmp_path / "uploads" / f"{task_id}.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    full_audio_path = task_dir / "tts_full.av.mp3"
    full_audio_path.write_bytes(b"old-full-audio")
    srt_path = task_dir / "subtitle.av.srt"
    srt_path.write_text("old srt", encoding="utf-8")
    hard_video_path = task_dir / f"{task_id}_hard.av.mp4"
    hard_video_path.write_bytes(b"old-hard-video")
    capcut_archive_path = task_dir / "capcut_av.zip"
    capcut_archive_path.write_bytes(b"old-capcut")
    seg0_path = task_dir / "seg_0000.mp3"
    seg1_path = task_dir / "seg_0001.mp3"
    seg0_path.write_bytes(b"old-seg-0")
    seg1_path.write_bytes(b"old-seg-1")

    store.create(task_id, str(video_path), str(task_dir), user_id=1)
    store.update(
        task_id,
        status="done",
        pipeline_version="av",
        av_translate_inputs={
            "target_language": "en",
            "target_language_name": "English",
            "target_market": "US",
            "sync_granularity": "hybrid",
            "product_overrides": {},
        },
        shot_notes={
            "global": {
                "product_name": "Glow Serum",
                "category": "护肤精华",
                "overall_theme": "海边清透护肤",
            },
            "sentences": [],
        },
        steps={
            "extract": "done",
            "asr": "done",
            "alignment": "done",
            "translate": "done",
            "tts": "done",
            "subtitle": "done",
            "compose": "done",
            "export": "done",
        },
        step_messages={
            "extract": "",
            "asr": "",
            "alignment": "",
            "translate": "",
            "tts": "",
            "subtitle": "",
            "compose": "旧成片仍可下载",
            "export": "旧工程包仍可下载",
        },
        result={"hard_video": str(hard_video_path)},
        exports={"capcut_archive": str(capcut_archive_path)},
        artifacts={
            "compose": {"items": [{"type": "video", "artifact": "hard_video", "label": "硬字幕视频"}]},
            "export": {"items": [{"type": "download", "label": "CapCut 工程包", "url": "/x.zip"}]},
        },
        preview_files={
            "hard_video": str(hard_video_path),
            "tts_full_audio": str(full_audio_path),
            "srt": str(srt_path),
        },
        tos_uploads={
            "av:srt": {"tos_key": "artifacts/1/task-av-rewrite/av/subtitle.av.srt", "artifact_kind": "srt", "variant": "av"},
            "av:hard_video": {"tos_key": "artifacts/1/task-av-rewrite/av/task_av_hard.mp4", "artifact_kind": "hard_video", "variant": "av"},
            "av:capcut_archive": {"tos_key": "artifacts/1/task-av-rewrite/av/capcut_av.zip", "artifact_kind": "capcut_archive", "variant": "av"},
        },
    )
    store.update_variant(
        task_id,
        "av",
        label="音画同步版",
        voice_id="voice-db-id",
        localized_translation={
            "full_text": "Old opening line Keep this one",
            "sentences": [
                {"index": 0, "asr_index": 0, "text": "Old opening line", "source_segment_indices": [0]},
                {"index": 1, "asr_index": 1, "text": "Keep this one", "source_segment_indices": [1]},
            ],
        },
        tts_result={
            "full_audio_path": str(full_audio_path),
            "segments": [
                {
                    "index": 0,
                    "asr_index": 0,
                    "translated": "Old opening line",
                    "text": "Old opening line",
                    "tts_duration": 2.4,
                    "tts_path": str(seg0_path),
                },
                {
                    "index": 1,
                    "asr_index": 1,
                    "translated": "Keep this one",
                    "text": "Keep this one",
                    "tts_duration": 1.2,
                    "tts_path": str(seg1_path),
                },
            ],
        },
        tts_audio_path=str(full_audio_path),
        srt_path=str(srt_path),
        corrected_subtitle={"srt_content": "old srt"},
        result={"hard_video": str(hard_video_path)},
        exports={"capcut_archive": str(capcut_archive_path)},
        preview_files={
            "tts_full_audio": str(full_audio_path),
            "hard_video": str(hard_video_path),
        },
        sentences=[
            {
                "asr_index": 0,
                "start_time": 0.0,
                "end_time": 2.0,
                "target_duration": 2.0,
                "target_chars_range": [10, 18],
                "text": "Old opening line",
                "est_chars": 16,
                "tts_path": str(seg0_path),
                "tts_duration": 2.4,
                "speed": 1.12,
                "rewrite_rounds": 2,
                "status": "warning_overshoot",
            },
            {
                "asr_index": 1,
                "start_time": 2.0,
                "end_time": 3.2,
                "target_duration": 1.2,
                "target_chars_range": [6, 10],
                "text": "Keep this one",
                "est_chars": 13,
                "tts_path": str(seg1_path),
                "tts_duration": 1.2,
                "speed": 1.0,
                "rewrite_rounds": 0,
                "status": "ok",
            },
        ],
    )

    generated = []
    rebuilt = []

    monkeypatch.setattr(
        "web.routes.task.tts.get_voice_by_id",
        lambda voice_id, user_id: {"id": voice_id, "elevenlabs_voice_id": "voice-el-id"},
    )

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        generated.append({"text": text, "voice_id": voice_id, "output_path": output_path, **kwargs})
        Path(output_path).write_bytes(f"audio:{text}".encode("utf-8"))
        return output_path

    monkeypatch.setattr("web.routes.task.tts.generate_segment_audio", fake_generate_segment_audio)
    monkeypatch.setattr("web.routes.task.tts.get_audio_duration", lambda path: 1.95 if str(path).endswith("seg_0000.mp3") else 1.2)

    def fake_rebuild_full_audio(task_dir_arg, segments, variant):
        rebuilt.append(
            {
                "task_dir": task_dir_arg,
                "variant": variant,
                "texts": [segment["text"] for segment in segments],
            }
        )
        Path(full_audio_path).write_bytes(b"rebuilt-full-audio")
        return str(full_audio_path)

    monkeypatch.setattr("web.routes.task._rebuild_tts_full_audio", fake_rebuild_full_audio)
    built_chunks = []

    def fake_build_srt_from_chunks(chunks):
        built_chunks.append(chunks)
        return "1\n00:00:00,000 --> 00:00:03,150\nFresh new hook Keep this one\n"

    monkeypatch.setattr("web.routes.task.build_srt_from_chunks", fake_build_srt_from_chunks, raising=False)

    response = authed_client_no_db.post(
        f"/api/tasks/{task_id}/av/rewrite_sentence",
        json={"asr_index": 0, "text": "Fresh new hook"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["status"] == "speed_adjusted"
    assert payload["compose_stale"] is True
    assert payload["tts_duration"] == 1.95
    payload_sentence = payload["task"]["variants"]["av"]["sentences"][0]
    assert payload_sentence["duration_ratio"] == pytest.approx(
        payload_sentence["tts_duration"] / payload_sentence["target_duration"]
    )
    assert 0.95 <= payload_sentence["speed"] <= 1.05
    assert payload_sentence["speed"] != 1.12
    assert payload_sentence["status"] != "warning_overshoot"
    assert isinstance(payload_sentence["attempts"], list)
    assert generated and generated[0]["text"] == "Fresh new hook"
    assert rebuilt == [
        {
            "task_dir": str(task_dir),
            "variant": "av",
            "texts": ["Fresh new hook", "Keep this one"],
        }
    ]

    saved = store.get(task_id)
    assert saved["segments"][0]["translated"] == "Fresh new hook"
    assert saved["localized_translation"]["full_text"] == "Fresh new hook Keep this one"
    assert saved["tts_audio_path"] == str(full_audio_path)
    assert saved["srt_path"] == str(srt_path)
    assert "Fresh new hook" in saved["corrected_subtitle"]["srt_content"]
    assert saved["corrected_subtitle"]["chunks"][0]["text"] == "Fresh new hook Keep this one"
    assert saved["steps"]["compose"] == "done"
    assert saved["steps"]["export"] == "done"
    assert "请从此步继续" in saved["step_messages"]["compose"]
    assert saved["result"] == {}
    assert saved["exports"] == {}
    assert "compose" not in saved["artifacts"]
    assert "export" not in saved["artifacts"]
    assert "hard_video" not in saved["preview_files"]
    assert saved["tos_uploads"] == {}
    av_variant = saved["variants"]["av"]
    assert av_variant["sentences"][0]["text"] == "Fresh new hook"
    assert av_variant["sentences"][0]["status"] == "speed_adjusted"
    assert av_variant["sentences"][0]["speed"] == 0.975
    assert av_variant["sentences"][0]["tts_duration"] == 1.95
    assert av_variant["tts_audio_path"] == str(full_audio_path)
    assert av_variant["srt_path"] == str(srt_path)
    assert av_variant["subtitle_units"][0]["asr_indices"] == [0, 1]
    assert av_variant["subtitle_units"][0]["text"] == "Fresh new hook Keep this one"
    assert built_chunks and built_chunks[0] == av_variant["subtitle_units"]
    assert av_variant["result"] == {}
    assert av_variant["exports"] == {}
    assert "hard_video" not in av_variant["preview_files"]


def test_av_rewrite_sentence_route_marks_long_warning_without_out_of_range_speed(
    tmp_path,
    authed_client_no_db,
    monkeypatch,
):
    task_id = "task-av-rewrite-long-warning"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    segment_path = task_dir / "seg_0000.mp3"
    segment_path.write_bytes(b"old-seg")

    store.create(task_id, str(tmp_path / "video.mp4"), str(task_dir), user_id=1)
    store.update(
        task_id,
        status="done",
        pipeline_version="av",
        av_translate_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        steps={"tts": "done", "subtitle": "done", "compose": "done", "export": "done"},
        step_messages={},
    )
    store.update_variant(
        task_id,
        "av",
        voice_id="voice-db-id",
        sentences=[
            {
                "asr_index": 0,
                "target_duration": 2.0,
                "text": "Old line",
                "tts_path": str(segment_path),
                "tts_duration": 2.4,
                "speed": 1.0,
                "status": "warning_long",
            }
        ],
    )

    generated = []
    monkeypatch.setattr(
        "web.routes.task.tts.get_voice_by_id",
        lambda voice_id, user_id: {"id": voice_id, "elevenlabs_voice_id": "voice-el-id"},
    )

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        generated.append({"text": text, "speed": kwargs.get("speed")})
        Path(output_path).write_bytes(b"audio")
        return output_path

    monkeypatch.setattr("web.routes.task.tts.generate_segment_audio", fake_generate_segment_audio)
    monkeypatch.setattr("web.routes.task.tts.get_audio_duration", lambda path: 2.4)
    monkeypatch.setattr("web.routes.task._rebuild_tts_full_audio", lambda task_dir_arg, segments, variant: str(task_dir / "full.mp3"))

    response = authed_client_no_db.post(
        f"/api/tasks/{task_id}/av/rewrite_sentence",
        json={"asr_index": 0, "text": "Still too long"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "warning_long"
    saved_sentence = store.get(task_id)["variants"]["av"]["sentences"][0]
    assert saved_sentence["status"] == "warning_long"
    assert saved_sentence["speed"] == 1.0
    assert 0.95 <= saved_sentence["speed"] <= 1.05
    assert saved_sentence["duration_ratio"] == 1.2
    assert isinstance(saved_sentence["attempts"], list)
    assert generated == [{"text": "Still too long", "speed": None}]


def test_av_rewrite_sentence_route_marks_short_warning_without_needs_expand(
    tmp_path,
    authed_client_no_db,
    monkeypatch,
):
    task_id = "task-av-rewrite-short-warning"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    segment_path = task_dir / "seg_0000.mp3"
    segment_path.write_bytes(b"old-seg")

    store.create(task_id, str(tmp_path / "video.mp4"), str(task_dir), user_id=1)
    store.update(
        task_id,
        status="done",
        pipeline_version="av",
        av_translate_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        steps={"tts": "done", "subtitle": "done", "compose": "done", "export": "done"},
        step_messages={},
    )
    store.update_variant(
        task_id,
        "av",
        voice_id="voice-db-id",
        sentences=[
            {
                "asr_index": 0,
                "target_duration": 2.0,
                "text": "Old line",
                "tts_path": str(segment_path),
                "tts_duration": 1.6,
                "speed": 1.0,
                "status": "warning_short",
            }
        ],
    )

    generated = []
    monkeypatch.setattr(
        "web.routes.task.tts.get_voice_by_id",
        lambda voice_id, user_id: {"id": voice_id, "elevenlabs_voice_id": "voice-el-id"},
    )

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        generated.append({"text": text, "speed": kwargs.get("speed")})
        Path(output_path).write_bytes(b"audio")
        return output_path

    monkeypatch.setattr("web.routes.task.tts.generate_segment_audio", fake_generate_segment_audio)
    monkeypatch.setattr("web.routes.task.tts.get_audio_duration", lambda path: 1.6)
    monkeypatch.setattr("web.routes.task._rebuild_tts_full_audio", lambda task_dir_arg, segments, variant: str(task_dir / "full.mp3"))

    response = authed_client_no_db.post(
        f"/api/tasks/{task_id}/av/rewrite_sentence",
        json={"asr_index": 0, "text": "Still too short"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "warning_short"
    assert payload["status"] != "needs_expand"
    saved_sentence = store.get(task_id)["variants"]["av"]["sentences"][0]
    assert saved_sentence["status"] == "warning_short"
    assert saved_sentence["status"] != "needs_expand"
    assert saved_sentence["speed"] == 1.0
    assert 0.95 <= saved_sentence["speed"] <= 1.05
    assert saved_sentence["duration_ratio"] == 0.8
    assert isinstance(saved_sentence["attempts"], list)
    assert generated == [{"text": "Still too short", "speed": None}]


def test_rename_route_updates_task_state_for_future_capcut_downloads(tmp_path, authed_client_no_db, monkeypatch):
    archive_path = tmp_path / "capcut_normal.zip"
    archive_path.write_bytes(b"capcut-archive")
    store.create("task-rename-download", "video.mp4", str(tmp_path), user_id=1)
    store.update_variant(
        "task-rename-download",
        "normal",
        exports={"capcut_archive": str(archive_path)},
    )

    def fake_db_query_one(sql, args):
        if "SELECT id, user_id FROM projects" in sql:
            return {"id": "task-rename-download", "user_id": 1}
        return None

    monkeypatch.setattr("web.routes.task.db_query_one", fake_db_query_one)
    monkeypatch.setattr("web.routes.task.db_execute", lambda sql, args: None)
    monkeypatch.setattr(
        "web.services.artifact_download.upload_capcut_archive_for_current_user",
        lambda *a, **kw: None,
    )

    rename_response = authed_client_no_db.patch(
        "/api/tasks/task-rename-download",
        json={"display_name": "example"},
    )

    assert rename_response.status_code == 200
    assert store.get("task-rename-download")["display_name"] == "example"

    download_response = authed_client_no_db.get("/api/tasks/task-rename-download/download/capcut?variant=normal")

    assert download_response.status_code == 200
    assert 'filename=example_capcut_normal.zip' in download_response.headers["Content-Disposition"]


def test_medias_list_is_shared_for_normal_users(authed_user_client_no_db, monkeypatch):
    captured = {}
    shared_row = {
        "id": 7,
        "user_id": 88,
        "name": "shared-product",
        "product_code": "shared-product",
        "color_people": None,
        "source": None,
        "owner_name": "张三",
        "archived": False,
        "created_at": datetime(2026, 4, 16, 10, 0, 0),
        "updated_at": datetime(2026, 4, 16, 10, 0, 0),
    }

    def fake_list_products(user_id, **kwargs):
        captured["user_id"] = user_id
        return [shared_row], 1

    monkeypatch.setattr("web.routes.medias.medias.list_products", fake_list_products)
    monkeypatch.setattr("web.routes.medias.medias.count_items_by_product", lambda pids: {7: 1})
    monkeypatch.setattr("web.routes.medias.medias.count_raw_sources_by_product", lambda pids: {7: 0})
    monkeypatch.setattr("web.routes.medias.medias.first_thumb_item_by_product", lambda pids: {7: None})
    monkeypatch.setattr("web.routes.medias.medias.list_item_filenames_by_product", lambda pids, limit_per=5: {7: ["shared.mp4"]})
    monkeypatch.setattr("web.routes.medias.medias.lang_coverage_by_product", lambda pids: {7: {"en": 1}})
    monkeypatch.setattr("web.routes.medias.medias.get_product_covers_batch", lambda pids: {7: {"en": "shared-cover.jpg"}})

    response = authed_user_client_no_db.get("/medias/api/products")

    assert response.status_code == 200
    assert captured["user_id"] is None
    payload = response.get_json()
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "shared-product"
    assert payload["items"][0]["id"] == 7
    assert payload["items"][0]["product_code"] == "shared-product"
    assert payload["items"][0]["owner_name"] == "张三"


def test_medias_normal_user_can_read_update_and_delete_other_users_product(authed_user_client_no_db, monkeypatch):
    product = {
        "id": 7,
        "user_id": 88,
        "name": "共享产品",
        "product_code": "shared",
        "color_people": None,
        "source": None,
        "archived": False,
        "created_at": datetime(2026, 4, 16, 10, 0, 0),
        "updated_at": datetime(2026, 4, 16, 10, 0, 0),
    }
    updated = {}
    deleted = {}

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product if pid == 7 else None)
    monkeypatch.setattr("web.routes.medias.medias.get_product_covers", lambda pid: {"en": "shared-cover.jpg"} if pid == 7 else {})
    monkeypatch.setattr("web.routes.medias.medias.list_copywritings", lambda pid: [{"lang": "en", "text": "shared copy"}] if pid == 7 else [])
    monkeypatch.setattr(
        "web.routes.medias.medias.list_items",
        lambda pid: [{
            "id": 701,
            "product_id": 7,
            "lang": "en",
            "filename": "shared.mp4",
            "display_name": "shared.mp4",
            "cover_object_key": None,
            "object_key": "shared.mp4",
            "thumbnail_path": None,
            "duration_seconds": 12.3,
            "file_size": 456,
            "created_at": datetime(2026, 4, 16, 10, 5, 0),
        }] if pid == 7 else []
    )
    monkeypatch.setattr("web.routes.medias.medias.get_product_by_code", lambda code: None)
    monkeypatch.setattr("web.routes.medias.medias.has_english_cover", lambda pid: True)
    monkeypatch.setattr("web.routes.medias.medias.update_product", lambda pid, **fields: updated.update({"pid": pid, **fields}))
    monkeypatch.setattr("web.routes.medias.medias.replace_copywritings", lambda pid, lang_items, lang=None: None)
    monkeypatch.setattr("web.routes.medias.medias.soft_delete_product", lambda pid: deleted.update({"pid": pid}))

    read_response = authed_user_client_no_db.get("/medias/api/products/7")
    update_response = authed_user_client_no_db.put(
        "/medias/api/products/7",
        json={"name": "共享改名", "product_code": "shared-edited-rjc"},
    )
    delete_response = authed_user_client_no_db.delete("/medias/api/products/7")

    failures = []

    if read_response.status_code != 200:
        failures.append(f"GET /medias/api/products/7 returned {read_response.status_code}")
    else:
        read_payload = read_response.get_json()
        if read_payload["product"]["name"] != "共享产品":
            failures.append(f"GET returned product name {read_payload['product']['name']!r}")

    if update_response.status_code != 200:
        failures.append(f"PUT /medias/api/products/7 returned {update_response.status_code}")
    if updated != {"pid": 7, "name": "共享改名", "product_code": "shared-edited-rjc"}:
        failures.append(f"PUT captured {updated!r}")

    if delete_response.status_code != 200:
        failures.append(f"DELETE /medias/api/products/7 returned {delete_response.status_code}")
    if deleted != {"pid": 7}:
        failures.append(f"DELETE captured {deleted!r}")

    assert not failures, "\n".join(failures)


def test_deploy_route_copies_variant_capcut_project(tmp_path, logged_in_client, monkeypatch):
    project_dir = tmp_path / "capcut_hook_cta"
    project_dir.mkdir()
    (project_dir / "draft_content.json").write_text("{}", encoding="utf-8")
    store.create("task-deploy-variant", "video.mp4", str(tmp_path))
    store.update_variant(
        "task-deploy-variant",
        "hook_cta",
        exports={"capcut_project": str(project_dir)},
    )

    deployed_dir = tmp_path / "jianying" / "deployed"

    def fake_deploy_capcut_project(path):
        target = deployed_dir / Path(path).name
        target.mkdir(parents=True, exist_ok=True)
        (target / "draft_content.json").write_text("{}", encoding="utf-8")
        return str(target)

    monkeypatch.setattr("web.routes.task.deploy_capcut_project", fake_deploy_capcut_project)

    response = logged_in_client.post("/api/tasks/task-deploy-variant/deploy/capcut?variant=hook_cta")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["deployed_project_dir"].endswith("capcut_hook_cta")
    assert store.get("task-deploy-variant")["variants"]["hook_cta"]["exports"]["jianying_project_dir"].endswith("capcut_hook_cta")


def test_medias_create_product_rejects_bad_slug(logged_in_client):
    rv = logged_in_client.post(
        "/medias/api/products",
        json={"name": "slug-guard", "product_code": "Bad_Slug"},
    )
    assert rv.status_code == 400
    body = rv.get_json()
    assert "产品 ID" in body["error"]


def test_medias_create_product_requires_rjc_suffix(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.medias.medias.get_product_by_code", lambda code: None)
    monkeypatch.setattr("web.routes.medias.medias.create_product", lambda *args, **kwargs: 123)

    rv = authed_client_no_db.post(
        "/medias/api/products",
        json={"name": "rjc-guard", "product_code": "sonic-lens-refresher"},
    )
    assert rv.status_code == 400
    body = rv.get_json()
    assert body["error"] == "Product ID 必须以 -RJC 结尾"


def test_medias_update_product_requires_rjc_suffix(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.medias.medias.get_product",
        lambda pid: {"id": pid, "user_id": 2, "name": "共享产品", "product_code": "shared-rjc"},
    )
    monkeypatch.setattr("web.routes.medias._can_access_product", lambda product: True)
    monkeypatch.setattr("web.routes.medias.medias.get_product_by_code", lambda code: None)
    monkeypatch.setattr("web.routes.medias.medias.update_product", lambda pid, **fields: None)

    rv = authed_user_client_no_db.put(
        "/medias/api/products/7",
        json={"product_code": "shared-edited"},
    )
    assert rv.status_code == 400
    body = rv.get_json()
    assert body["error"] == "Product ID 必须以 -RJC 结尾"


def test_medias_create_product_rejects_duplicate_slug(logged_in_client):
    from appcore.db import execute as db_execute
    code = "dup-slug-test-rjc"
    db_execute("DELETE FROM media_products WHERE product_code=%s", (code,))
    try:
        rv1 = logged_in_client.post(
            "/medias/api/products",
            json={"name": "t1", "product_code": code},
        )
        assert rv1.status_code == 201
        rv2 = logged_in_client.post(
            "/medias/api/products",
            json={"name": "t2", "product_code": code},
        )
        assert rv2.status_code == 409
    finally:
        db_execute("DELETE FROM media_products WHERE product_code=%s", (code,))


def test_medias_put_product_requires_cover_and_items(logged_in_client):
    from appcore.db import execute as db_execute
    code = "save-guard-test-rjc"
    db_execute("DELETE FROM media_products WHERE product_code=%s", (code,))
    rv = logged_in_client.post(
        "/medias/api/products",
        json={"name": "t", "product_code": code},
    )
    assert rv.status_code == 201
    pid = rv.get_json()["id"]
    try:
        rv2 = logged_in_client.put(
            f"/medias/api/products/{pid}",
            json={"name": "t", "product_code": code},
        )
        assert rv2.status_code == 400
        assert "主图" in rv2.get_json()["error"]
    finally:
        db_execute("DELETE FROM media_products WHERE product_code=%s", (code,))


def test_medias_page_contains_aligned_create_modal_layout(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "width:min(1120px, calc(100vw - 48px));" in body
    assert "oc-add-form" in body
    assert "oc-add-hero-grid" in body
    assert "oc-add-main-grid" in body
    assert "oc-add-video-grid" in body


def test_medias_page_contains_aligned_edit_modal_layout(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<div class="oc-modal oc-modal-narrow oc-modal-edit"' in body
    assert '.oc-modal-edit { width:min(1344px, calc(100vw - 48px)); }' in body
    assert "oc-edit-form" in body
    assert "oc-edit-hero-grid" in body
    assert "oc-edit-main-grid" in body
    assert "oc-edit-video-grid" in body


def test_medias_page_prioritizes_push_audit_sections_in_edit_modal(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.index('id="edTitle"') < body.index('id="edAdSupportedLangsBox"')
    assert body.index('id="edAdSupportedLangsBox"') < body.index('id="edLangTabs"')
    assert body.index('id="edCwSection"') < body.index('id="edItemsSection"')
    assert body.index('id="edItemsSection"') < body.index('id="edCoverSection"')
    assert body.index('id="edCoverSection"') < body.index('id="edDetailImagesSection"')
    assert body.index('id="edDetailImagesSection"') < body.index('for="edMkId"')
    assert '.oc-edit-form { display:flex; flex-direction:column; gap:var(--oc-sp-2); }' in body
    assert '.oc-modal-head-main {' in body
    assert '.oc-modal-head-main {\n  display:flex;\n  align-items:center;\n  justify-content:flex-start;' in body
    assert '.oc-modal-head-meta {' in body
    assert '.oc-modal-head-main h3 {\n  flex:0 0 auto;\n  padding-top:0;\n  font-weight:700;' in body
    assert '主站已适配语种' not in body


def test_medias_page_exposes_compact_copy_review_layout(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'class="oc-section-head oc-section-head-between"' in body
    assert 'id="edCwAddBtn"' in body
    assert 'id="edCwTranslateSlot"' in body
    assert "添加文案" in body
    assert '.oc-cw-grid { display:grid; grid-template-columns:1fr; gap:var(--oc-sp-2); }' in body
    assert "textarea.rows = 3;" in medias_js
    assert "function edNormalizeCopywritingBody" in medias_js
    assert "function edTranslateEnglishCopywriting()" in medias_js
    assert "'/api/title-translate/translate'" in medias_js
    assert "slot.innerHTML = '';" in medias_js
    assert "slot.replaceChildren(btn);" in medias_js
    assert "btn.textContent = '一键翻译英文文案';" in medias_js
    assert "textarea.placeholder = '标题: \\n文案: \\n描述: ';" in medias_js


def test_medias_page_translates_copywriting_as_single_structured_block():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function edValidateCopyTranslateSource(rawText)" in medias_js
    assert "const sourceValidation = edValidateCopyTranslateSource(source.body);" in medias_js
    assert "source_text: sourceValidation.value" in medias_js
    assert "const translatedBody = edNormalizeCopywritingBody(response.result || '');" in medias_js
    assert "const sourceBody = edNormalizeCopywritingBody(source.body);" not in medias_js
    assert "function edTranslateCopyField" not in medias_js


def test_medias_copywriting_normalizer_strips_nested_field_labels():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    fn_block = (
        "function edCanonicalCopyField"
        + medias_js.split("function edCanonicalCopyField", 1)[1]
        .split("function edNormalizeCopywritingsData", 1)[0]
    )
    expected = "标题: Magnet\n文案: Strong hold\n描述: Easy install"
    source = "标题: 标题: Magnet\n文案: 文案: Strong hold\n描述: 描述: Easy install"
    script = f"""
{fn_block}
const normalized = edNormalizeCopywritingBody({source!r});
if (normalized !== {expected!r}) {{
  throw new Error(`unexpected normalized copywriting: ${{normalized}}`);
}}
"""

    result = subprocess.run(
        ["node", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_medias_page_wraps_language_coverage_with_full_labels():
    template = (Path(__file__).resolve().parents[1] / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function langDisplayName(code)" in medias_js
    assert "if (l && l.name_zh) return `${l.name_zh} (${l.code})`;" in medias_js
    assert "${escapeHtml(langDisplayName(l.code))}" in medias_js
    assert '<col style="width:336px">' in medias_js
    assert "const midpoint = Math.ceil(chips.length / 2);" in medias_js
    assert 'class="oc-lang-row"' in medias_js
    assert ".oc-lang-bar {" in template
    assert "flex-direction:column;" in template
    assert ".oc-lang-row {" in template
    assert "display:flex;" in template
    assert "flex-wrap:nowrap;" in template


def test_medias_page_marks_copy_as_optional_in_add_modal(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<span>文案<span class="req">*</span></span>' not in body
    assert '<span>文案<span class="optional">可选</span></span>' in body
    assert '没有可以留空' in body

    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    assert "if (!cw.length) { alert('请填写文案');" not in medias_js
    assert 'copywritings: { en: cw }' in medias_js


def test_medias_js_submit_requires_rjc_suffix():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function validateProductCodeForSubmit(code)" in medias_js
    assert "Product ID 必须以 -RJC 结尾" in medias_js
    assert medias_js.count("validateProductCodeForSubmit(code)") >= 3
    assert "alert(codeError);" in medias_js
    assert "throw new Error(codeError);" in medias_js


def test_medias_page_wraps_video_titles_in_edit_modal(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '.oc-vitem .vname {' in body
    assert 'white-space:normal;' in body
    assert 'overflow-wrap:anywhere;' in body
    assert 'min-height:calc(1.45em * 2);' in body


def test_medias_page_centers_new_item_submit_button_in_edit_modal(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '.oc-edit-form .oc-new-item-footer { justify-content:center; }' in body


def test_medias_page_shrinks_edit_modal_video_cards_to_eighty_percent(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '.oc-edit-form #edItemsGrid {' in body
    assert 'grid-template-columns:repeat(auto-fill, 196px);' in body
    assert 'justify-content:flex-start;' in body


def test_medias_page_removes_admin_only_scope_toggle(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="scopeAll"' not in body
    assert 'id="chipScope"' not in body
    assert 'window.MEDIAS_IS_ADMIN' not in body


def test_medias_page_removes_archived_filter_chip(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="archived"' not in body
    assert 'id="chipArchived"' not in body


def test_medias_page_contains_raw_sources_modal_and_upload_modal(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '.oc-rs-list {' in body
    assert 'grid-template-columns:repeat(auto-fill, 180px);' in body
    assert '.oc-rs-card.oc-vitem {' in body
    assert '.oc-rs-card.oc-vitem .vbody {' in body
    assert 'height:320px;' in body
    assert '.oc-rs-upload-grid {' in body
    assert '.oc-rs-upload-video-fill {' in body
    assert 'id="rsModalMask"' in body
    assert 'id="rsModal"' in body
    assert 'id="rsModalClose"' in body
    assert 'id="rsSummary"' in body
    assert '<div id="rsList" class="oc-rs-list"></div>' in body
    assert 'id="rsUploadMask"' in body
    assert 'id="rsUploadForm"' in body
    assert 'id="rsVideoInput"' in body
    assert 'id="rsCoverInput"' in body
    assert 'id="rsDisplayName"' in body
    assert 'id="rsUploadCoverBox"' in body
    assert 'id="rsUploadCoverPreview"' in body
    assert 'id="rsUploadVideoBox"' in body
    assert 'id="rsUploadVideoFilled"' in body
    assert 'id="rsUploadVideoName"' in body
    assert '支持 MP4 / MOV' in body
    assert 'WebM / MKV' not in body
    assert ".oc-rs-title-display {" in body
    assert ".oc-rs-title-input {" in body
    assert "-webkit-line-clamp:2;" in body


def test_medias_page_contains_raw_source_translate_dialog(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="rsTranslateMask"' in body
    assert 'id="rsTranslateDialog"' in body
    assert 'id="rstRsList"' in body
    assert 'id="rstLangs"' in body
    assert 'id="rstPreview"' in body
    assert 'id="rstSubmit"' in body
    assert 'id="rstCancel"' in body
    assert ".oc-rst-choice-row {" in body
    assert "--rst-preview-w:90px;" in body
    assert "--rst-preview-h:160px;" in body
    assert ".oc-rst-choice-video {" in body


def test_voice_library_page_ok_and_menu_rendered(authed_client_no_db):
    resp = authed_client_no_db.get("/voice-library")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "voice-library-root" in body
    assert "/voice-library" in body
    assert "声音仓库" in body


def test_medias_scripts_do_not_use_admin_scope_switch():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    load_list_block = medias_js.split("// ---------- List ----------", 1)[1].split("// ---------- Modal ----------", 1)[0]
    events_block = medias_js.split("// ---------- Events ----------", 1)[1].split("$('createBtn').addEventListener('click', openCreate);", 1)[0]

    assert "scopeAll" not in load_list_block
    assert "MEDIAS_IS_ADMIN" not in load_list_block
    assert "params.set('scope', 'all')" not in load_list_block
    assert "syncChip('chipScope', 'scopeAll')" not in events_block
    assert "chipScope" not in events_block


def test_medias_scripts_do_not_use_archived_filter_chip():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    load_list_block = medias_js.split("// ---------- List ----------", 1)[1].split("// ---------- Modal ----------", 1)[0]
    events_block = medias_js.split("// ---------- Events ----------", 1)[1].split("$('createBtn').addEventListener('click', openCreate);", 1)[0]

    assert "$('archived').checked" not in load_list_block
    assert "params.set('archived', '1')" not in load_list_block
    assert "syncChip('chipArchived', 'archived')" not in events_block
    assert "chipArchived" not in events_block


def test_medias_scripts_wire_raw_sources_modal_flow():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "js-raw-sources" in medias_js
    assert "rsModalMask" in medias_js
    assert "rsModalClose" in medias_js
    assert "loadRawSourceList" in medias_js
    assert "await loadRawSourceList(uiState.currentPid)" in medias_js
    assert "rsUploadForm" in medias_js
    assert "renderRawSourceCard" in medias_js
    assert "data-rs-id" in medias_js
    assert "ensureRawSourceVideoLoaded" in medias_js
    assert "bindRawSourceCards" in medias_js
    assert 'data-tab="cover"' in medias_js
    assert 'data-tab="video"' in medias_js
    assert "document.createElement('video')" in medias_js
    assert "video.addEventListener('loadedmetadata'" in medias_js
    assert "video.addEventListener('error'" in medias_js
    assert "isRawSourceCoverFile" in medias_js
    assert "isRawSourceVideoFile" in medias_js
    assert "仅支持 JPG / PNG / WebP / GIF 图片" in medias_js
    assert "仅支持 MP4 / MOV 视频" in medias_js
    assert "setRawSourceUploadCover" in medias_js
    assert "setRawSourceUploadVideo" in medias_js
    assert "bindRawSourceUploadDropzone" in medias_js
    assert "uploadNameInput.value = file.name" in medias_js
    assert '/medias/api/products/${pid}/raw-sources' in medias_js
    assert '/medias/api/raw-sources/${del.dataset.rid}' in medias_js
    assert "js-rs-title-display" in medias_js
    assert "js-rs-title-input" in medias_js
    assert "startRawSourceTitleEdit" in medias_js
    assert "saveRawSourceTitle" in medias_js
    assert "cancelRawSourceTitleEdit" in medias_js
    assert "refreshRawSourceList" in medias_js


def test_medias_scripts_wire_raw_source_translate_dialog():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "openTranslateDialog" in medias_js
    assert "rsTranslateMask" in medias_js
    assert "rstRsList" in medias_js
    assert "rstLangs" in medias_js
    assert "rstPreview" in medias_js
    assert '/medias/api/languages' in medias_js
    assert '/medias/api/products/${pid}/translate' in medias_js
    assert "const videoUrl = escapeHtml(it.video_url || '')" in medias_js
    assert "poster=\"${escapeHtml(it.cover_url)}\"" in medias_js
    assert 'class="oc-rst-choice-video"' in medias_js
    assert 'controls playsinline preload="metadata"' in medias_js
    assert "function rawSourceLangDisplayName(lang)" in medias_js
    assert "const name = escapeHtml(rawSourceLangDisplayName(lang));" in medias_js
    assert "window.open(`/tasks/${taskId}`, '_blank', 'noopener,noreferrer')" in medias_js
    assert "window.location.href = `/tasks/${taskId}`" not in medias_js


def test_medias_scripts_include_owner_column():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "<th>负责人</th>" in medias_js
    assert "p.owner_name" in medias_js


def test_medias_scripts_make_listing_status_inline_editable():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function listingStatusSelect" in medias_js
    assert "startListingStatusInlineEdit" in medias_js
    assert "data-listing-status" in medias_js
    assert "data-listing-edit" in medias_js
    assert "listing_status: nextStatus" in medias_js
    assert "body: JSON.stringify({ listing_status: nextStatus })" in medias_js
    assert "grid.querySelectorAll('td.listing-status-cell')" in medias_js


def test_image_translate_detail_template_contains_medias_context_block():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "image_translate_detail.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "templates" / "_image_translate_scripts.html").read_text(encoding="utf-8")

    assert "itMediasContextCard" in template
    assert "商品素材编辑页" in template
    assert "window.renderImageTranslateMediasContext" in scripts
    assert "medias_context" in scripts


def test_image_translate_templates_show_concurrency_mode_pills():
    root = Path(__file__).resolve().parents[1]
    list_template = (root / "web" / "templates" / "image_translate_list.html").read_text(encoding="utf-8")
    detail_template = (root / "web" / "templates" / "image_translate_detail.html").read_text(encoding="utf-8")
    styles = (root / "web" / "templates" / "_image_translate_styles.html").read_text(encoding="utf-8")

    assert "处理模式" in list_template
    assert "处理模式" in detail_template
    assert "itMetaConcurrencyMode" in detail_template
    assert "data-concurrency-mode" in list_template
    assert "data-concurrency-mode" in detail_template
    assert "it-mode-pill" in list_template
    assert "it-mode-pill" in detail_template
    assert ".it-mode-pill" in styles


def test_image_translate_retry_fetch_handles_non_json_errors():
    root = Path(__file__).resolve().parents[1]
    scripts = (root / "web" / "templates" / "_image_translate_scripts.html").read_text(encoding="utf-8")

    assert "parseJsonResponse" in scripts
    assert ".catch(function(){ return {}; })" in scripts


def test_medias_edit_modal_contains_detail_image_translation_controls():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'id="edDetailImagesTranslateBtn"' in template
    assert 'id="edDetailTranslateStatus"' in template
    assert 'id="edDetailTranslateHistory"' in template
    assert "detail-image-translate-tasks" in scripts
    assert "detail-images/translate-from-en" in scripts


def test_medias_edit_modal_contains_detail_image_zip_download_button():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'id="edDetailImagesDownloadZipBtn"' in template
    assert "detail-images/download-zip" in scripts


def test_medias_edit_modal_contains_download_product_images_button():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'id="edDownloadProductImagesBtn"' in template
    assert "下载商品图" in template
    assert template.index('id="edDownloadProductImagesBtn"') < template.index('id="edClose"')
    assert "detail-images/download-localized-zip" in scripts


def test_medias_page_exposes_edit_modal_link_check_controls(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="edOpenProductUrlBtn"' not in body
    assert "访问商品链接" not in body
    assert 'id="edCopyProductUrlBtn"' in body
    assert 'id="edLinkCheckSummary"' in body
    assert 'id="edLinkCheckViewBtn"' in body
    assert 'id="edLinkCheckMask"' in body
    assert '.oc-link-check-inline {' in body
    assert '.oc-link-check-modal-grid {' in body


def test_medias_scripts_wire_material_link_check_flow():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert 'function edOpenLocalizedProductUrl()' not in medias_js
    assert "window.open(url, '_blank', 'noopener')" not in medias_js
    assert 'function edStartLinkCheck()' in medias_js
    assert 'function edPollLinkCheck(lang)' in medias_js
    assert 'function edOpenLinkCheckModal()' in medias_js
    assert 'function edRenderLinkCheckSummary(task)' in medias_js
    assert '/medias/api/products/${pid}/link-check' in medias_js
    assert '/medias/api/products/${pid}/link-check/${encodeURIComponent(lang)}' in medias_js
    assert '/medias/api/products/${pid}/link-check/${encodeURIComponent(lang)}/detail' in medias_js
    assert "edState.productData.product.link_check_tasks" in medias_js


def test_mk_selection_template_proxies_wedev_media_assets():
    template = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "templates"
        / "mk_selection.html"
    ).read_text(encoding="utf-8")

    assert "function normalizeMkMediaPath" in template
    assert "/medias/api/mk-media?path=" in template
    assert "/medias/media-objects/" not in template


def test_pushes_scripts_format_language_as_chinese_plus_code():
    pushes_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "pushes.js").read_text(encoding="utf-8")

    assert "function formatLanguageLabel" in pushes_js
    assert "const raw = String(code || '').trim();" in pushes_js
    assert "${name} (${normalized})" in pushes_js
    assert '<span class="lang-pill">${formatLanguageLabel(it.lang)}</span>' in pushes_js
    assert "[['语种', formatLanguageLabel(t.lang)" in pushes_js
    assert "addKV('语种', formatLanguageLabel(item.lang));" in pushes_js


def test_medias_scripts_format_language_as_chinese_plus_code():
    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function langDisplayName(code)" in medias_js
    assert "const raw = String(code || '').trim();" in medias_js
    assert "${l.name_zh} (${l.code})" in medias_js
    assert "${langDisplayName(l.code)}${badgeHtml}" in medias_js
    assert "const label = langDisplayName(lang);" in medias_js
    assert "确认删除 ${langDisplayName(lang)} 语种主图" in medias_js
    assert "langDisplayName(analysis.detected_language || '-')" in medias_js
    assert "langDisplayName(task.page_language || '-')" in medias_js
