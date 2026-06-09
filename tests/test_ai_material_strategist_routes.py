from __future__ import annotations


def test_ai_material_strategist_page_requires_login(authed_client_no_db):
    raw_client = authed_client_no_db.application.test_client()

    response = raw_client.get("/medias/ai-material-strategist")

    assert response.status_code == 302


def test_ai_material_strategist_page_requires_admin(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/medias/ai-material-strategist")

    assert response.status_code in {302, 403}


def test_ai_material_strategist_page_renders_for_admin(authed_client_no_db):
    response = authed_client_no_db.get("/medias/ai-material-strategist")

    assert response.status_code == 200
    assert "AI素材军师".encode() in response.data


def test_ai_material_strategist_projects_api_delegates_service(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.medias.ai_material_strategist.service.list_projects",
        lambda limit=30: [{"id": 7, "project_name": "demo", "status": "success"}],
    )

    response = authed_client_no_db.get("/medias/api/ai-material-strategist/projects")

    assert response.status_code == 200
    assert response.get_json()["projects"] == [{"id": 7, "project_name": "demo", "status": "success"}]


def test_ai_material_strategist_create_project_starts_background_job(
    authed_client_no_db,
    monkeypatch,
):
    calls = {}

    monkeypatch.setattr(
        "web.routes.medias.ai_material_strategist.service.create_project_record",
        lambda user_id, project_name=None: {"id": 9, "project_name": project_name, "status": "running"},
    )

    def fake_start_background_task(target, *args, **kwargs):
        calls["target"] = target
        calls["args"] = args
        calls["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(
        "web.routes.medias.ai_material_strategist.start_background_task",
        fake_start_background_task,
    )

    response = authed_client_no_db.post(
        "/medias/api/ai-material-strategist/projects",
        json={"project_name": "demo run", "run_ai": False},
    )

    assert response.status_code == 202
    assert response.get_json()["project"]["id"] == 9
    assert calls["args"] == (9,)
    assert calls["kwargs"]["user_id"] == 1
    assert calls["kwargs"]["run_ai"] is False
