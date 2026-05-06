from __future__ import annotations

import pytest

import web.routes.subtitle_removal as subtitle_removal
from web import store


@pytest.fixture(autouse=True)
def clear_upload_bootstrap_reservations():
    subtitle_removal._upload_bootstrap_reservations.clear()
    yield
    subtitle_removal._upload_bootstrap_reservations.clear()


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


def _put_uploaded_subtitle_removal_video(authed_client_no_db, payload, *, data: bytes = b"video", content_type: str = "video/mp4"):
    response = authed_client_no_db.put(
        payload["upload_url"],
        data=data,
        content_type=content_type,
    )
    assert response.status_code == 204


def _mock_public_source_stage(monkeypatch, url: str = "https://example.com/source.mp4"):
    monkeypatch.setattr(
        "web.routes.subtitle_removal._ensure_public_source_url",
        lambda task_id, task: url,
    )


def test_subtitle_removal_complete_upload_prepares_first_frame(tmp_path, authed_client_no_db, monkeypatch):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch)

    payload = _bootstrap_subtitle_removal_upload(authed_client_no_db)
    _put_uploaded_subtitle_removal_video(authed_client_no_db, payload)

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
    assert task["step_messages"]["prepare"] == "首帧提取和媒体信息解析已完成"
    assert task["thumbnail_path"].endswith("thumbnail.jpg")
    assert task["media_info"]["resolution"] == "720x1280"
    assert task["source_tos_key"] == ""
    assert task["source_object_info"]["storage_backend"] == "local"
    assert task["subtitle_backend"] == "volc"


def test_subtitle_removal_complete_upload_persists_local_vsr_backend(
    tmp_path, authed_client_no_db, monkeypatch
):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch)

    payload = _bootstrap_subtitle_removal_upload(authed_client_no_db)
    _put_uploaded_subtitle_removal_video(authed_client_no_db, payload)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": payload["task_id"],
            "original_filename": "source.mp4",
            "object_key": payload["object_key"],
            "content_type": "video/mp4",
            "file_size": 2048,
            "subtitle_backend": "local_vsr",
        },
    )

    assert response.status_code == 201
    task = store.get(payload["task_id"])
    assert task["subtitle_backend"] == "local_vsr"


def test_subtitle_removal_complete_upload_rejects_invalid_backend(
    tmp_path, authed_client_no_db, monkeypatch
):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch)

    payload = _bootstrap_subtitle_removal_upload(authed_client_no_db)
    _put_uploaded_subtitle_removal_video(authed_client_no_db, payload)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": payload["task_id"],
            "original_filename": "source.mp4",
            "object_key": payload["object_key"],
            "content_type": "video/mp4",
            "file_size": 2048,
            "subtitle_backend": "unknown",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "subtitle_backend must be volc or local_vsr"


def test_subtitle_removal_complete_upload_rejects_unreserved_task_id(tmp_path, authed_client_no_db, monkeypatch):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch)
    task_id = "sr-unreserved"
    object_key = f"uploads/1/{task_id}/source.mp4"

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": task_id,
            "original_filename": "source.mp4",
            "object_key": object_key,
            "content_type": "video/mp4",
            "file_size": 2048,
        },
    )

    assert response.status_code == 403
    assert store.get(task_id) is None


def test_subtitle_removal_complete_upload_rejects_foreign_reserved_task_id(tmp_path, authed_client_no_db, monkeypatch):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch)
    task_id = "sr-foreign-reserved"
    object_key = f"uploads/1/{task_id}/source.mp4"
    subtitle_removal._upload_bootstrap_reservations[task_id] = {
        "task_id": task_id,
        "user_id": 2,
        "original_filename": "source.mp4",
        "object_key": object_key,
    }

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/complete",
        json={
            "task_id": task_id,
            "original_filename": "source.mp4",
            "object_key": object_key,
            "content_type": "video/mp4",
            "file_size": 2048,
        },
    )

    assert response.status_code == 403
    assert store.get(task_id) is None


def test_subtitle_removal_bootstrap_rejects_invalid_video_extension(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.is_tos_configured", lambda: True)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/bootstrap",
        json={"original_filename": "notes.txt"},
    )

    assert response.status_code == 400


