from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from web import store


def test_tos_upload_bootstrap_returns_signed_put_payload(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.tos_upload.tos_clients.generate_signed_upload_url",
        lambda object_key: f"https://signed-upload.example.com/{object_key}",
    )
    monkeypatch.setattr("web.routes.tos_upload.TOS_UPLOAD_CLEANUP_MAX_AGE_SECONDS", 7200, raising=False)

    response = authed_client_no_db.post(
        "/api/tos-upload/bootstrap",
        json={"original_filename": "demo.mp4", "file_size": 12345, "content_type": "video/mp4"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["task_id"]
    assert payload["object_key"].endswith("/demo.mp4")
    assert payload["upload_url"] == f"https://signed-upload.example.com/{payload['object_key']}"
    assert payload["max_object_age_seconds"] == 7200


def test_tos_upload_complete_creates_task_from_tos_object(tmp_path, authed_client_no_db, monkeypatch):
    captured_updates = []

    monkeypatch.setattr("web.routes.tos_upload.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.tos_upload.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.tos_upload.tos_clients.object_exists", lambda object_key: True)
    monkeypatch.setattr(
        "web.routes.tos_upload.tos_clients.head_object",
        lambda object_key: SimpleNamespace(content_length=4321),
    )
    monkeypatch.setattr("web.routes.tos_upload.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.tos_upload.db_execute", lambda sql, args: captured_updates.append((sql, args)))

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

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["task_id"] == "task-from-tos"
    task = store.get("task-from-tos")
    assert task["source_tos_key"] == "uploads/1/task-from-tos/demo.mp4"
    assert task["delivery_mode"] == "pure_tos"
    assert task["source_object_info"]["file_size"] == 4321
    assert Path(task["task_dir"]).exists()
    assert task["video_path"].endswith("task-from-tos.mp4")
    assert task["display_name"] == "demo"
    assert any("UPDATE projects SET display_name" in sql for sql, _ in captured_updates)


def test_de_translate_complete_marks_task_as_pure_tos(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.de_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.de_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("appcore.tos_clients.object_exists", lambda object_key: True)
    monkeypatch.setattr(
        "appcore.tos_clients.head_object",
        lambda object_key: SimpleNamespace(content_length=3210),
    )
    monkeypatch.setattr("web.routes.de_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.de_translate.db_execute", lambda sql, args: None)

    response = authed_client_no_db.post(
        "/api/de-translate/complete",
        json={
            "task_id": "de-task-from-tos",
            "object_key": "uploads/1/de-task-from-tos/demo.mp4",
            "original_filename": "demo.mp4",
        },
    )

    assert response.status_code == 201
    task = store.get("de-task-from-tos")
    assert task["source_tos_key"] == "uploads/1/de-task-from-tos/demo.mp4"
    assert task["delivery_mode"] == "pure_tos"
    assert task["type"] == "de_translate"


def test_fr_translate_complete_marks_task_as_pure_tos(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.fr_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.fr_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("appcore.tos_clients.object_exists", lambda object_key: True)
    monkeypatch.setattr(
        "appcore.tos_clients.head_object",
        lambda object_key: SimpleNamespace(content_length=6543),
    )
    monkeypatch.setattr("web.routes.fr_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.fr_translate.db_execute", lambda sql, args: None)

    response = authed_client_no_db.post(
        "/api/fr-translate/complete",
        json={
            "task_id": "fr-task-from-tos",
            "object_key": "uploads/1/fr-task-from-tos/demo.mp4",
            "original_filename": "demo.mp4",
        },
    )

    assert response.status_code == 201
    task = store.get("fr-task-from-tos")
    assert task["source_tos_key"] == "uploads/1/fr-task-from-tos/demo.mp4"
    assert task["delivery_mode"] == "pure_tos"
    assert task["type"] == "fr_translate"
