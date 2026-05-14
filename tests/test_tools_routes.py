import re


def test_tools_page_is_available_to_normal_users(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/tools/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "小工具" in body
    assert "平均运费" in body


def test_tools_menu_entry_is_visible_to_normal_users(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/tools/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'href="/tools/"' in body
    assert "小工具" in body
    assert "平均运费" in body


def test_drawing_studio_menu_entry_is_visible_to_normal_users(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/tools/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "画图工作室" in body
    assert 'href="/drawing-studio/sso"' in body
    assert '<span class="nav-icon">🎨</span> 画图工作室' in body


def test_drawing_studio_menu_entry_ignores_legacy_false_permission(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)

    fake_user = {
        "id": 4,
        "username": "legacy-user",
        "role": "user",
        "is_active": 1,
        "permissions": {"drawing_studio": False},
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 4 else None)

    from web.app import create_app

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "4"
        session["_fresh"] = True

    response = client.get("/tools/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "画图工作室" in body
    assert 'href="/drawing-studio/sso"' in body


def test_sidebar_nav_icons_use_fixed_alignment_column(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/tools/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    match = re.search(r"\.sidebar-nav \.nav-icon\s*\{(?P<rules>[^}]*)\}", body)

    assert match
    rules = match.group("rules")
    assert "display: inline-flex" in rules
    assert "width: 20px" in rules
    assert "justify-content: center" in rules
