import importlib

import pytest

from web.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAPI_MEDIA_API_KEY", "demo-key")
    monkeypatch.setenv("LOCAL_SERVER_BASE_URL", "http://local.test")
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    import config as _config

    importlib.reload(_config)
    app = create_app()
    return app.test_client()


def test_claim_requires_api_key(client):
    response = client.post("/openapi/medias/shopify-image-localizer/tasks/claim", json={})

    assert response.status_code == 401


def test_claim_returns_task(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.shopify_image_tasks.claim_next_task",
        lambda worker_id, lock_seconds=900: {
            "id": 9,
            "product_id": 7,
            "product_code": "demo-rjc",
            "lang": "it",
            "shopify_product_id": "855",
            "link_url": "url",
        },
    )

    response = client.post(
        "/openapi/medias/shopify-image-localizer/tasks/claim",
        headers={"X-API-Key": "demo-key"},
        json={"worker_id": "w1", "lock_seconds": 300},
    )

    assert response.status_code == 200
    assert response.get_json()["task"]["id"] == 9


def test_complete_marks_task_done(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "web.routes.openapi_materials.shopify_image_tasks.complete_task",
        lambda task_id, result: captured.update({"task_id": task_id, "result": result})
        or {"replace_status": "auto_done"},
    )

    response = client.post(
        "/openapi/medias/shopify-image-localizer/tasks/9/complete",
        headers={"X-API-Key": "demo-key"},
        json={"result": {"carousel": {"ok": 11}}},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert captured["task_id"] == 9


def test_fail_marks_task_failed(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "web.routes.openapi_materials.shopify_image_tasks.fail_task",
        lambda task_id, error_code, error_message, result=None: captured.update(
            {
                "task_id": task_id,
                "error_code": error_code,
                "error_message": error_message,
                "result": result,
            }
        )
        or {"replace_status": "failed"},
    )

    response = client.post(
        "/openapi/medias/shopify-image-localizer/tasks/9/fail",
        headers={"X-API-Key": "demo-key"},
        json={"error_code": "boom", "error_message": "failed", "result": {"x": 1}},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert captured["error_code"] == "boom"
    assert captured["result"] == {"x": 1}
