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


def test_admin_settings_empty_state_container(authed_client_no_db, monkeypatch):
    from web.routes import admin as r

    monkeypatch.setattr(r, "get_all_retention_settings", lambda: {})
    monkeypatch.setattr(r.medias, "list_languages_for_admin", lambda: [])
    resp = authed_client_no_db.get("/admin/settings")
    assert resp.status_code == 200
    assert 'id="imgTransPromptEmpty"' in resp.get_data(as_text=True)


def test_admin_settings_script_moves_voice_library_sync_to_top():
    from pathlib import Path

    script = Path("web/static/admin_settings.js").read_text(encoding="utf-8")

    assert "placeVoiceLibraryCardFirst" in script
    assert "stack.prepend(section)" in script


def test_admin_media_language_routes_forward_shopify_language_name(authed_client_no_db, monkeypatch):
    from web.routes import admin as r

    calls = []

    def fake_create_language(code, name_zh, sort_order, enabled, shopify_language_name):
        calls.append(("create", code, name_zh, sort_order, enabled, shopify_language_name))

    def fake_update_language(code, name_zh, sort_order, enabled, shopify_language_name):
        calls.append(("update", code, name_zh, sort_order, enabled, shopify_language_name))

    monkeypatch.setattr(r.medias, "create_language", fake_create_language)
    monkeypatch.setattr(r.medias, "update_language", fake_update_language)

    create_resp = authed_client_no_db.post(
        "/admin/api/media-languages",
        json={
            "code": "nl",
            "name_zh": "Dutch",
            "sort_order": 8,
            "enabled": True,
            "shopify_language_name": "Dutch",
        },
    )
    update_resp = authed_client_no_db.put(
        "/admin/api/media-languages/nl",
        json={
            "name_zh": "Dutch",
            "sort_order": 8,
            "enabled": True,
            "shopify_language_name": "Dutch",
        },
    )

    assert create_resp.status_code == 201
    assert update_resp.status_code == 200
    assert calls == [
        ("create", "nl", "Dutch", 8, True, "Dutch"),
        ("update", "nl", "Dutch", 8, True, "Dutch"),
    ]


def test_admin_settings_language_table_has_shopify_language_name_column():
    from pathlib import Path

    template = Path("web/templates/admin_settings.html").read_text(encoding="utf-8")
    script = Path("web/static/admin_settings.js").read_text(encoding="utf-8")

    assert "Shopify language name" in template
    assert "shopify_language_name" in script


def test_admin_settings_default_change_skips_per_type_adjust_for_default_types(
    authed_client_no_db, monkeypatch
):
    from web.routes import admin as r

    monkeypatch.setattr(
        r,
        "PROJECT_TYPE_LABELS",
        {"translation": "英文翻译", "de_translate": "德语翻译"},
    )

    store = {"retention_default_hours": "768"}

    def fake_get_retention_hours(project_type):
        if project_type == "__nonexistent__":
            return int(store["retention_default_hours"])
        return int(store.get(f"retention_{project_type}_hours") or store["retention_default_hours"])

    def fake_set_setting(key, value):
        store[key] = value

    def fake_db_execute(sql, args=()):
        if "DELETE FROM system_settings" in sql:
            store.pop(args[0], None)
        return 0

    per_type_calls = []
    default_calls = []

    monkeypatch.setattr(r, "get_retention_hours", fake_get_retention_hours)
    monkeypatch.setattr(r, "has_retention_override", lambda project_type: False)
    monkeypatch.setattr(r, "set_setting", fake_set_setting)
    monkeypatch.setattr(r, "adjust_expires_for_type", lambda *args: per_type_calls.append(args) or 0)
    monkeypatch.setattr(r, "adjust_expires_for_default", lambda *args, **kwargs: default_calls.append((args, kwargs)) or 0)
    monkeypatch.setattr("appcore.db.execute", fake_db_execute)

    resp = authed_client_no_db.post(
        "/admin/settings",
        data={
            "retention_default_days": "2",
            "retention_translation_days": "",
            "retention_de_translate_days": "",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert per_type_calls == []
    assert len(default_calls) == 1


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