def test_subtitle_removal_bootstrap_allows_local_upload_without_tos(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.is_tos_configured", lambda: False)
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.build_source_object_key",
        lambda user_id, task_id, name: f"uploads/{user_id}/{task_id}/{name}",
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/upload/bootstrap",
        json={"original_filename": "source.mp4"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["upload_url"].endswith(f"/api/subtitle-removal/upload/local/{payload['task_id']}")


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
    _put_uploaded_subtitle_removal_video(authed_client_no_db, payload)

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
    assert task["source_tos_key"] == ""
    assert task["source_object_info"]["file_size"] == len(b"video")
    assert task["source_object_info"]["content_type"] == "video/mp4"
    assert task["source_object_info"]["storage_backend"] == "local"


def test_subtitle_removal_complete_upload_rejects_thumbnail_failure(tmp_path, authed_client_no_db, monkeypatch):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch, thumbnail_path=None)
    monkeypatch.setattr("web.routes.subtitle_removal.extract_thumbnail", lambda video_path, output_dir: None)
    payload = _bootstrap_subtitle_removal_upload(authed_client_no_db)
    _put_uploaded_subtitle_removal_video(authed_client_no_db, payload)

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


def test_subtitle_removal_complete_upload_keeps_source_local_until_submit(
    tmp_path, authed_client_no_db, monkeypatch
):
    _mock_subtitle_removal_upload_env(tmp_path, monkeypatch)
    payload = _bootstrap_subtitle_removal_upload(authed_client_no_db)
    _put_uploaded_subtitle_removal_video(authed_client_no_db, payload)
    uploaded = []
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.upload_file",
        lambda local_path, object_key: uploaded.append((local_path, object_key)),
    )

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
    assert task is not None
    assert task["status"] == "ready"
    assert task["source_tos_key"] == ""
    assert task["source_object_info"]["file_size"] == len(b"video")
    assert task["source_object_info"]["content_type"] == "video/mp4"
    assert task["source_object_info"]["storage_backend"] == "local"
    assert uploaded == []


def test_subtitle_removal_complete_upload_rejects_when_local_file_missing(
    tmp_path, authed_client_no_db, monkeypatch
):
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

    assert response.status_code == 400
    assert response.get_json()["error"] == "uploaded video file missing"


def test_subtitle_removal_source_artifact_serves_owned_task_thumbnail(tmp_path, authed_client_no_db, monkeypatch):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    thumbnail = task_dir / "thumbnail.jpg"
    thumbnail.write_bytes(b"jpg")
    task = store.create_subtitle_removal(
        "sr-artifact",
        str(tmp_path / "video.mp4"),
        str(task_dir),
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


def test_subtitle_removal_source_artifact_serves_other_users_task_when_global_visible(tmp_path, authed_client_no_db):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    thumbnail = task_dir / "thumbnail.jpg"
    thumbnail.write_bytes(b"jpg")
    task = store.create_subtitle_removal(
        "sr-artifact-other-user",
        str(tmp_path / "video.mp4"),
        str(task_dir),
        original_filename="video.mp4",
        user_id=2,
    )
    store.update(task["id"], thumbnail_path=str(thumbnail))

    response = authed_client_no_db.get(f"/api/subtitle-removal/{task['id']}/artifact/source")

    assert response.status_code == 200
    assert response.data == b"jpg"


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


def test_subtitle_removal_source_artifact_rejects_thumbnail_outside_task_storage(tmp_path, authed_client_no_db):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"jpg")
    task = store.create_subtitle_removal(
        "sr-artifact-outside",
        str(task_dir / "video.mp4"),
        str(task_dir),
        original_filename="video.mp4",
        user_id=1,
    )
    store.update(task["id"], thumbnail_path=str(thumbnail))

    response = authed_client_no_db.get(f"/api/subtitle-removal/{task['id']}/artifact/source")

    assert response.status_code == 404


def test_subtitle_removal_source_video_artifact_serves_owned_task_video(tmp_path, authed_client_no_db):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    video_path = task_dir / "source.mp4"
    video_path.write_bytes(b"video")
    task = store.create_subtitle_removal(
        "sr-source-video",
        str(video_path),
        str(task_dir),
        original_filename="video.mp4",
        user_id=1,
    )

    response = authed_client_no_db.get(f"/api/subtitle-removal/{task['id']}/artifact/source-video")

    assert response.status_code == 200
    assert response.data == b"video"


