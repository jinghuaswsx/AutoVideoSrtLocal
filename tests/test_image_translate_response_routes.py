from __future__ import annotations


def test_system_prompts_rejects_unsupported_language(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as route_mod

    monkeypatch.setattr(route_mod.its, "is_image_translate_language_supported", lambda code: False)

    resp = authed_client_no_db.get("/api/image-translate/system-prompts?lang=xx")

    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_upload_bootstrap_rejects_missing_files(authed_client_no_db):
    resp = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={})

    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_upload_complete_rejects_unknown_task_before_db_access(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/api/image-translate/upload/complete",
        json={"task_id": "missing"},
    )

    assert resp.status_code == 403
    assert "error" in resp.get_json()
