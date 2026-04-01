from pathlib import Path
import json

from web import store
from web.app import create_app


def test_index_page_contains_alignment_and_voice_controls(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "voiceSelect" in body
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


def test_index_page_contains_confirmation_mode_control(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "interactiveReviewToggle" in body
    assert "全自动" in body
    assert "手动确认" in body


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
    assert "voiceSelect" in body
    assert "interactiveReviewToggle" in body
    assert "renderStepPreviews" in body
    assert "pipelineCard" in body


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


def test_task_detail_returns_artifacts_structure(logged_in_client):
    store.create("task-preview", "video.mp4", "output/task-preview")

    response = logged_in_client.get("/api/tasks/task-preview")

    assert response.status_code == 200
    payload = response.get_json()
    assert "artifacts" in payload
    assert payload["artifacts"] == {}


def test_store_create_initializes_two_variants():
    task = store.create("task-variants", "video.mp4", "output/task-variants")

    assert set(task["variants"].keys()) == {"normal", "hook_cta"}
    assert task["variants"]["normal"]["label"] == "普通版"
    assert task["variants"]["hook_cta"]["label"] == "黄金3秒 + CTA版"


def test_artifact_route_serves_whitelisted_preview_file(tmp_path, logged_in_client):
    audio_path = tmp_path / "preview.mp3"
    audio_path.write_bytes(b"audio-preview")
    store.create("task-file", "video.mp4", str(tmp_path))
    store.update("task-file", preview_files={"audio_extract": str(audio_path)})

    response = logged_in_client.get("/api/tasks/task-file/artifact/audio_extract")

    assert response.status_code == 200
    assert response.data == b"audio-preview"


def test_artifact_route_serves_variant_preview_file(tmp_path, logged_in_client):
    video_path = tmp_path / "preview.mp4"
    video_path.write_bytes(b"video-preview")
    store.create("task-variant-file", "video.mp4", str(tmp_path))
    store.update_variant("task-variant-file", "hook_cta", preview_files={"soft_video": str(video_path)})

    response = logged_in_client.get("/api/tasks/task-variant-file/artifact/soft_video?variant=hook_cta")

    assert response.status_code == 200
    assert response.data == b"video-preview"


def test_artifact_route_rejects_unknown_name(tmp_path, logged_in_client):
    store.create("task-bad", "video.mp4", str(tmp_path))

    response = logged_in_client.get("/api/tasks/task-bad/artifact/not_allowed")

    assert response.status_code == 404


def test_artifact_route_falls_back_to_output_dir_when_task_state_is_missing(tmp_path, logged_in_client, monkeypatch):
    task_id = "task-restored"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    preview_path = task_dir / f"{task_id}_soft.mp4"
    preview_path.write_bytes(b"soft-video-preview")
    monkeypatch.setattr("web.routes.task.OUTPUT_DIR", str(tmp_path))

    response = logged_in_client.get(f"/api/tasks/{task_id}/artifact/soft_video")

    assert response.status_code == 200
    assert response.data == b"soft-video-preview"


def test_alignment_route_compiles_script_segments(logged_in_client):
    task = store.create("task-1", "video.mp4", "output/task-1")
    task["utterances"] = [
        {"text": "浣犲ソ", "start_time": 0.0, "end_time": 0.8, "words": []},
        {"text": "涓栫晫", "start_time": 0.8, "end_time": 1.6, "words": []},
    ]

    response = logged_in_client.put(
        "/api/tasks/task-1/alignment",
        json={"break_after": [False, True]},
    )

    assert response.status_code == 200
    saved = store.get("task-1")
    assert saved["_alignment_confirmed"] is True
    assert saved["script_segments"][0]["text"] == "浣犲ソ涓栫晫"
    assert saved["artifacts"]["alignment"]["items"][1]["segments"][0]["text"] == "浣犲ソ涓栫晫"


def test_segments_route_updates_translate_artifact(logged_in_client):
    store.create("task-translate", "video.mp4", "output/task-translate")
    store.update(
        "task-translate",
        script_segments=[{"text": "你好世界", "translated": "Hello world", "start_time": 0.0, "end_time": 1.6}],
        segments=[{"text": "你好世界", "translated": "Hello world", "start_time": 0.0, "end_time": 1.6}],
    )

    response = logged_in_client.put(
        "/api/tasks/task-translate/segments",
        json={"segments": [{"text": "你好世界", "translated": "Hello there", "start_time": 0.0, "end_time": 1.6}]},
    )

    assert response.status_code == 200
    saved = store.get("task-translate")
    assert saved["_segments_confirmed"] is True
    assert saved["artifacts"]["translate"]["items"][0]["segments"][0]["translated"] == "Hello there"


def test_segments_route_updates_localized_translation_for_future_tts(logged_in_client):
    store.create("task-translate-localized", "video.mp4", "output/task-translate-localized")
    store.update(
        "task-translate-localized",
        source_full_text_zh="你好世界",
        script_segments=[{"index": 0, "text": "你好世界", "start_time": 0.0, "end_time": 1.6}],
        segments=[{"index": 0, "text": "你好世界", "translated": "Hello world", "start_time": 0.0, "end_time": 1.6}],
    )

    response = logged_in_client.put(
        "/api/tasks/task-translate-localized/segments",
        json={"segments": [{"index": 0, "text": "你好世界", "translated": "Hello there", "start_time": 0.0, "end_time": 1.6}]},
    )

    assert response.status_code == 200
    saved = store.get("task-translate-localized")
    assert saved["script_segments"][0]["text"] == "你好世界"
    assert saved["localized_translation"]["full_text"] == "Hello there"
    assert saved["localized_translation"]["sentences"][0]["source_segment_indices"] == [0]


def test_task_payload_exposes_tts_script_and_corrected_subtitle(logged_in_client):
    store.create("task-payload", "video.mp4", "output/task-payload")
    store.update(
        "task-payload",
        tts_script={"full_text": "Say it smooth.", "blocks": [], "subtitle_chunks": []},
        corrected_subtitle={"chunks": [], "srt_content": "1\n00:00:00,000 --> 00:00:01,000\nSay it smooth.\n"},
    )

    response = logged_in_client.get("/api/tasks/task-payload")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tts_script"]["full_text"] == "Say it smooth."
    assert "Say it smooth." in payload["corrected_subtitle"]["srt_content"]


def test_voice_routes_support_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICES_FILE", str(tmp_path / "voices.json"))
    app = create_app()
    client = app.test_client()

    created = client.post(
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

    updated = client.put(
        f"/api/voices/{voice_id}",
        json={"description": "Warm and updated"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["voice"]["description"] == "Warm and updated"

    listed = client.get("/api/voices")
    assert listed.status_code == 200
    assert listed.get_json()["voices"][0]["id"] == voice_id

    deleted = client.delete(f"/api/voices/{voice_id}")
    assert deleted.status_code == 200


def test_download_route_can_return_hook_cta_capcut_archive(tmp_path, logged_in_client):
    archive_path = tmp_path / "capcut_hook_cta.zip"
    archive_path.write_bytes(b"capcut-archive")
    store.create("task-download-variant", "video.mp4", str(tmp_path))
    store.update_variant(
        "task-download-variant",
        "hook_cta",
        exports={"capcut_archive": str(archive_path)},
    )

    response = logged_in_client.get("/api/tasks/task-download-variant/download/capcut?variant=hook_cta")

    assert response.status_code == 200
    assert response.data == b"capcut-archive"


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
