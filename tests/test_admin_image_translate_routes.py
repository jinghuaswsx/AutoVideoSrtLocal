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


def test_admin_get_all_prompts_returns_dynamic_language_objects(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its

    languages = [
        {"code": "de", "name_zh": "德语"},
        {"code": "nl", "name_zh": "荷兰语"},
    ]
    prompts = {
        "de": {"cover": "cover-de", "detail": "detail-de"},
        "nl": {"cover": "cover-nl", "detail": "detail-nl"},
    }
    monkeypatch.setattr(its, "list_image_translate_languages", lambda: languages)
    monkeypatch.setattr(its, "list_all_prompts", lambda: prompts)

    resp = authed_client_no_db.get("/admin/api/image-translate/prompts")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["languages"] == languages
    assert all("code" in item and "name_zh" in item for item in data["languages"])
    assert data["prompts"] == prompts


def test_admin_get_prompts_accepts_dynamic_language(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its

    monkeypatch.setattr(its, "is_image_translate_language_supported", lambda code: code == "nl")
    monkeypatch.setattr(its, "get_prompts_for_lang", lambda code: {"cover": f"cover-{code}", "detail": f"detail-{code}"})

    resp = authed_client_no_db.get("/admin/api/image-translate/prompts?lang= NL ")
    assert resp.status_code == 200
    assert resp.get_json() == {"cover": "cover-nl", "detail": "detail-nl"}


def test_admin_get_rejects_unenabled_lang(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its

    monkeypatch.setattr(its, "is_image_translate_language_supported", lambda code: code == "nl")

    resp = authed_client_no_db.get("/admin/api/image-translate/prompts?lang=xx")
    assert resp.status_code == 400


def test_admin_post_prompt_accepts_dynamic_language(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its

    store = {}

    def fake_execute(sql, params):
        store[params[0]] = params[1]

    monkeypatch.setattr(its, "is_image_translate_language_supported", lambda code: code == "nl")
    monkeypatch.setattr(its, "list_image_translate_languages", lambda: [{"code": "nl", "name_zh": "荷兰语"}])
    monkeypatch.setattr(its, "execute", fake_execute)

    resp = authed_client_no_db.post(
        "/admin/api/image-translate/prompts",
        json={"preset": "cover", "lang": "nl", "value": "新的荷兰语封面 prompt"},
    )
    assert resp.status_code == 200
    assert store.get("image_translate.prompt_cover_nl") == "新的荷兰语封面 prompt"


def test_admin_post_rejects_invalid(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its

    monkeypatch.setattr(its, "is_image_translate_language_supported", lambda code: code == "nl")
    monkeypatch.setattr(its, "execute", lambda s, p: None)

    resp = authed_client_no_db.post(
        "/admin/api/image-translate/prompts",
        json={"preset": "invalid", "lang": "nl", "value": "x"},
    )
    assert resp.status_code == 400

    resp = authed_client_no_db.post(
        "/admin/api/image-translate/prompts",
        json={"preset": "cover", "lang": "xx", "value": "x"},
    )
    assert resp.status_code == 400

    resp = authed_client_no_db.post(
        "/admin/api/image-translate/prompts",
        json={"preset": "cover", "lang": "nl", "value": ""},
    )
    assert resp.status_code == 400
