from __future__ import annotations


def test_upload_missing_prompt_returns_json_400_without_db(authed_client_no_db):
    response = authed_client_no_db.post(
        "/api/video-creation/upload",
        data={},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_regenerate_missing_task_returns_json_404_without_db(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr("web.routes.video_creation.recover_project_if_needed", lambda *a, **k: None)
    monkeypatch.setattr("web.routes.video_creation.db_query_one", lambda *a, **k: None)

    response = authed_client_no_db.post("/api/video-creation/vc-missing/regenerate")

    assert response.status_code == 404
    assert response.get_json() == {"error": "not found"}


def test_delete_missing_task_returns_json_404_without_db(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr("web.routes.video_creation.db_query_one", lambda *a, **k: None)

    response = authed_client_no_db.delete("/api/video-creation/vc-missing")

    assert response.status_code == 404
    assert response.get_json() == {"error": "not found"}
