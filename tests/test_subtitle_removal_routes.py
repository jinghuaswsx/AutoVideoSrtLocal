from __future__ import annotations

from types import SimpleNamespace

from web import store


def _mock_subtitle_removal_upload_env(tmp_path, monkeypatch, *, probe_media_info=None, thumbnail_path=None):
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video")
    if thumbnail_path is None:
        thumbnail = tmp_path / "thumbnail.jpg"
        thumbnail.write_bytes(b"jpg")
        thumbnail_path = str(thumbnail)

    monkeypatch.setattr("web.routes.subtitle_removal.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.subtitle_removal.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.is_tos_configured", lambda: True)
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.build_source_object_key",
        lambda user_id, task_id, name: f"uploads/{user_id}/{task_id}/{name}",
    )
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.generate_signed_upload_url",
        lambda key: f"https://upload.example/{key}",
    )
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.object_exists", lambda object_key: True)
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.head_object",
        lambda object_key: SimpleNamespace(content_length=2048),
    )
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.download_file",
        lambda object_key, local_path: str(source_video),
    )
    monkeypatch.setattr(
        "web.routes.subtitle_removal.probe_media_info",
        probe_media_info or (lambda path: {"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0}),
    )
    monkeypatch.setattr(
        "web.routes.subtitle_removal.extract_thumbnail",
        lambda video_path, output_dir: thumbnail_path,
    )
    monkeypatch.setattr("web.routes.subtitle_removal.db_execute", lambda sql, args: None)


def _bootstrap_subtitle_removal_upload(authed_client_no_db):
    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/bootstrap",
        json={"original_filename": "source.mp4"},
    )
    assert response.status_code == 200
    return response.get_json()


def test_subtitle_removal_complete_upload_prepares_first_frame(tmp_path, authed_client_no_db, monkeypatch):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch)

    payload = _bootstrap_subtitle_removal_upload(authed_client_no_db)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": payload["task_id"],
            "original_filename": "source.mp4",
            "object_key": payload["object_key"],
            "content_type": "video/mp4",
            "file_size": 2048,
        },
    )

    assert response.status_code == 201
    task = store.get(payload["task_id"])
    assert task["steps"]["prepare"] == "done"
    assert task["thumbnail_path"].endswith("thumbnail.jpg")
    assert task["media_info"]["resolution"] == "720x1280"


def test_subtitle_removal_bootstrap_rejects_invalid_video_extension(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.is_tos_configured", lambda: True)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/bootstrap",
        json={"original_filename": "notes.txt"},
    )

    assert response.status_code == 400


def test_subtitle_removal_complete_upload_rejects_invalid_file_size(tmp_path, authed_client_no_db, monkeypatch):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch)
    payload = _bootstrap_subtitle_removal_upload(authed_client_no_db)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": payload["task_id"],
            "original_filename": "source.mp4",
            "object_key": payload["object_key"],
            "content_type": "video/mp4",
            "file_size": "not-an-int",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "file_size must be an integer"


def test_subtitle_removal_complete_upload_rejects_invalid_video_extension(tmp_path, authed_client_no_db, monkeypatch):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": "bad-ext-task",
            "original_filename": "notes.txt",
            "object_key": "uploads/1/bad-ext-task/notes.txt",
            "content_type": "text/plain",
            "file_size": 128,
        },
    )

    assert response.status_code == 400


def test_subtitle_removal_complete_upload_rejects_probe_failure(tmp_path, authed_client_no_db, monkeypatch):
    _mock_subtitle_removal_upload_env(
        tmp_path,
        monkeypatch,
        probe_media_info=lambda path: {"width": 0, "height": 0, "resolution": "", "duration": 0.0},
    )
    payload = _bootstrap_subtitle_removal_upload(authed_client_no_db)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": payload["task_id"],
            "original_filename": "source.mp4",
            "object_key": payload["object_key"],
            "content_type": "video/mp4",
            "file_size": 2048,
        },
    )

    assert response.status_code == 422
    task = store.get(payload["task_id"])
    assert task["status"] != "ready"
    assert task["steps"]["prepare"] == "pending"
    assert task["source_tos_key"] == payload["object_key"]
    assert task["source_object_info"]["file_size"] == 2048
    assert task["source_object_info"]["content_type"] == "video/mp4"


