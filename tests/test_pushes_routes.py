"""推送管理蓝图骨架测试。"""


def test_pushes_index_requires_login():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    # /pushes 先被 Flask 308 重定向到 /pushes/（strict_slashes），
    # 再被 @login_required 302 重定向到登录页。
    # follow_redirects=True 跟到最终：要么 200 但是登录页（含"登录"），
    # 要么停在 302（登录页本身）。
    resp = client.get("/pushes/", follow_redirects=False)
    # 未登录应该跳转到登录页
    assert resp.status_code in (301, 302)


def test_pushes_index_loads_for_admin(authed_client_no_db):
    resp = authed_client_no_db.get("/pushes/")
    assert resp.status_code == 200
    assert b"\xe6\x8e\xa8\xe9\x80\x81\xe7\xae\xa1\xe7\x90\x86" in resp.data  # "推送管理"


def test_pushes_api_items_requires_login():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    resp = client.get("/pushes/api/items")
    assert resp.status_code in (302, 401)


def test_pushes_api_items_returns_list(logged_in_client):
    resp = logged_in_client.get("/pushes/api/items?page=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert data["page"] == 1


def test_pushes_api_items_filter_status(logged_in_client):
    resp = logged_in_client.get("/pushes/api/items?status=pending&page=1")
    assert resp.status_code == 200
    data = resp.get_json()
    for it in data["items"]:
        assert it["status"] == "pending"


import pytest


@pytest.fixture
def user_id_int():
    from appcore.db import query_one
    return int(query_one("SELECT id FROM users ORDER BY id ASC LIMIT 1")["id"])


@pytest.fixture
def seeded_item(user_id_int):
    from appcore import medias
    import uuid
    pid = medias.create_product(user_id_int, "路由测试产品")
    code = f"route-test-{uuid.uuid4().hex[:8]}"
    medias.update_product(pid, product_code=code, ad_supported_langs="de")
    item_id = medias.create_item(
        pid, user_id_int, "demo.mp4", "u/1/m/1/demo.mp4",
        cover_object_key="u/1/m/1/cover.jpg",
        file_size=100, duration_seconds=5.0, lang="de",
    )
    medias.replace_copywritings(pid, [{"title": "T", "body": "B"}], lang="de")
    yield pid, item_id
    medias.soft_delete_product(pid)


def test_payload_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/pushes/api/items/99999/payload")
    assert resp.status_code == 403


def test_payload_rejects_already_pushed(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET pushed_at=NOW() WHERE id=%s", (item_id,))
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 409


def test_payload_rejects_not_ready(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET cover_object_key=NULL WHERE id=%s", (item_id,))
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "not_ready"
    assert "has_cover" in data["missing"]


def test_payload_rejects_probe_fail(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    monkeypatch.setattr("appcore.pushes.probe_ad_url", lambda url: (False, "HTTP 404"))
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "link_not_adapted"


def test_payload_success(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    monkeypatch.setattr("appcore.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "appcore.pushes.tos_clients.generate_signed_media_download_url",
        lambda key: f"https://signed/{key}",
    )
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "payload" in data
    assert "push_url" in data
    assert data["payload"]["videos"][0]["url"].startswith("https://signed/")


def test_mark_pushed_updates_state(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    resp = logged_in_client.post(
        f"/pushes/api/items/{item_id}/mark-pushed",
        json={"request_payload": {"a": 1}, "response_body": "ok"},
    )
    assert resp.status_code == 204
    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is not None


def test_mark_failed_keeps_pushed_at_null(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    resp = logged_in_client.post(
        f"/pushes/api/items/{item_id}/mark-failed",
        json={"request_payload": {"a": 1}, "error_message": "boom"},
    )
    assert resp.status_code == 204
    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] is not None


def test_reset_clears_state(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET pushed_at=NOW(), latest_push_id=1 WHERE id=%s", (item_id,))
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/reset")
    assert resp.status_code == 204
    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] is None


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 400


def _stub_probe_ok(monkeypatch):
    monkeypatch.setattr("appcore.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "appcore.pushes.tos_clients.generate_signed_media_download_url",
        lambda key: f"https://signed/{key}",
    )


def _seed_en_push_texts(product_id: int):
    """推送就绪要求英文 idx=1 文案能解析成「标题/文案/描述」三段。"""
    from appcore import medias
    medias.replace_copywritings(
        product_id,
        [{
            "title": "T_EN",
            "body": "标题: 产品标题\n文案: 产品文案\n描述: 产品描述",
        }],
        lang="en",
    )


def test_push_rejects_not_configured(logged_in_client, seeded_item, monkeypatch):
    _, item_id = seeded_item
    monkeypatch.setattr("config.PUSH_TARGET_URL", "")
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "push_target_not_configured"


def test_push_rejects_not_ready(logged_in_client, seeded_item, monkeypatch):
    _, item_id = seeded_item
    monkeypatch.setattr("config.PUSH_TARGET_URL", "http://downstream.invalid/push")
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET cover_object_key=NULL WHERE id=%s", (item_id,))
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "not_ready"
    assert "has_cover" in data["missing"]


def test_push_success_marks_pushed(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    _seed_en_push_texts(pid)
    _stub_probe_ok(monkeypatch)
    monkeypatch.setattr("config.PUSH_TARGET_URL", "http://downstream.invalid/push")

    captured = {}
    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs.get("json")
        return _FakeResponse(200, '{"ok":true}')
    monkeypatch.setattr("web.routes.pushes.requests.post", fake_post)

    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["upstream_status"] == 200
    assert captured["url"] == "http://downstream.invalid/push"
    assert captured["payload"]["mode"] == "create"

    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is not None


def test_push_downstream_4xx_records_failure(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    _seed_en_push_texts(pid)
    _stub_probe_ok(monkeypatch)
    monkeypatch.setattr("config.PUSH_TARGET_URL", "http://downstream.invalid/push")
    monkeypatch.setattr(
        "web.routes.pushes.requests.post",
        lambda url, **kw: _FakeResponse(400, "bad request"),
    )
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 502
    assert resp.get_json()["error"] == "downstream_error"

    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] is not None


def test_push_network_error_records_failure(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    _seed_en_push_texts(pid)
    _stub_probe_ok(monkeypatch)
    monkeypatch.setattr("config.PUSH_TARGET_URL", "http://downstream.invalid/push")

    import requests as _req
    def boom(url, **kw):
        raise _req.ConnectionError("connection refused")
    monkeypatch.setattr("web.routes.pushes.requests.post", boom)

    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 502
    assert resp.get_json()["error"] == "downstream_unreachable"

    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] is not None


def test_push_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.post("/pushes/api/items/99999/push")
    assert resp.status_code == 403


def test_logs_returns_history(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore import pushes as pushes_mod
    pushes_mod.record_push_failure(item_id=item_id, operator_user_id=1,
                                   payload={}, error_message="e", response_body=None)
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/logs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["logs"]) >= 1
