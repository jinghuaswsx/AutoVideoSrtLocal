from __future__ import annotations


def _stub_product(monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "name": "smart-ball", "user_id": 1},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)
    return r


def test_product_translation_tasks_page_renders(authed_client_no_db, monkeypatch):
    _stub_product(monkeypatch)

    resp = authed_client_no_db.get("/medias/products/123/translation-tasks")

    assert resp.status_code == 200
    assert "翻译任务管理".encode("utf-8") in resp.data
    assert b"translationTasksApp" in resp.data
    assert b"medias_translation_tasks.js" in resp.data


def test_product_translation_tasks_api_returns_projection(authed_client_no_db, monkeypatch):
    _stub_product(monkeypatch)
    monkeypatch.setattr(
        "web.routes.medias.build_product_task_payload",
        lambda user_id, product_id: {
            "product": {"id": product_id, "name": "smart-ball"},
            "batches": [
                {
                    "task_id": "bt-1",
                    "status": "running",
                    "created_at": "2026-04-22T12:00:00",
                    "groups": {
                        "videos": [
                            {
                                "idx": 3,
                                "label": "原视频 #1001 · 德语",
                                "status": "awaiting_voice",
                                "action": {"label": "去选声音", "href": "/multi-translate/child-1"},
                            }
                        ],
                    },
                }
            ],
        },
    )

    resp = authed_client_no_db.get("/medias/api/products/123/translation-tasks")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["product"]["id"] == 123
    assert payload["batches"][0]["task_id"] == "bt-1"
    assert payload["batches"][0]["groups"]["videos"][0]["action"]["label"] == "去选声音"