def test_state_api_returns_detail_payload(tmp_path, authed_client_no_db):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    task = store.create_subtitle_removal(
        "sr-state-api",
        str(video_path),
        "output/sr-state-api",
        original_filename="demo.mp4",
        user_id=1,
    )
    store.update(
        task["id"],
        remove_mode="full",
        source_tos_key="uploads/1/sr-state-api/demo.mp4",
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
    assert payload["source_video_url"].endswith(f"/api/subtitle-removal/{task['id']}/artifact/source-video")


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
    _mock_public_source_stage(monkeypatch)
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
    assert saved["step_messages"]["prepare"] == "首帧提取和媒体信息解析已完成"
    assert saved["step_messages"]["submit"] == "等待后台提交去字幕任务"


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
    _mock_public_source_stage(monkeypatch)
    monkeypatch.setattr("web.routes.subtitle_removal.subtitle_removal_runner.start", lambda task_id, user_id=None: None)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-full/submit",
        json={"remove_mode": "full"},
    )

    assert response.status_code == 202
    saved = store.get("sr-submit-full")
    assert saved["selection_box"] == {"x1": 0, "y1": 0, "x2": 720, "y2": 1280}
    assert saved["position_payload"] == {"l": 0, "t": 0, "w": 720, "h": 1280}


def test_subtitle_removal_submit_local_vsr_skips_public_source_stage_and_persists_options(
    authed_client_no_db, monkeypatch
):
    store.create_subtitle_removal(
        "sr-submit-local-vsr",
        "uploads/source-local-vsr.mp4",
        "output/sr-submit-local-vsr",
        original_filename="source-local-vsr.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-local-vsr",
        status="ready",
        subtitle_backend="local_vsr",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    monkeypatch.setattr(
        "web.routes.subtitle_removal._ensure_public_source_url",
        lambda task_id, task: (_ for _ in ()).throw(AssertionError("local VSR should read local source directly")),
    )
    monkeypatch.setattr("web.routes.subtitle_removal.subtitle_removal_runner.start", lambda task_id, user_id=None: None)

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-local-vsr/submit",
        json={"remove_mode": "full"},
    )

    assert response.status_code == 202
    saved = store.get("sr-submit-local-vsr")
    assert saved["subtitle_backend"] == "local_vsr"
    assert saved["local_vsr_options"] == {
        "detection": "ocr",
        "ocr_engine": "easyocr",
        "inpaint": "lama",
        "vsr": "real-esrgan",
        "roi": "bottom_20%",
    }


def test_subtitle_removal_submit_stages_public_source_on_demand(tmp_path, authed_client_no_db, monkeypatch):
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video")
    store.create_subtitle_removal(
        "sr-submit-public-source",
        str(source_video),
        str(tmp_path / "task"),
        original_filename="demo.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-public-source",
        status="ready",
        video_path=str(source_video),
        source_tos_key="",
        media_info={
            "width": 1280,
            "height": 720,
            "resolution": "1280x720",
            "duration": 8.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    monkeypatch.setattr("web.routes.subtitle_removal.subtitle_removal_runner.start", lambda task_id, user_id=None: None)
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.build_source_object_key",
        lambda user_id, task_id, original_filename: f"uploads/{user_id}/{task_id}/{original_filename}",
    )
    uploaded = []
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.upload_file",
        lambda local_path, object_key: uploaded.append((local_path, object_key)),
    )
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.generate_signed_download_url",
        lambda object_key, expires=86400: f"https://example.com/{object_key}",
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-public-source/submit",
        json={"remove_mode": "full", "erase_text_type": "subtitle"},
    )

    assert response.status_code == 202
    saved = store.get("sr-submit-public-source")
    assert saved["source_tos_key"] == "uploads/1/sr-submit-public-source/demo.mp4"
    assert uploaded == [(str(source_video), "uploads/1/sr-submit-public-source/demo.mp4")]


