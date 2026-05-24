import json
import pytest


def _client_for_user(monkeypatch, *, role="user", username="worker", permissions=None):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    from web.app import create_app

    fake_user = {
        "id": 3,
        "username": username,
        "role": role,
        "is_active": 1,
        "permissions": json.dumps(permissions) if permissions is not None else None,
    }

    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 3 else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "3"
        session["_fresh"] = True
    return client


def test_import_endpoint_requires_login(authed_client_no_db):
    raw_client = authed_client_no_db.application.test_client()
    resp = raw_client.post("/xuanpin/api/meta-hot-posts/123/import", json={"translator_id": 3})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_import_endpoint_requires_admin(monkeypatch):
    client = _client_for_user(monkeypatch, role="user", username="test-user")
    resp = client.post("/xuanpin/api/meta-hot-posts/123/import", json={"translator_id": 3})
    assert resp.status_code == 302
    assert resp.headers.get("Location") == "/"



def test_import_endpoint_requires_translator_id(authed_client_no_db):
    resp = authed_client_no_db.post("/xuanpin/api/meta-hot-posts/123/import", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "translator_id_required"


def test_import_endpoint_success_calls_service_and_triggers_evaluation(authed_client_no_db, monkeypatch):
    import_called = []
    thread_started = []

    def fake_import_hot_post(post_id, translator_id, actor_user_id):
        import_called.append({
            "post_id": post_id,
            "translator_id": translator_id,
            "actor_user_id": actor_user_id,
        })
        return {
            "media_product_id": 456,
            "media_item_id": 789,
            "is_new_product": True,
        }

    def fake_start_tracked_thread(*args, **kwargs):
        thread_started.append((args, kwargs))
        return True

    monkeypatch.setattr("appcore.meta_hot_posts.service.import_hot_post", fake_import_hot_post)
    monkeypatch.setattr("appcore.runner_lifecycle.start_tracked_thread", fake_start_tracked_thread)
    monkeypatch.setattr("appcore.material_evaluation.evaluate_product_if_ready", lambda *args, **kwargs: None)

    resp = authed_client_no_db.post("/xuanpin/api/meta-hot-posts/123/import", json={"translator_id": 3})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["media_product_id"] == 456
    assert data["media_item_id"] == 789
    assert data["is_new_product"] is True

    assert len(import_called) == 1
    assert import_called[0]["post_id"] == 123
    assert import_called[0]["translator_id"] == 3
    assert import_called[0]["actor_user_id"] == 1  # authed_client_no_db fake_user.id is 1

    assert len(thread_started) == 1
    args, kwargs = thread_started[0]
    assert kwargs["project_type"] == "material_evaluation"
    assert kwargs["task_id"] == "456"
    assert kwargs["args"] == (456,)
    assert kwargs["entrypoint"] == "meta_hot_posts.import"


def test_import_endpoint_value_error_returns_400(authed_client_no_db, monkeypatch):
    def fake_import_hot_post(post_id, translator_id, actor_user_id):
        raise ValueError("本地视频尚未就绪，请等待视频本地化完成")

    monkeypatch.setattr("appcore.meta_hot_posts.service.import_hot_post", fake_import_hot_post)

    resp = authed_client_no_db.post("/xuanpin/api/meta-hot-posts/123/import", json={"translator_id": 3})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "本地视频尚未就绪，请等待视频本地化完成"
