from types import SimpleNamespace

import web.routes.medias as medias_route


def test_create_material_link_check_task_collects_cover_and_detail_refs(authed_user_client_no_db, monkeypatch):
    created = {}
    updated = {}

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: {"id": pid, "name": "demo", "product_code": "demo"})
    monkeypatch.setattr("web.routes.medias.medias.is_valid_language", lambda code: code == "de")
    monkeypatch.setattr("web.routes.medias.medias.get_language", lambda code: {"code": code, "name_zh": "德语", "enabled": 1})
    monkeypatch.setattr("web.routes.medias.medias.get_product_covers", lambda pid: {"de": "covers/de.jpg"})
    monkeypatch.setattr(
        "web.routes.medias.medias.list_detail_images",
        lambda pid, lang: [{"id": 11, "object_key": "details/de_1.jpg"}],
    )
    monkeypatch.setattr(medias_route, "_download_media_object", lambda key, path: str(path))
    monkeypatch.setattr(medias_route, "link_check_runner", SimpleNamespace(start=lambda task_id: True), raising=False)

    def fake_create(task_id, task_dir, **kwargs):
        created["task_id"] = task_id
        created["task_dir"] = task_dir
        created.update(kwargs)
        return {"id": task_id, "type": "link_check", "_user_id": 2}

    def fake_set_task(pid, lang, payload):
        updated["pid"] = pid
        updated["lang"] = lang
        updated["payload"] = payload
        return 1

    monkeypatch.setattr(medias_route, "store", SimpleNamespace(create_link_check=fake_create, get=lambda task_id: None), raising=False)
    monkeypatch.setattr("web.routes.medias.medias.set_product_link_check_task", fake_set_task)

    response = authed_user_client_no_db.post(
        "/medias/api/products/7/link-check",
        json={"lang": "de", "link_url": "https://newjoyloo.com/de/products/demo"},
    )

    assert response.status_code == 202
    body = response.get_json()
    assert body["status"] == "queued"
    assert body["reference_count"] == 2
    assert created["target_language"] == "de"
    assert len(created["reference_images"]) == 2
    assert created["reference_images"][0]["filename"].startswith("cover_de")
    assert created["reference_images"][1]["filename"].startswith("detail_001")
    assert updated["pid"] == 7
    assert updated["lang"] == "de"
    assert updated["payload"]["task_id"] == body["task_id"]


def test_create_material_link_check_task_rejects_when_no_reference_images(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: {"id": pid, "name": "demo", "product_code": "demo"})
    monkeypatch.setattr("web.routes.medias.medias.is_valid_language", lambda code: code == "de")
    monkeypatch.setattr("web.routes.medias.medias.get_language", lambda code: {"code": code, "name_zh": "德语", "enabled": 1})
    monkeypatch.setattr("web.routes.medias.medias.get_product_covers", lambda pid: {})
    monkeypatch.setattr("web.routes.medias.medias.list_detail_images", lambda pid, lang: [])

    response = authed_user_client_no_db.post(
        "/medias/api/products/7/link-check",
        json={"lang": "de", "link_url": "https://newjoyloo.com/de/products/demo"},
    )

    assert response.status_code == 400
    assert "参考图" in response.get_json()["error"]


def test_get_material_link_check_summary_uses_latest_associated_task(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.medias.medias.get_product",
        lambda pid: {
            "id": pid,
            "name": "demo",
            "product_code": "demo",
            "link_check_tasks_json": '{"de":{"task_id":"lc-1","status":"done","link_url":"https://x","checked_at":"2026-04-19T22:10:00","summary":{"overall_decision":"done","pass_count":2,"replace_count":0,"review_count":0}}}',
        },
    )
    monkeypatch.setattr("web.routes.medias.medias.is_valid_language", lambda code: code == "de")
    monkeypatch.setattr(
        medias_route,
        "store",
        SimpleNamespace(
            create_link_check=lambda *a, **k: None,
            get=lambda task_id: {
                "id": task_id,
                "type": "link_check",
                "_user_id": 2,
                "status": "done",
                "link_url": "https://x",
                "target_language": "de",
                "target_language_name": "德语",
                "progress": {"total": 2},
                "summary": {"overall_decision": "done", "pass_count": 2, "replace_count": 0, "review_count": 0},
                "items": [],
                "reference_images": [],
                "error": "",
            },
        ),
        raising=False,
    )
    monkeypatch.setattr("web.routes.medias.medias.set_product_link_check_task", lambda pid, lang, payload: 1)

    response = authed_user_client_no_db.get("/medias/api/products/7/link-check/de")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["task"]["task_id"] == "lc-1"
    assert payload["task"]["summary"]["pass_count"] == 2
    assert payload["task"]["has_detail"] is True


def test_get_product_detail_includes_link_check_tasks(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.medias.medias.get_product",
        lambda pid: {
            "id": pid,
            "name": "demo",
            "product_code": "demo",
            "color_people": None,
            "source": None,
            "ad_supported_langs": "",
            "archived": 0,
            "created_at": None,
            "updated_at": None,
            "localized_links_json": None,
            "link_check_tasks_json": '{"de":{"task_id":"lc-123","status":"done","link_url":"https://x"}}',
        },
    )
    monkeypatch.setattr("web.routes.medias.medias.get_product_covers", lambda pid: {})
    monkeypatch.setattr("web.routes.medias.medias.list_copywritings", lambda pid: [])
    monkeypatch.setattr("web.routes.medias.medias.list_items", lambda pid: [])

    response = authed_user_client_no_db.get("/medias/api/products/7")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["product"]["link_check_tasks"]["de"]["task_id"] == "lc-123"
