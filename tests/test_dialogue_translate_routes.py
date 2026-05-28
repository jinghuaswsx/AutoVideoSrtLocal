from __future__ import annotations

import io
import json

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
    assert "/api/dialogue-translate" in body


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
