from __future__ import annotations

import io
from pathlib import Path

from web import store


def test_tos_upload_bootstrap_rejects_new_task_creation(authed_client_no_db):
    response = authed_client_no_db.post(
        "/api/tos-upload/bootstrap",
        json={"original_filename": "demo.mp4", "file_size": 12345, "content_type": "video/mp4"},
    )

    assert response.status_code == 410
    payload = response.get_json()
    assert "本地" in payload["error"]
    assert "TOS" in payload["error"]


def test_tos_upload_complete_rejects_new_task_creation(authed_client_no_db):
    response = authed_client_no_db.post(
        "/api/tos-upload/complete",
        json={
            "task_id": "task-from-tos",
            "object_key": "uploads/1/task-from-tos/demo.mp4",
            "original_filename": "demo.mp4",
            "file_size": 4321,
            "content_type": "video/mp4",
        },
    )

    assert response.status_code == 410
    payload = response.get_json()
    assert "本地" in payload["error"]
    assert store.get("task-from-tos") is None


def test_de_translate_complete_rejects_new_pure_tos_creation(authed_client_no_db):
    response = authed_client_no_db.post(
        "/api/de-translate/complete",
        json={
            "task_id": "de-task-from-tos",
            "object_key": "uploads/1/de-task-from-tos/demo.mp4",
            "original_filename": "demo.mp4",
        },
    )

    assert response.status_code == 410
    assert "本地" in response.get_json()["error"]
    assert store.get("de-task-from-tos") is None


def test_de_translate_start_accepts_local_multipart_and_marks_local_primary(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.de_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.de_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.de_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.de_translate.db_execute", lambda sql, args: None)

    response = authed_client_no_db.post(
        "/api/de-translate/start",
        data={"video": (io.BytesIO(b"de-video"), "demo.mp4")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    task = store.get(payload["task_id"])
    assert task["type"] == "de_translate"
    assert task["delivery_mode"] == "local_primary"
    assert task["source_tos_key"] == ""
    assert task["source_language"] == "en"
    assert task["source_object_info"]["original_filename"] == "demo.mp4"
    assert task["source_object_info"]["content_type"] == "video/mp4"
    assert task["source_object_info"]["file_size"] == len(b"de-video")
    assert task["source_object_info"]["storage_backend"] == "local"
    assert task["source_object_info"]["uploaded_at"]
    assert Path(task["video_path"]).read_bytes() == b"de-video"


def test_fr_translate_complete_rejects_new_pure_tos_creation(authed_client_no_db):
    response = authed_client_no_db.post(
        "/api/fr-translate/complete",
        json={
            "task_id": "fr-task-from-tos",
            "object_key": "uploads/1/fr-task-from-tos/demo.mp4",
            "original_filename": "demo.mp4",
        },
    )

    assert response.status_code == 410
    assert "本地" in response.get_json()["error"]
    assert store.get("fr-task-from-tos") is None


def test_fr_translate_start_accepts_local_multipart_and_marks_local_primary(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.fr_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.fr_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.fr_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.fr_translate.db_execute", lambda sql, args: None)

    response = authed_client_no_db.post(
        "/api/fr-translate/start",
        data={"video": (io.BytesIO(b"fr-video"), "demo.mp4")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    task = store.get(payload["task_id"])
    assert task["type"] == "fr_translate"
    assert task["delivery_mode"] == "local_primary"
    assert task["source_tos_key"] == ""
    assert task["source_language"] == "en"
    assert task["source_object_info"]["original_filename"] == "demo.mp4"
    assert task["source_object_info"]["content_type"] == "video/mp4"
    assert task["source_object_info"]["file_size"] == len(b"fr-video")
    assert task["source_object_info"]["storage_backend"] == "local"
    assert task["source_object_info"]["uploaded_at"]
    assert Path(task["video_path"]).read_bytes() == b"fr-video"
