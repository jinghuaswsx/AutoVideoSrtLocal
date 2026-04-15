from pathlib import Path
import json

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


def test_index_page_uses_tos_direct_upload_bootstrap(authed_client_no_db):
    response = authed_client_no_db.get("/api/tasks/upload-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "/api/tos-upload/bootstrap" in body
    assert "/api/tos-upload/complete" in body
    assert 'xhr.open("PUT", bootstrap.upload_url, true)' in body


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
    assert "type=\"file\"" not in upload_body
    assert "subtitleRemovalFile" not in upload_body
    assert "sr-upload-placeholder" in upload_body
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


def test_layout_contains_subtitle_removal_nav_icon(authed_client_no_db):
    response = authed_client_no_db.get("/subtitle-removal")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'href="/subtitle-removal"' in body
    assert '<span class="nav-icon">🧽</span>' in body


def test_settings_page_contains_default_jianying_project_root(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.settings.get_all", lambda user_id: {})

    response = authed_client_no_db.get("/settings")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "jianying_project_root" in body
    assert DEFAULT_JIANYING_PROJECT_ROOT in body


def test_settings_page_saves_custom_jianying_project_root(authed_client_no_db, monkeypatch):
    custom_root = r"D:\JianyingDrafts"
    captured = []

    def fake_set_key(user_id, service, key_value, extra=None):
        captured.append((user_id, service, key_value, extra))

    monkeypatch.setattr("web.routes.settings.get_all", lambda user_id: {"jianying": {"key_value": "", "extra": {"project_root": custom_root}}})
    monkeypatch.setattr("web.routes.settings.set_key", fake_set_key)

    response = authed_client_no_db.post(
        "/settings",
        data={"jianying_project_root": custom_root},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (1, "jianying", "", {"project_root": custom_root}) in captured


def test_task_detail_returns_artifacts_structure(authed_client_no_db):
    store.create("task-preview", "video.mp4", "output/task-preview", user_id=1)

    response = authed_client_no_db.get("/api/tasks/task-preview")

    assert response.status_code == 200
    payload = response.get_json()
    assert "artifacts" in payload
    assert payload["artifacts"] == {}


def test_store_create_initializes_single_variant():
    task = store.create("task-variants", "video.mp4", "output/task-variants")

    assert set(task["variants"].keys()) == {"normal"}
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


def test_medias_create_product_rejects_duplicate_slug(logged_in_client):
    from appcore.db import execute as db_execute
    code = "dup-slug-test"
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
    code = "save-guard-test"
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


def test_medias_page_marks_copy_as_required_in_add_modal(authed_client_no_db):
    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<span>文案<span class="req">*</span></span>' in body
    assert '<span>文案<span class="optional">可选</span></span>' not in body
    assert '没有可以留空' not in body

    medias_js = (Path(__file__).resolve().parents[1] / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    assert 'copywritings: { en: cw }' in medias_js


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
    assert 'grid-template-columns:repeat(auto-fill, 208px);' in body
    assert 'justify-content:flex-start;' in body
