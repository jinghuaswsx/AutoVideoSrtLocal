from __future__ import annotations

import io


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
