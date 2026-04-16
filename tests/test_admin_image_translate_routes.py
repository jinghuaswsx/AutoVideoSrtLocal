from unittest.mock import patch


def test_get_prompts_requires_admin(monkeypatch):
    # 非 admin 应拒绝；用普通 user 重新构造 client
    from web.app import create_app
    normal_user = {"id": 2, "username": "u", "role": "user", "is_active": 1}
    monkeypatch.setattr("web.auth.get_by_id", lambda uid: normal_user if int(uid) == 2 else None)
    app = create_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = "2"
        sess["_fresh"] = True
    resp = client.get("/admin/api/image-translate/prompts")
    assert resp.status_code == 403


def test_admin_get_and_post_prompts(authed_client_no_db, monkeypatch):
    client = authed_client_no_db
    store = {
        "image_translate.prompt_cover": "默认封面",
        "image_translate.prompt_detail": "默认详情",
    }
    # 模拟 image_translate_settings 内部 DB 调用
    from appcore import image_translate_settings as its
    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None
    def fake_execute(sql, params):
        store[params[0]] = params[1]
    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    # GET
    r = client.get("/admin/api/image-translate/prompts")
    assert r.status_code == 200
    data = r.get_json()
    assert data["cover"] == "默认封面"
    assert data["detail"] == "默认详情"

    # POST
    r2 = client.post(
        "/admin/api/image-translate/prompts",
        json={"preset": "cover", "value": "新的封面模板 {target_language_name}"},
    )
    assert r2.status_code == 200

    r3 = client.get("/admin/api/image-translate/prompts")
    assert r3.get_json()["cover"] == "新的封面模板 {target_language_name}"


def test_admin_post_rejects_invalid(authed_client_no_db, monkeypatch):
    client = authed_client_no_db
    from appcore import image_translate_settings as its
    monkeypatch.setattr(its, "execute", lambda s, p: None)
    r = client.post("/admin/api/image-translate/prompts",
                     json={"preset": "invalid", "value": "x"})
    assert r.status_code == 400
    r2 = client.post("/admin/api/image-translate/prompts",
                      json={"preset": "cover", "value": ""})
    assert r2.status_code == 400
