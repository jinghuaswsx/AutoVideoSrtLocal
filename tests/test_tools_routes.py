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