def test_subtitle_removal_complete_upload_rejects_thumbnail_failure(tmp_path, authed_client_no_db, monkeypatch):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch, thumbnail_path=None)
    monkeypatch.setattr("web.routes.subtitle_removal.extract_thumbnail", lambda video_path, output_dir: None)
    payload = _bootstrap_subtitle_removal_upload(authed_client_no_db)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": payload["task_id"],
            "original_filename": "source.mp4",
            "object_key": payload["object_key"],
            "content_type": "video/mp4",
            "file_size": 2048,
        },
    )

    assert response.status_code == 422
    task = store.get(payload["task_id"])
    assert task["status"] != "ready"
    assert task["steps"]["prepare"] == "pending"


def test_subtitle_removal_complete_upload_rejects_head_object_failure_but_keeps_source_state(
    tmp_path, authed_client_no_db, monkeypatch
):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.head_object",
        lambda object_key: (_ for _ in ()).throw(RuntimeError("head failed")),
    )
    payload = _bootstrap_subtitle_removal_upload(authed_client_no_db)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": payload["task_id"],
            "original_filename": "source.mp4",
            "object_key": payload["object_key"],
            "content_type": "video/mp4",
            "file_size": 2048,
        },
    )

    assert response.status_code != 201
    task = store.get(payload["task_id"])
    assert task is not None
    assert task["status"] != "ready"
    assert task["steps"]["prepare"] == "pending"
    assert task["source_tos_key"] == payload["object_key"]
    assert task["source_object_info"]["file_size"] == 2048
    assert task["source_object_info"]["content_type"] == "video/mp4"


def test_subtitle_removal_complete_upload_rejects_download_failure_but_keeps_source_state(
    tmp_path, authed_client_no_db, monkeypatch
):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.download_file",
        lambda object_key, local_path: (_ for _ in ()).throw(RuntimeError("download failed")),
    )
    payload = _bootstrap_subtitle_removal_upload(authed_client_no_db)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": payload["task_id"],
            "original_filename": "source.mp4",
            "object_key": payload["object_key"],
            "content_type": "video/mp4",
            "file_size": 2048,
        },
    )

    assert response.status_code != 201
    task = store.get(payload["task_id"])
    assert task is not None
    assert task["status"] != "ready"
    assert task["steps"]["prepare"] == "pending"
    assert task["source_tos_key"] == payload["object_key"]
    assert task["source_object_info"]["file_size"] == 2048
    assert task["source_object_info"]["content_type"] == "video/mp4"


def test_subtitle_removal_source_artifact_serves_owned_task_thumbnail(tmp_path, authed_client_no_db, monkeypatch):
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"jpg")
    task = store.create_subtitle_removal(
        "sr-artifact",
        str(tmp_path / "video.mp4"),
        str(tmp_path / "task"),
        original_filename="video.mp4",
        user_id=1,
    )
    store.update(task["id"], thumbnail_path=str(thumbnail))

    response = authed_client_no_db.get(f"/api/subtitle-removal/{task['id']}/artifact/source")

    assert response.status_code == 200
    assert response.data == b"jpg"


def test_subtitle_removal_source_artifact_returns_404_without_thumbnail(authed_client_no_db):
    store.create_subtitle_removal(
        "sr-artifact-missing",
        "uploads/video.mp4",
        "output/sr-artifact-missing",
        original_filename="video.mp4",
        user_id=1,
    )

    response = authed_client_no_db.get("/api/subtitle-removal/sr-artifact-missing/artifact/source")

    assert response.status_code == 404


def test_subtitle_removal_source_artifact_returns_404_for_other_users_task(tmp_path, authed_client_no_db):
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"jpg")
    task = store.create_subtitle_removal(
        "sr-artifact-other-user",
        str(tmp_path / "video.mp4"),
        str(tmp_path / "task"),
        original_filename="video.mp4",
        user_id=2,
    )
    store.update(task["id"], thumbnail_path=str(thumbnail))

    response = authed_client_no_db.get(f"/api/subtitle-removal/{task['id']}/artifact/source")

    assert response.status_code == 404


def test_subtitle_removal_source_artifact_returns_404_for_non_subtitle_removal_task(tmp_path, authed_client_no_db):
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"jpg")
    task = store.create(
        "plain-task-artifact",
        str(tmp_path / "video.mp4"),
        str(tmp_path / "task"),
        original_filename="video.mp4",
        user_id=1,
    )
    store.update(task["id"], thumbnail_path=str(thumbnail))

    response = authed_client_no_db.get(f"/api/subtitle-removal/{task['id']}/artifact/source")

    assert response.status_code == 404