def test_subtitle_removal_submit_stages_public_source_in_backup_tos_when_enabled(
    tmp_path, authed_client_no_db, monkeypatch
):
    from appcore import tos_backup_storage

    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video")
    store.create_subtitle_removal(
        "sr-submit-backup-source",
        str(source_video),
        str(tmp_path / "task"),
        original_filename="demo.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-backup-source",
        status="ready",
        video_path=str(source_video),
        source_tos_key="",
        media_info={
            "width": 1280,
            "height": 720,
            "resolution": "1280x720",
            "duration": 8.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    monkeypatch.setattr("web.routes.subtitle_removal.subtitle_removal_runner.start", lambda task_id, user_id=None: None)
    monkeypatch.setattr(tos_backup_storage, "is_enabled", lambda: True)
    monkeypatch.setattr(tos_backup_storage.config, "TOS_BACKUP_PREFIX", "FILES")
    monkeypatch.setattr(tos_backup_storage.config, "TOS_BACKUP_ENV", "test")
    backup_uploaded = []
    legacy_uploaded = []
    monkeypatch.setattr(
        tos_backup_storage,
        "upload_local_file",
        lambda local_path, object_key=None: backup_uploaded.append((local_path, object_key)) or object_key,
    )
    monkeypatch.setattr(
        tos_backup_storage,
        "generate_signed_download_url",
        lambda object_key, expires=86400: f"https://backup.example/{object_key}",
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.upload_file",
        lambda local_path, object_key: legacy_uploaded.append((local_path, object_key)),
    )
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.generate_signed_download_url",
        lambda object_key, expires=86400: f"https://legacy.example/{object_key}",
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-backup-source/submit",
        json={"remove_mode": "full", "erase_text_type": "subtitle"},
    )

    expected_key = "FILES/test/subtitle_removal/uploads/1/sr-submit-backup-source/demo.mp4"
    assert response.status_code == 202
    saved = store.get("sr-submit-backup-source")
    assert saved["source_tos_key"] == expected_key
    assert saved["source_object_info"]["public_source_storage_backend"] == "tos_backup"
    assert saved["source_object_info"]["public_source_key"] == expected_key
    assert backup_uploaded == [(str(source_video), expected_key)]
    assert legacy_uploaded == []


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


def test_subtitle_removal_result_artifact_serves_local_result_file(tmp_path, authed_client_no_db):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    result_path = task_dir / "result.cleaned.mp4"
    result_path.write_bytes(b"result-video")
    task = store.create_subtitle_removal(
        "sr-result-artifact",
        "uploads/source.mp4",
        str(task_dir),
        original_filename="source.mp4",
        user_id=1,
    )
    store.update("sr-result-artifact", status="done", result_video_path=str(result_path))

    response = authed_client_no_db.get("/api/subtitle-removal/sr-result-artifact/artifact/result")

    assert response.status_code == 200
    assert response.data == b"result-video"


def test_subtitle_removal_result_artifact_rejects_local_result_outside_task_storage(tmp_path, authed_client_no_db):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    result_path = tmp_path / "result.cleaned.mp4"
    result_path.write_bytes(b"result-video")
    task = store.create_subtitle_removal(
        "sr-result-outside",
        "uploads/source.mp4",
        str(task_dir),
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(task["id"], status="done", result_video_path=str(result_path))

    response = authed_client_no_db.get("/api/subtitle-removal/sr-result-outside/artifact/result")

    assert response.status_code == 404


def test_subtitle_removal_download_result_redirects_to_tos_when_local_file_missing(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-download",
        "uploads/source.mp4",
        "output/sr-download",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-download",
        status="done",
        result_tos_key="artifacts/1/sr-download/subtitle_removal/result.cleaned.mp4",
        result_video_path="",
    )
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.generate_signed_download_url", lambda key, expires=None: "https://tos.example/result.cleaned.mp4")

    response = authed_client_no_db.get("/api/subtitle-removal/sr-download/download/result")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://tos.example/result.cleaned.mp4"


def test_subtitle_removal_resubmit_cleans_previous_result_artifacts(authed_client_no_db, monkeypatch, tmp_path):
    output_root = tmp_path / "output"
    result_path = output_root / "sr-resubmit" / "result.cleaned.mp4"
    result_path.parent.mkdir(parents=True)
    result_path.write_bytes(b"result-video")
    monkeypatch.setattr(subtitle_removal, "OUTPUT_DIR", str(output_root))
    task = store.create_subtitle_removal(
        "sr-resubmit",
        "uploads/source.mp4",
        "output/sr-resubmit",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-resubmit",
        status="error",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
        remove_mode="box",
        selection_box={"x1": 0, "y1": 1000, "x2": 720, "y2": 1180},
        position_payload={"l": 0, "t": 1000, "w": 720, "h": 180},
        provider_task_id="provider-task-1",
        provider_status="failed",
        provider_result_url="https://provider.example/result.mp4",
        result_tos_key="artifacts/1/sr-resubmit/subtitle_removal/result.cleaned.mp4",
        result_video_path=str(result_path),
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    monkeypatch.setattr("web.routes.subtitle_removal._submission_age_seconds", lambda task_id, task: 3600)
    _mock_public_source_stage(monkeypatch)
    started = {}
    deleted = []
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.delete_object", lambda key: deleted.append(key))
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: started.setdefault("task_id", task_id),
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-resubmit/resubmit",
        json={"remove_mode": "full"},
    )

    assert response.status_code == 202
    assert started["task_id"] == "sr-resubmit"
    assert deleted == ["artifacts/1/sr-resubmit/subtitle_removal/result.cleaned.mp4"]
    assert not result_path.exists()
    saved = store.get("sr-resubmit")
    assert saved["provider_task_id"] == ""
    assert saved["provider_status"] == "queued"
    assert saved["provider_result_url"] == ""
    assert saved["result_tos_key"] == ""
    assert saved["result_video_path"] == ""
    assert saved["remove_mode"] == "full"
    assert saved["selection_box"] == {"x1": 0, "y1": 0, "x2": 720, "y2": 1280}
    assert saved["position_payload"] == {"l": 0, "t": 0, "w": 720, "h": 1280}


def test_subtitle_removal_cleanup_skips_result_path_outside_storage_roots(monkeypatch, tmp_path):
    outside_result = tmp_path / "outside-result.mp4"
    outside_result.write_bytes(b"result")
    deleted_tos_keys = []

    monkeypatch.setattr(subtitle_removal, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(subtitle_removal, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        subtitle_removal.tos_clients,
        "delete_object",
        lambda key: deleted_tos_keys.append(key),
    )

    subtitle_removal._cleanup_result_artifacts(
        {
            "result_tos_key": "artifacts/1/sr-clean/subtitle_removal/result.cleaned.mp4",
            "result_video_path": str(outside_result),
        }
    )

    assert deleted_tos_keys == ["artifacts/1/sr-clean/subtitle_removal/result.cleaned.mp4"]
    assert outside_result.read_bytes() == b"result"


def test_subtitle_removal_resubmit_recent_provider_task_resumes_poll_without_new_submit(
    authed_client_no_db, monkeypatch, tmp_path
):
    result_path = tmp_path / "result.cleaned.mp4"
    result_path.write_bytes(b"old-result")
    store.create_subtitle_removal(
        "sr-resubmit-recent",
        "uploads/source.mp4",
        "output/sr-resubmit-recent",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-resubmit-recent",
        status="interrupted",
        provider_task_id="provider-task-recent",
        provider_task_submitted_at=1234.0,
        result_tos_key="artifacts/1/sr-resubmit-recent/subtitle_removal/result.cleaned.mp4",
        result_video_path=str(result_path),
        steps={
            "prepare": "done",
            "submit": "done",
            "poll": "interrupted",
            "download_result": "pending",
            "upload_result": "pending",
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    monkeypatch.setattr("web.routes.subtitle_removal._submission_age_seconds", lambda task_id, task: 60)
    monkeypatch.setattr(
        "web.routes.subtitle_removal._ensure_public_source_url",
        lambda task_id, task: (_ for _ in ()).throw(AssertionError("recent resubmit should not stage a new source")),
    )
    deleted = []
    started = []
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.delete_object", lambda key: deleted.append(key))
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: started.append((task_id, user_id)) or True,
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-resubmit-recent/resubmit",
        json={"remove_mode": "full"},
    )

    assert response.status_code == 202
    assert response.get_json()["status"] == "running"
    assert started == [("sr-resubmit-recent", 1)]
    assert deleted == []
    assert result_path.exists()
    saved = store.get("sr-resubmit-recent")
    assert saved["provider_task_id"] == "provider-task-recent"
    assert saved["result_tos_key"] == "artifacts/1/sr-resubmit-recent/subtitle_removal/result.cleaned.mp4"
    assert saved["steps"]["poll"] == "running"


def test_subtitle_removal_resume_poll_restarts_runner_for_existing_provider_task(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-resume",
        "uploads/source.mp4",
        "output/sr-resume",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update("sr-resume", status="running", provider_task_id="provider-task-1")
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    started = {}
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: started.setdefault("task_id", task_id),
    )

    response = authed_client_no_db.post("/api/subtitle-removal/sr-resume/resume-poll")

    assert response.status_code == 202
    assert started["task_id"] == "sr-resume"


def test_subtitle_removal_resume_poll_rejects_duplicate_runner_start(authed_client_no_db, monkeypatch):
    task = store.create_subtitle_removal(
        "sr-resume-busy",
        "uploads/source.mp4",
        "output/sr-resume-busy",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update("sr-resume-busy", status="running", provider_task_id="provider-task-1")
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    monkeypatch.setattr("web.routes.subtitle_removal.subtitle_removal_runner.is_running", lambda task_id: True)
    started = []
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: started.append(task_id),
    )

    response = authed_client_no_db.post("/api/subtitle-removal/sr-resume-busy/resume-poll")

    assert response.status_code == 409
    assert started == []


def test_subtitle_removal_delete_soft_deletes_project_and_cleans_result_artifacts_only(
    authed_client_no_db, monkeypatch, tmp_path
):
    output_root = tmp_path / "output"
    result_path = output_root / "sr-delete" / "result.cleaned.mp4"
    result_path.parent.mkdir(parents=True)
    result_path.write_bytes(b"result-video")
    monkeypatch.setattr(subtitle_removal, "OUTPUT_DIR", str(output_root))
    task = store.create_subtitle_removal(
        "sr-delete",
        "uploads/source.mp4",
        "output/sr-delete",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-delete",
        source_tos_key="uploads/1/sr-delete/source.mp4",
        result_tos_key="artifacts/1/sr-delete/subtitle_removal/result.cleaned.mp4",
        result_video_path=str(result_path),
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    deleted = []
    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.delete_object", lambda key: deleted.append(key))
    monkeypatch.setattr("web.routes.subtitle_removal.db_execute", lambda *args, **kwargs: None)

    response = authed_client_no_db.delete("/api/subtitle-removal/sr-delete")

    assert response.status_code == 204
    assert deleted == ["artifacts/1/sr-delete/subtitle_removal/result.cleaned.mp4"]
    assert not result_path.exists()


def test_subtitle_removal_submit_persists_erase_text_type_text(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-submit-erase-text",
        "uploads/source.mp4",
        "output/sr-submit-erase-text",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-erase-text",
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
    _mock_public_source_stage(monkeypatch)
    started = {}
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: started.setdefault("task_id", task_id),
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-erase-text/submit",
        json={"remove_mode": "full", "erase_text_type": "text"},
    )

    assert response.status_code == 202
    assert started["task_id"] == "sr-submit-erase-text"
    saved = store.get("sr-submit-erase-text")
    assert saved["erase_text_type"] == "text"


def test_subtitle_removal_submit_defaults_erase_text_type_to_subtitle(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-submit-erase-default",
        "uploads/source.mp4",
        "output/sr-submit-erase-default",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-erase-default",
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
    _mock_public_source_stage(monkeypatch)
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: None,
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-erase-default/submit",
        json={"remove_mode": "full"},
    )

    assert response.status_code == 202
    saved = store.get("sr-submit-erase-default")
    assert saved["erase_text_type"] == "subtitle"


def test_subtitle_removal_submit_rejects_invalid_erase_text_type(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-submit-erase-bogus",
        "uploads/source.mp4",
        "output/sr-submit-erase-bogus",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-erase-bogus",
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

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-erase-bogus/submit",
        json={"remove_mode": "full", "erase_text_type": "bogus"},
    )

    assert response.status_code == 400
    assert "erase_text_type" in (response.get_json() or {}).get("error", "")
    assert started == []


def test_subtitle_removal_resubmit_overrides_erase_text_type(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-resubmit-erase",
        "uploads/source.mp4",
        "output/sr-resubmit-erase",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-resubmit-erase",
        status="done",
        erase_text_type="subtitle",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    _mock_public_source_stage(monkeypatch)
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: None,
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-resubmit-erase/resubmit",
        json={"remove_mode": "full", "erase_text_type": "text"},
    )

    assert response.status_code == 202
    saved = store.get("sr-resubmit-erase")
    assert saved["erase_text_type"] == "text"


def test_subtitle_removal_state_api_returns_erase_text_type(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-state-erase",
        "uploads/source.mp4",
        "output/sr-state-erase",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-state-erase",
        status="ready",
        erase_text_type="text",
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0},
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_task", lambda task_id: store.get(task_id))

    response = authed_client_no_db.get("/api/subtitle-removal/sr-state-erase")

    assert response.status_code == 200
    assert response.get_json().get("erase_text_type") == "text"


def test_subtitle_removal_default_display_name_uses_original_stem_and_timestamp():
    from datetime import datetime as _datetime

    result = subtitle_removal._default_display_name(
        "summer.product.video.mp4",
        now=_datetime(2026, 4, 29, 18, 15, 30),
    )

    assert result == "summer.product.video-0429-181530"


def test_subtitle_removal_list_returns_erase_text_type(authed_client_no_db, monkeypatch):
    import json as _json
    monkeypatch.setattr(
        "web.routes.subtitle_removal.db_query",
        lambda sql, args=None: [
            {
                "id": "sr-list-erase",
                "user_id": 1,
                "status": "done",
                "state_json": _json.dumps({
                    "display_name": "demo",
                    "original_filename": "demo.mp4",
                    "status": "done",
                    "erase_text_type": "text",
                    "media_info": {"resolution": "720x1280", "duration": 10.0},
                    "thumbnail_path": "",
                    "provider_status": "success",
                    "provider_result_url": "",
                }),
                "created_at": None,
                "username": "tester",
            }
        ],
    )

    response = authed_client_no_db.get("/api/subtitle-removal/list")

    assert response.status_code == 200
    items = (response.get_json() or {}).get("items") or []
    assert items, "list 接口应返回至少一条"
    assert items[0]["erase_text_type"] == "text"


def test_subtitle_removal_list_applies_submitter_and_project_search_filters(authed_client_no_db, monkeypatch):
    captured = {}

    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args=None: {"ok": 1})

    def fake_db_query(sql, args=None):
        if "SELECT DISTINCT p.user_id" in sql:
            return [
                {"user_id": 2, "username": "guqian", "submitter_name": "guqian"},
            ]
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr("web.routes.subtitle_removal.db_query", fake_db_query)

    response = authed_client_no_db.get("/api/subtitle-removal/list?user_id=2&q=Last")

    assert response.status_code == 200
    payload = response.get_json() or {}
    assert payload["items"] == []
    assert payload["users"] == [{"id": 2, "name": "guqian"}]
    assert "p.user_id = %s" in captured["sql"]
    assert "LOWER(COALESCE(p.display_name, '')) LIKE %s" in captured["sql"]
    assert "LOWER(COALESCE(p.original_filename, '')) LIKE %s" in captured["sql"]
    assert captured["args"] == (2, "%last%", "%last%", "%last%")


def test_subtitle_removal_list_prefers_submitter_chinese_name(authed_client_no_db, monkeypatch):
    import json as _json

    captured = {}

    monkeypatch.setattr("web.routes.subtitle_removal.db_query_one", lambda sql, args=None: {"ok": 1})

    def fake_db_query(sql, args=None):
        captured["sql"] = sql
        return [
            {
                "id": "sr-list-submitter",
                "user_id": 1,
                "status": "done",
                "state_json": _json.dumps({
                    "display_name": "demo",
                    "original_filename": "demo.mp4",
                    "status": "done",
                    "erase_text_type": "subtitle",
                    "media_info": {"resolution": "720x1280", "duration": 10.0},
                    "thumbnail_path": "",
                    "provider_status": "success",
                    "provider_result_url": "",
                }),
                "created_at": None,
                "username": "admin",
                "submitter_name": "张三",
            }
        ]

    monkeypatch.setattr("web.routes.subtitle_removal.db_query", fake_db_query)

    response = authed_client_no_db.get("/api/subtitle-removal/list")

    assert response.status_code == 200
    items = (response.get_json() or {}).get("items") or []
    assert "COALESCE(NULLIF(TRIM(u.xingming), ''), u.username) AS submitter_name" in captured["sql"]
    assert items[0]["created_by"] == "张三"
