import pytest
from pathlib import Path
from types import SimpleNamespace


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


def test_product_translation_tasks_page_links_open_new_tabs(authed_client_no_db, monkeypatch):
    _stub_product(monkeypatch)

    resp = authed_client_no_db.get("/medias/products/123/translation-tasks")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert body.count('href="/medias/" target="_blank" rel="noopener noreferrer"') == 2


def test_product_translation_tasks_dynamic_links_open_new_tabs():
    script = Path("web/static/medias_translation_tasks.js").read_text(encoding="utf-8")

    assert 'target="_blank" rel="noopener noreferrer"' in script
    assert "newTabAttrs(task.detail_url)" in script
    assert "newTabAttrs(item.detail_url)" in script


def test_product_translation_tasks_api_returns_projection(authed_client_no_db, monkeypatch):
    _stub_product(monkeypatch)
    monkeypatch.setattr(
        "appcore.bulk_translate_projection.list_product_task_ids",
        lambda user_id, product_id: [],
        raising=False,
    )
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


def test_product_translation_tasks_api_delegates_response_builder(
    authed_user_client_no_db,
    monkeypatch,
):
    routes = _stub_product(monkeypatch)
    calls = []

    monkeypatch.setattr(
        routes,
        "_build_product_translation_tasks_response",
        lambda product_id, *, scope_user_id: calls.append((product_id, scope_user_id))
        or SimpleNamespace(payload={"items": [{"id": "bt-user"}]}, status_code=200),
    )

    resp = authed_user_client_no_db.get("/medias/api/products/123/translation-tasks")

    assert resp.status_code == 200
    assert resp.get_json() == {"items": [{"id": "bt-user"}]}
    assert calls == [(123, 2)]


def test_product_translation_tasks_api_admin_scope_crosses_users(authed_client_no_db, monkeypatch):
    _stub_product(monkeypatch)
    calls = []

    monkeypatch.setattr(
        "appcore.bulk_translate_projection.list_product_task_ids",
        lambda user_id, product_id: calls.append(("task_ids", user_id, product_id)) or ["bt-1"],
        raising=False,
    )
    monkeypatch.setattr(
        "appcore.bulk_translate_runtime.sync_task_with_children_once",
        lambda task_id, user_id=None: calls.append(("sync", task_id, user_id)) or {"actions": []},
        raising=False,
    )
    monkeypatch.setattr(
        "appcore.bulk_translate_projection.list_product_tasks",
        lambda user_id, product_id: calls.append(("project", user_id, product_id)) or [],
    )

    resp = authed_client_no_db.get("/medias/api/products/123/translation-tasks")

    assert resp.status_code == 200
    assert calls == [
        ("task_ids", None, 123),
        ("sync", "bt-1", None),
        ("project", None, 123),
    ]


def test_product_translation_tasks_api_user_scope_keeps_owner_filter(authed_user_client_no_db, monkeypatch):
    _stub_product(monkeypatch)
    calls = []

    monkeypatch.setattr(
        "appcore.bulk_translate_projection.list_product_task_ids",
        lambda user_id, product_id: calls.append(("task_ids", user_id, product_id)) or ["bt-1"],
        raising=False,
    )
    monkeypatch.setattr(
        "appcore.bulk_translate_runtime.sync_task_with_children_once",
        lambda task_id, user_id=None: calls.append(("sync", task_id, user_id)) or {"actions": []},
        raising=False,
    )
    monkeypatch.setattr(
        "appcore.bulk_translate_projection.list_product_tasks",
        lambda user_id, product_id: calls.append(("project", user_id, product_id)) or [],
    )

    resp = authed_user_client_no_db.get("/medias/api/products/123/translation-tasks")

    assert resp.status_code == 200
    assert calls == [
        ("task_ids", 2, 123),
        ("sync", "bt-1", 2),
        ("project", 2, 123),
    ]
