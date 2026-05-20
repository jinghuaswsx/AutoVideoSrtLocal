def test_list_users_includes_optional_xingming_column(monkeypatch):
    from appcore import users

    captured = {}
    monkeypatch.setattr(users, "_user_column_exists", lambda column: column == "xingming")
    monkeypatch.setattr(users, "query", lambda sql, args=(): captured.setdefault("sql", sql) or [])

    users.list_users()

    assert "xingming" in captured["sql"]
    assert "password_hash" not in captured["sql"]


def test_update_user_profile_updates_editable_fields_and_resets_role_permissions(monkeypatch):
    from appcore import users

    calls = []
    monkeypatch.setattr(
        users,
        "get_by_id",
        lambda user_id: {
            "id": user_id,
            "username": "worker",
            "role": "user",
            "is_active": 1,
            "permissions": '{"can_translate": true, "work_scope_translation": false}',
        },
    )
    monkeypatch.setattr(users, "get_by_username", lambda username: None)
    monkeypatch.setattr(users, "_user_column_exists", lambda column: column == "xingming")
    monkeypatch.setattr(users, "execute", lambda sql, args=(): calls.append((sql, args)))

    users.update_user_profile(
        9,
        username="worker-updated",
        role="admin",
        is_active=False,
        xingming="王同学",
        work_scopes=["translation"],
    )

    sql, args = calls[0]
    assert "password_hash" not in sql
    assert "username = %s" in sql
    assert "role = %s" in sql
    assert "permissions = %s" in sql
    assert "is_active = %s" in sql
    assert "xingming = %s" in sql
    assert args[0:3] == ("worker-updated", "admin", 0)
    assert '"work_scope_translation": true' in args[3]
    assert args[-2:] == ("王同学", 9)


def test_update_user_profile_can_update_work_scope_without_role_change(monkeypatch):
    from appcore import users

    calls = []
    monkeypatch.setattr(
        users,
        "get_by_id",
        lambda user_id: {
            "id": user_id,
            "username": "worker",
            "role": "user",
            "is_active": 1,
            "permissions": '{"can_translate": true, "work_scope_translation": false}',
        },
    )
    monkeypatch.setattr(users, "get_by_username", lambda username: {"id": 9, "username": username})
    monkeypatch.setattr(users, "_user_column_exists", lambda column: False)
    monkeypatch.setattr(users, "execute", lambda sql, args=(): calls.append((sql, args)))

    users.update_user_profile(
        9,
        username="worker",
        role="user",
        is_active=True,
        work_scopes=["translation"],
    )

    sql, args = calls[0]
    assert "permissions = %s" in sql
    assert '"can_translate": true' in args[3]
    assert '"work_scope_translation": true' in args[3]


def test_update_user_profile_rejects_username_owned_by_another_user(monkeypatch):
    from appcore import users

    monkeypatch.setattr(
        users,
        "get_by_id",
        lambda user_id: {
            "id": user_id,
            "username": "worker",
            "role": "user",
            "is_active": 1,
        },
    )
    monkeypatch.setattr(users, "get_by_username", lambda username: {"id": 10, "username": username})

    try:
        users.update_user_profile(9, username="taken", role="user", is_active=True)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate username should be rejected")
