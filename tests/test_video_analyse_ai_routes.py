from __future__ import annotations

import json
from appcore.ad_material_ai_analysis import ProjectAlreadyRunningError


def test_video_analyse_ai_page_requires_login(authed_client_no_db):
    raw_client = authed_client_no_db.application.test_client()
    response = raw_client.get("/video-analyse-ai/")
    assert response.status_code == 302


def test_video_analyse_ai_page_requires_admin(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/video-analyse-ai/")
    assert response.status_code in {302, 403}


def test_video_analyse_ai_page_renders_for_admin(authed_client_no_db):
    response = authed_client_no_db.get("/video-analyse-ai/")
    assert response.status_code == 200
    assert "投放素材AI%E5%88%86%E6%9E%90" in response.data.decode("utf-8") or "投放素材AI分析" in response.data.decode("utf-8")


def test_video_analyse_ai_public_share_page_does_not_require_login(authed_client_no_db):
    raw_client = authed_client_no_db.application.test_client()
    response = raw_client.get("/video-analyse-ai/share/share_token_1234567890")
    assert response.status_code == 200
    assert b"window.AIMS_PUBLIC_MODE = true;" in response.data


def test_video_analyse_ai_projects_api_delegates_service(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.video_analyse_ai.service.list_projects",
        lambda limit=30: [{"id": 7, "project_name": "demo", "status": "success"}],
    )
    response = authed_client_no_db.get("/video-analyse-ai/api/projects")
    assert response.status_code == 200
    assert response.get_json()["projects"] == [{"id": 7, "project_name": "demo", "status": "success"}]


def test_video_analyse_ai_public_share_api_returns_sanitized_project_without_login(
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
        "web.routes.video_analyse_ai.service.get_project_by_share_token",
        fake_get_project_by_share_token,
    )

    response = raw_client.get("/video-analyse-ai/api/share/share_token_1234567890")
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


def test_video_analyse_ai_delete_project(authed_client_no_db, monkeypatch):
    deleted_calls = []

    def fake_delete_project(project_id):
        deleted_calls.append(project_id)
        if project_id == 999:
            return {"deleted": False, "reason": "not_found"}
        if project_id == 888:
            return {"deleted": False, "reason": "running"}
        return {"deleted": True, "project_id": project_id}

    monkeypatch.setattr(
        "web.routes.video_analyse_ai.service.delete_project",
        fake_delete_project,
    )

    # 1. 成功删除
    response = authed_client_no_db.delete("/video-analyse-ai/api/projects/7")
    assert response.status_code == 200
    assert response.get_json() == {"success": True, "deleted": True, "project_id": 7}
    assert deleted_calls == [7]

    # 2. 未找到
    response = authed_client_no_db.delete("/video-analyse-ai/api/projects/999")
    assert response.status_code == 404

    # 3. 运行中不允许删除
    response = authed_client_no_db.delete("/video-analyse-ai/api/projects/888")
    assert response.status_code == 409
    assert response.get_json()["message"] == "运行中的项目不能删除"
