import pytest


@pytest.fixture(autouse=True)
def _patch_bulk_translate_startup_recovery(monkeypatch):
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)


def _stub_product(monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "name": "smart-ball", "product_code": "smart-ball", "user_id": 1},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)
    return r


def test_product_translation_tasks_page_renders(authed_client_no_db, monkeypatch):
    _stub_product(monkeypatch)

    resp = authed_client_no_db.get("/medias/products/123/translation-tasks")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "翻译任务管理" in body
    assert "mediasTranslationTasksMount" in body
    assert "medias_translation_tasks.js" in body


def test_product_translation_tasks_api_returns_projection(authed_client_no_db, monkeypatch):
    _stub_product(monkeypatch)
    monkeypatch.setattr(
        "appcore.bulk_translate_projection.list_product_tasks",
        lambda user_id, product_id: [
            {
                "id": "bt-1",
                "status": "running",
                "items": [
                    {
                        "idx": 3,
                        "kind": "videos",
                        "kind_label": "视频翻译",
                        "status": "awaiting_voice",
                        "status_label": "等待选声音",
                        "detail_url": "/multi-translate/child-1",
                        "manual_step": "voice_selection",
                    }
                ],
            }
        ],
    )

    resp = authed_client_no_db.get("/medias/api/products/123/translation-tasks")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["items"][0]["id"] == "bt-1"
    assert payload["items"][0]["items"][0]["status"] == "awaiting_voice"
    assert payload["items"][0]["items"][0]["detail_url"] == "/multi-translate/child-1"
