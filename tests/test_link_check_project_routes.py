from __future__ import annotations

import json


def test_link_check_list_page_renders_global_projects(authed_user_client_no_db, monkeypatch):
    recovered = []

    monkeypatch.setattr("web.routes.link_check.recover_all_interrupted_tasks", lambda: recovered.append(True))
    monkeypatch.setattr(
        "web.routes.link_check.query",
        lambda sql, args=(): [
            {
                "id": "lc-global-1",
                "display_name": "Global Link Check A",
                "original_filename": "",
                "status": "queued",
                "created_at": None,
            },
            {
                "id": "lc-global-2",
                "display_name": "Global Link Check B",
                "original_filename": "",
                "status": "done",
                "created_at": None,
            },
        ],
    )

    response = authed_user_client_no_db.get("/link-check")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert recovered == [True]
    assert "Global Link Check A" in body
    assert "Global Link Check B" in body
    assert "/link-check/lc-global-1" in body


def test_create_link_check_task_auto_detects_target_language(authed_user_client_no_db, monkeypatch):
    from web import store

    created = {}

    def fake_create(task_id, task_dir, **kwargs):
        created.update({"task_id": task_id, "task_dir": task_dir, **kwargs})
        return {"id": task_id, "type": "link_check"}

    monkeypatch.setattr(store, "create_link_check", fake_create)
    monkeypatch.setattr("web.routes.link_check.link_check_runner.start", lambda tid: True)
    monkeypatch.setattr(
        "web.routes.link_check.medias.list_languages",
        lambda: [
            {"code": "de", "name_zh": "德语", "enabled": 1},
            {"code": "fr", "name_zh": "法语", "enabled": 1},
        ],
    )
    monkeypatch.setattr(
        "web.routes.link_check.medias.get_language",
        lambda code: {"code": code, "name_zh": "德语" if code == "de" else "法语", "enabled": 1},
    )

    response = authed_user_client_no_db.post(
        "/api/link-check/tasks",
        data={
            "link_url": "https://shop.example.com/de/products/demo",
            "target_language": "",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["task_id"] == created["task_id"]
    assert payload["detail_url"].endswith(f"/link-check/{created['task_id']}")
    assert created["target_language"] == "de"
    assert created["target_language_name"] == "德语"


def test_link_check_detail_page_bootstraps_state_from_projects_row(authed_user_client_no_db, monkeypatch):
    state = {
        "id": "lc-db-1",
        "type": "link_check",
        "status": "done",
        "link_url": "https://shop.example.com/de/products/demo",
        "target_language": "de",
        "target_language_name": "德语",
        "progress": {"total": 1},
        "summary": {"overall_decision": "done"},
        "reference_images": [],
        "items": [],
    }

    monkeypatch.setattr("web.routes.link_check.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr("web.routes.link_check.store.get", lambda task_id: None)
    monkeypatch.setattr(
        "web.routes.link_check.query_one",
        lambda sql, args: {
            "id": "lc-db-1",
            "type": "link_check",
            "display_name": "Persisted Link Check",
            "status": "done",
            "state_json": json.dumps(state, ensure_ascii=False),
        },
    )

    response = authed_user_client_no_db.get("/link-check/lc-db-1")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Persisted Link Check" in body
    assert "lc-db-1" in body
    assert "https://shop.example.com/de/products/demo" in body


def test_link_check_detail_page_uses_project_row_metadata_when_store_hits(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.link_check.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr(
        "web.routes.link_check.query_one",
        lambda sql, args: {
            "id": "lc-store-1",
            "type": "link_check",
            "display_name": "Store-backed Link Check",
            "status": "running",
            "state_json": "",
        },
    )
    monkeypatch.setattr(
        "web.routes.link_check.store.get",
        lambda task_id: {
            "id": "lc-store-1",
            "type": "link_check",
            "status": "running",
            "link_url": "https://shop.example.com/de/products/demo",
            "target_language": "de",
            "target_language_name": "德语",
            "progress": {},
            "summary": {},
            "reference_images": [],
            "items": [],
        },
    )

    response = authed_user_client_no_db.get("/link-check/lc-store-1")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Store-backed Link Check" in body
    assert "lc-store-1" in body


def test_link_check_api_rejects_deleted_project_even_if_store_has_task(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.link_check.store.get", lambda task_id: {"id": task_id, "type": "link_check"})
    monkeypatch.setattr("web.routes.link_check.query_one", lambda sql, args: None)

    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-deleted-1")

    assert response.status_code == 404


def test_link_check_api_does_not_fallback_to_store_when_project_query_errors(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.link_check.store.get",
        lambda task_id: {
            "id": task_id,
            "type": "link_check",
            "status": "done",
            "link_url": "https://shop.example.com/de/products/demo",
            "target_language": "de",
            "target_language_name": "德语",
            "progress": {},
            "summary": {},
            "reference_images": [],
            "items": [],
        },
    )

    def _boom(sql, args):
        raise RuntimeError("db down")

    monkeypatch.setattr("web.routes.link_check.query_one", _boom)

    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-db-error-1")

    assert response.status_code == 500


def test_link_check_rename_route_updates_global_project(authed_user_client_no_db, monkeypatch):
    calls = {}

    def fake_query_one(sql, args):
        calls["query"] = {"sql": sql, "args": args}
        return {"id": "lc-rename-1"}

    def fake_execute(sql, args):
        calls["execute"] = {"sql": sql, "args": args}

    monkeypatch.setattr(
        "web.routes.link_check.query_one",
        fake_query_one,
    )
    monkeypatch.setattr(
        "web.routes.link_check.execute",
        fake_execute,
    )
    monkeypatch.setattr("web.routes.link_check.store.get", lambda task_id: {"id": task_id, "type": "link_check"})
    monkeypatch.setattr(
        "web.routes.link_check.store.update",
        lambda task_id, **fields: calls.setdefault("update", {"task_id": task_id, "fields": fields}),
    )

    response = authed_user_client_no_db.patch(
        "/api/link-check/tasks/lc-rename-1",
        json={"display_name": "Renamed Link Check"},
    )

    assert response.status_code == 200
    assert response.get_json()["display_name"] == "Renamed Link Check"
    assert "user_id" not in calls["query"]["sql"]
    assert "type = 'link_check'" in calls["query"]["sql"]
    assert calls["execute"]["args"] == ("Renamed Link Check", "lc-rename-1")
    assert calls["update"]["fields"]["display_name"] == "Renamed Link Check"


def test_link_check_delete_route_soft_deletes_global_project(authed_user_client_no_db, monkeypatch):
    calls = {}

    def fake_query_one(sql, args):
        calls["query"] = {"sql": sql, "args": args}
        return {
            "id": "lc-delete-1",
            "task_dir": "C:/tmp/lc-delete-1",
            "state_json": json.dumps({"tos_keys": ["x"]}, ensure_ascii=False),
        }

    def fake_execute(sql, args):
        calls["execute"] = {"sql": sql, "args": args}

    monkeypatch.setattr(
        "web.routes.link_check.query_one",
        fake_query_one,
    )
    monkeypatch.setattr("web.routes.link_check.store.get", lambda task_id: {"id": task_id, "type": "link_check"})
    monkeypatch.setattr(
        "web.routes.link_check.cleanup.collect_task_tos_keys",
        lambda task: ["collected-key"],
    )
    monkeypatch.setattr(
        "web.routes.link_check.cleanup.delete_task_storage",
        lambda task: calls.setdefault("cleanup", task),
    )
    monkeypatch.setattr(
        "web.routes.link_check.execute",
        fake_execute,
    )
    monkeypatch.setattr(
        "web.routes.link_check.store.update",
        lambda task_id, **fields: calls.setdefault("update", {"task_id": task_id, "fields": fields}),
    )

    response = authed_user_client_no_db.delete("/api/link-check/tasks/lc-delete-1")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert "user_id" not in calls["query"]["sql"]
    assert "type = 'link_check'" in calls["query"]["sql"]
    assert "deleted_at" in calls["execute"]["sql"]
    assert calls["cleanup"]["task_dir"] == "C:/tmp/lc-delete-1"
    assert calls["cleanup"]["tos_keys"] == ["collected-key"]
    assert calls["update"]["fields"]["status"] == "deleted"


def test_link_check_detail_page_bootstrap_json_escapes_script_terminator(authed_user_client_no_db, monkeypatch):
    state = {
        "id": "lc-xss-1",
        "type": "link_check",
        "status": "done",
        "link_url": "https://shop.example.com/de/products/demo",
        "target_language": "de",
        "target_language_name": "德语",
        "summary": {"note": "x</script><div>boom</div>"},
        "progress": {},
        "reference_images": [],
        "items": [],
    }

    monkeypatch.setattr("web.routes.link_check.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr("web.routes.link_check.store.get", lambda task_id: None)
    monkeypatch.setattr(
        "web.routes.link_check.query_one",
        lambda sql, args: {
            "id": "lc-xss-1",
            "type": "link_check",
            "display_name": "Safe Link Check",
            "status": "done",
            "state_json": json.dumps(state, ensure_ascii=False),
        },
    )

    response = authed_user_client_no_db.get("/link-check/lc-xss-1")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "x</script><div>boom</div>" not in body
    assert "\\u003c/script\\u003e\\u003cdiv\\u003eboom\\u003c/div\\u003e" in body
