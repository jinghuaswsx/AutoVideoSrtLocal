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


def test_state_api_returns_detail_payload(authed_client_no_db):
    task = store.create_subtitle_removal(
        "sr-state-api",
        "uploads/sr-state-api.mp4",
        "output/sr-state-api",
        original_filename="demo.mp4",
        user_id=1,
    )
    store.update(
        task["id"],
        remove_mode="full",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
    )

    response = authed_client_no_db.get(f"/api/subtitle-removal/{task['id']}")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["id"] == task["id"]
    assert payload["remove_mode"] == "full"
    assert payload["media_info"]["resolution"] == "720x1280"


def test_subtitle_removal_submit_persists_mode_and_starts_runner(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-submit",
        "uploads/source.mp4",
        "output/sr-submit",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit",
        status="ready",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    started = {}
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: started.setdefault("task_id", task_id),
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit/submit",
        json={"remove_mode": "box", "selection_box": {"x1": 0, "y1": 1000, "x2": 720, "y2": 1180}},
    )

    assert response.status_code == 202
    assert started["task_id"] == "sr-submit"
    saved = store.get("sr-submit")
    assert saved["remove_mode"] == "box"
    assert saved["selection_box"] == {"x1": 0, "y1": 1000, "x2": 720, "y2": 1180}
    assert saved["position_payload"] == {"l": 0, "t": 1000, "w": 720, "h": 180}
    assert saved["steps"]["submit"] == "queued"


def test_subtitle_removal_submit_supports_full_mode(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-submit-full",
        "uploads/source-full.mp4",
        "output/sr-submit-full",
        original_filename="source-full.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-full",
        status="ready",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    monkeypatch.setattr("web.routes.subtitle_removal.subtitle_removal_runner.start", lambda task_id, user_id=None: None)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-full/submit",
        json={"remove_mode": "full"},
    )

    assert response.status_code == 202
    saved = store.get("sr-submit-full")
    assert saved["selection_box"] == {"x1": 0, "y1": 0, "x2": 720, "y2": 1280}
    assert saved["position_payload"] == {"l": 0, "t": 0, "w": 720, "h": 1280}


def test_subtitle_removal_submit_rejects_duration_over_limit(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-submit-too-long",
        "uploads/source-too-long.mp4",
        "output/sr-submit-too-long",
        original_filename="source-too-long.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-too-long",
        status="ready",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 9999.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    started = []
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: started.append(task_id),
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-too-long/submit",
        json={"remove_mode": "full"},
    )

    assert response.status_code == 400
    assert started == []


def test_subtitle_removal_submit_rejects_non_ready_task_and_does_not_restart_runner(
    authed_client_no_db, monkeypatch
):
    store.create_subtitle_removal(
        "sr-submit-blocked",
        "uploads/source-blocked.mp4",
        "output/sr-submit-blocked",
        original_filename="source-blocked.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-blocked",
        status="running",
        provider_task_id="provider-task-existing",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    started = []
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: started.append(task_id),
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-blocked/submit",
        json={"remove_mode": "full"},
    )

    assert response.status_code == 409
    assert started == []
    saved = store.get("sr-submit-blocked")
    assert saved["status"] == "running"
    assert saved["provider_task_id"] == "provider-task-existing"


def test_subtitle_removal_submit_rejects_when_task_lock_is_held_and_does_not_restart_runner(
    authed_client_no_db, monkeypatch
):
    import web.routes.subtitle_removal as subtitle_removal

    store.create_subtitle_removal(
        "sr-submit-locked",
        "uploads/source-locked.mp4",
        "output/sr-submit-locked",
        original_filename="source-locked.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-locked",
        status="ready",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    started = []
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: started.append(task_id),
    )

    lock = subtitle_removal._get_submit_lock("sr-submit-locked")
    assert lock.acquire(blocking=False)
    try:
        response = authed_client_no_db.post(
            "/api/subtitle-removal/sr-submit-locked/submit",
            json={"remove_mode": "full"},
        )
    finally:
        lock.release()

    assert response.status_code == 409
    assert started == []
    saved = store.get("sr-submit-locked")
    assert saved["status"] == "ready"
    assert saved["provider_task_id"] == ""
