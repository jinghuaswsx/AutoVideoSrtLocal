def test_get_prompts_requires_admin(monkeypatch):
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


def test_admin_get_all_prompts(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its
    store = {}
    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None
    def fake_execute(sql, params):
        store[params[0]] = params[1]
    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    r = authed_client_no_db.get("/admin/api/image-translate/prompts")
    assert r.status_code == 200
    data = r.get_json()
    assert "languages" in data and "presets" in data
    assert set(data["languages"]) == set(its.SUPPORTED_LANGS)
    assert set(data["prompts"].keys()) == set(its.SUPPORTED_LANGS)
    for lang, prompts in data["prompts"].items():
        assert "cover" in prompts and "detail" in prompts


def test_admin_get_prompts_for_lang(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its
    store = {"image_translate.prompt_cover_de": "用户自定义德语封面"}
    def fake_query_one(sql, params):
        key = params[0]
        return {"value": store[key]} if key in store else None
    def fake_execute(sql, params):
        store[params[0]] = params[1]
    monkeypatch.setattr(its, "query_one", fake_query_one)
    monkeypatch.setattr(its, "execute", fake_execute)

    r = authed_client_no_db.get("/admin/api/image-translate/prompts?lang=de")
    assert r.status_code == 200
    data = r.get_json()
    assert data["cover"] == "用户自定义德语封面"
    assert "德语" in data["detail"]


def test_admin_get_rejects_invalid_lang(authed_client_no_db):
    r = authed_client_no_db.get("/admin/api/image-translate/prompts?lang=xx")
    assert r.status_code == 400


def test_admin_post_prompt(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its
    store = {}
    def fake_execute(sql, params):
        store[params[0]] = params[1]
    monkeypatch.setattr(its, "execute", fake_execute)
    r = authed_client_no_db.post(
        "/admin/api/image-translate/prompts",
        json={"preset": "cover", "lang": "fr", "value": "新的法语封面 prompt"},
    )
    assert r.status_code == 200
    assert store.get("image_translate.prompt_cover_fr") == "新的法语封面 prompt"


def test_admin_post_rejects_invalid(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its
    monkeypatch.setattr(its, "execute", lambda s, p: None)
    # invalid preset
    r = authed_client_no_db.post("/admin/api/image-translate/prompts",
                                  json={"preset": "invalid", "lang": "de", "value": "x"})
    assert r.status_code == 400
    # invalid lang
    r2 = authed_client_no_db.post("/admin/api/image-translate/prompts",
                                   json={"preset": "cover", "lang": "xx", "value": "x"})
    assert r2.status_code == 400
    # empty value
    r3 = authed_client_no_db.post("/admin/api/image-translate/prompts",
                                   json={"preset": "cover", "lang": "de", "value": ""})
    assert r3.status_code == 400
