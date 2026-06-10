from __future__ import annotations

from appcore.ai_material_strategist import ProjectAlreadyRunningError


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


def test_ai_material_strategist_public_share_page_does_not_require_login(authed_client_no_db):
    raw_client = authed_client_no_db.application.test_client()

    response = raw_client.get("/medias/ai-material-strategist/share/share_token_1234567890")

    assert response.status_code == 200
    assert b"window.AIMS_PUBLIC_MODE = true;" in response.data
    assert b"medias_ai_material_strategist_public_base" not in response.data


def test_ai_material_strategist_public_share_page_rejects_invalid_token(authed_client_no_db):
    raw_client = authed_client_no_db.application.test_client()

    response = raw_client.get("/medias/ai-material-strategist/share/bad.token")

    assert response.status_code == 404


def test_ai_material_strategist_projects_api_delegates_service(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.medias.ai_material_strategist.service.list_projects",
        lambda limit=30: [{"id": 7, "project_name": "demo", "status": "success"}],
    )

    response = authed_client_no_db.get("/medias/api/ai-material-strategist/projects")

    assert response.status_code == 200
    assert response.get_json()["projects"] == [{"id": 7, "project_name": "demo", "status": "success"}]


def test_ai_material_strategist_public_share_api_returns_sanitized_project_without_login(
    authed_client_no_db,
    monkeypatch,
):
    raw_client = authed_client_no_db.application.test_client()

    def fake_get_project_by_share_token(share_token):
        assert share_token == "share_token_1234567890"
        return {
            "id": 7,
            "project_name": "shared",
            "status": "success",
            "ranking_prompt": {"prompt": "internal"},
            "data_snapshot": {"products": [{"product_id": 1}]},
            "products": [
                {
                    "product_code": "demo-rjc",
                    "action_items": [
                        {
                            "type": "supplement_workbench",
                            "label": "补素材",
                            "url": "/medias/demo-rjc",
                            "method": "POST",
                            "payload": {"x": 1},
                        }
                    ],
                    "country_summary": [
                        {
                            "country_code": "DE",
                            "blocking_task": {
                                "task_id": 44,
                                "status_group": "in_progress",
                                "task_url": "/tasks/detail/44",
                            },
                        }
                    ],
                    "mingkong_materials": [
                        {
                            "video_name": "demo.mp4",
                            "video_url": "/medias/api/mk-video?path=demo.mp4",
                        }
                    ],
                    "local_materials": [
                        {
                            "object_key": "tasks/12/medias/test.mp4",
                            "video_url": "/medias/object?object_key=tasks/12/medias/test.mp4",
                        }
                    ],
                }
            ],
        }

    monkeypatch.setattr(
        "web.routes.medias.ai_material_strategist.service.get_project_by_share_token",
        fake_get_project_by_share_token,
    )

    response = raw_client.get("/medias/api/ai-material-strategist/share/share_token_1234567890")

    assert response.status_code == 200
    project = response.get_json()["project"]
    assert project["public"] is True
    assert "ranking_prompt" not in project
    assert "data_snapshot" not in project
    action = project["products"][0]["action_items"][0]
    assert "url" not in action
    assert "method" not in action
    assert "payload" not in action
    assert "task_url" not in project["products"][0]["country_summary"][0]["blocking_task"]
    
    # 验证视频链接已正确加回并且拼接了 share_token
    mk_video = project["products"][0]["mingkong_materials"][0]["video_url"]
    assert "share_token=share_token_1234567890" in mk_video
    
    # 验证本地素材视频链接转换成了公开格式
    local_video = project["products"][0]["local_materials"][0]["video_url"]
    assert local_video == "/medias/obj/tasks/12/medias/test.mp4"


def test_ai_material_strategist_public_share_api_rejects_invalid_token(authed_client_no_db):
    raw_client = authed_client_no_db.application.test_client()

    response = raw_client.get("/medias/api/ai-material-strategist/share/bad.token")

    assert response.status_code == 404


def test_ai_material_strategist_share_project_api_returns_public_url(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.medias.ai_material_strategist.service.ensure_project_share",
        lambda project_id: {
            "project_id": project_id,
            "share_token": "share_token_1234567890",
            "share_enabled_at": "2026-06-09 20:30:00",
        },
    )

    response = authed_client_no_db.post("/medias/api/ai-material-strategist/projects/7/share")

    assert response.status_code == 200
    share = response.get_json()["share"]
    assert share["project_id"] == 7
    assert share["share_url"].endswith("/medias/ai-material-strategist/share/share_token_1234567890")


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


def test_ai_material_strategist_create_project_rejects_when_running(
    authed_client_no_db,
    monkeypatch,
):
    running = {"id": 8, "project_name": "running", "status": "running"}

    def fake_create_project_record(user_id, project_name=None):
        raise ProjectAlreadyRunningError(running)

    monkeypatch.setattr(
        "web.routes.medias.ai_material_strategist.service.create_project_record",
        fake_create_project_record,
    )

    response = authed_client_no_db.post(
        "/medias/api/ai-material-strategist/projects",
        json={"project_name": "demo run"},
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["running_project"]["id"] == 8
