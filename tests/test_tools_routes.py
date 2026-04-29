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
