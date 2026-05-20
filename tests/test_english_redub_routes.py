from __future__ import annotations

import io


def _denylisted_english_redub_user(username: str) -> dict:
    return {
        "id": 1,
        "username": username,
        "role": "user",
        "is_active": 1,
        "permissions": '{"english_redub": true}',
    }


def test_english_redub_list_page_is_registered(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.english_redub.recover_all_interrupted_tasks",
        lambda: None,
    )
    monkeypatch.setattr(
        "web.routes.english_redub.translation_route_store.list_projects_with_creator",
        lambda **kwargs: [],
    )

    resp = authed_client_no_db.get("/english-redub")

    assert resp.status_code == 200
    assert "英语视频重新配音" in resp.get_data(as_text=True)


def test_english_redub_rejects_denylisted_user_page_and_api(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: _denylisted_english_redub_user("zhangwei"),
    )
    with authed_client_no_db.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True

    page_resp = authed_client_no_db.get("/english-redub")
    api_resp = authed_client_no_db.post("/api/english-redub/start")

    assert page_resp.status_code == 302
    assert page_resp.headers["Location"].endswith("/")
    assert api_resp.status_code == 403


def test_english_redub_allows_other_default_user(
    authed_user_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "web.routes.english_redub.recover_all_interrupted_tasks",
        lambda: None,
    )
    monkeypatch.setattr(
        "web.routes.english_redub.translation_route_store.list_projects_with_creator",
        lambda **kwargs: [],
    )

    resp = authed_user_client_no_db.get("/english-redub")

    assert resp.status_code == 200
    assert "英语视频重新配音" in resp.get_data(as_text=True)


def test_english_redub_start_persists_fixed_english_and_script_mode(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    updates: dict = {}
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")

    monkeypatch.setattr(
        "web.routes.english_redub.save_uploaded_video",
        lambda *args, **kwargs: (str(video_path), 1, "video/mp4"),
    )
    monkeypatch.setattr(
        "web.routes.english_redub._ensure_uploaded_video_thumbnail",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        "web.routes.english_redub.english_redub_pipeline_runner.start",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr("web.routes.english_redub.store.create", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.routes.english_redub.store.update",
        lambda task_id, **kwargs: updates.update(kwargs),
    )
    monkeypatch.setattr(
        "web.routes.english_redub.store.set_preview_file",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "web.routes.english_redub._resolve_name_conflict",
        lambda user_id, desired_name: desired_name,
    )

    resp = authed_client_no_db.post(
        "/api/english-redub/start",
        data={
            "video": (io.BytesIO(b"video"), "demo.mp4"),
            "script_mode": "rewrite",
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 201
    assert updates["type"] == "english_redub"
    assert updates["source_language"] == "en"
    assert updates["target_lang"] == "en"
    assert updates["script_mode"] == "rewrite"


def test_english_redub_start_defaults_script_mode_to_original(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    updates: dict = {}
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")

    monkeypatch.setattr(
        "web.routes.english_redub.save_uploaded_video",
        lambda *args, **kwargs: (str(video_path), 1, "video/mp4"),
    )
    monkeypatch.setattr(
        "web.routes.english_redub._ensure_uploaded_video_thumbnail",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        "web.routes.english_redub.english_redub_pipeline_runner.start",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr("web.routes.english_redub.store.create", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.routes.english_redub.store.update",
        lambda task_id, **kwargs: updates.update(kwargs),
    )
    monkeypatch.setattr(
        "web.routes.english_redub.store.set_preview_file",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "web.routes.english_redub._resolve_name_conflict",
        lambda user_id, desired_name: desired_name,
    )

    resp = authed_client_no_db.post(
        "/api/english-redub/start",
        data={"video": (io.BytesIO(b"video"), "demo.mp4")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 201
    assert updates["script_mode"] == "original"


def test_english_redub_start_rejects_invalid_script_mode(
    authed_client_no_db,
):
    resp = authed_client_no_db.post(
        "/api/english-redub/start",
        data={
            "video": (io.BytesIO(b"video"), "demo.mp4"),
            "script_mode": "invalid",
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert "script_mode" in resp.get_json()["error"]


def test_english_redub_voice_library_accepts_pagination(
    authed_client_no_db,
    monkeypatch,
):
    captured: dict = {}
    monkeypatch.setattr(
        "web.routes.english_redub._query_viewable_project",
        lambda task_id, columns: {
            "state_json": '{"target_lang":"en","steps":{"voice_match":"waiting"}}',
            "user_id": 1,
        },
    )

    def fake_list_voices(**kwargs):
        captured.update(kwargs)
        return {"items": [{"voice_id": "voice-page-3", "name": "Page 3"}], "total": 450}

    monkeypatch.setattr("appcore.voice_library_browse.list_voices", fake_list_voices)
    monkeypatch.setattr("appcore.video_translate_defaults.resolve_default_voice", lambda *args, **kwargs: None)

    resp = authed_client_no_db.get("/api/english-redub/task-voice-pages/voice-library?page=3&page_size=150")

    assert resp.status_code == 200
    assert captured == {
        "language": "en",
        "gender": None,
        "q": None,
        "page": 3,
        "page_size": 150,
    }
    assert resp.get_json()["items"][0]["voice_id"] == "voice-page-3"
