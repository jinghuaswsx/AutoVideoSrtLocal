from __future__ import annotations


def test_list_translators_filters_active_users_with_translate_permission(monkeypatch):
    from appcore import users

    calls = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        return [
            {"id": 1, "username": "alice", "role": "user", "permissions": '{"can_translate": true}'},
            {"id": 2, "username": "bob", "role": "admin", "permissions": {"can_translate": True}},
            {"id": 3, "username": "carol", "role": "user", "permissions": '{"can_translate": false}'},
            {"id": 4, "username": "dan", "role": "user", "permissions": ""},
            {"id": 5, "username": "admin", "role": "superadmin", "permissions": '{"can_translate": true}'},
        ]

    monkeypatch.setattr(users, "query", fake_query)

    assert users.list_translators() == [
        {"id": 1, "username": "alice"},
        {"id": 2, "username": "bob"},
    ]
    assert calls == [
        (
            "SELECT id, username, role, permissions FROM users WHERE is_active=1 AND role <> %s ORDER BY username ASC",
            ("superadmin",),
        )
    ]


def test_list_translation_work_users_requires_translate_and_work_scope(monkeypatch):
    from appcore import users

    def fake_query(sql, args=()):
        return [
            {
                "id": 1,
                "username": "zhou",
                "display_name": "周干琴",
                "role": "user",
                "permissions": '{"can_translate": true, "work_scope_translation": true}',
            },
            {
                "id": 2,
                "username": "translate-only",
                "display_name": "翻译但非范围",
                "role": "user",
                "permissions": '{"can_translate": true, "work_scope_translation": false}',
            },
            {
                "id": 3,
                "username": "scope-only",
                "display_name": "范围但非翻译",
                "role": "user",
                "permissions": '{"can_translate": false, "work_scope_translation": true}',
            },
        ]

    monkeypatch.setattr(users, "_user_display_name_expr", lambda: "username", raising=False)
    monkeypatch.setattr(users, "query", fake_query)

    assert users.list_translation_work_users() == [
        {"id": 1, "username": "zhou", "display_name": "周干琴"},
    ]


def test_ensure_translation_work_user_accepts_valid_user(monkeypatch):
    from appcore import users

    monkeypatch.setattr(users, "_user_display_name_expr", lambda: "username", raising=False)
    monkeypatch.setattr(
        users,
        "query_one",
        lambda sql, args: {
            "id": 5,
            "username": "worker",
            "display_name": "顾倩",
            "role": "user",
            "permissions": '{"can_translate": true, "work_scope_translation": true}',
            "is_active": 1,
        },
    )

    assert users.ensure_translation_work_user(5)["username"] == "worker"


def test_ensure_translation_work_user_rejects_missing_scope(monkeypatch):
    from appcore import users

    monkeypatch.setattr(users, "_user_display_name_expr", lambda: "username", raising=False)
    monkeypatch.setattr(
        users,
        "query_one",
        lambda sql, args: {
            "id": 5,
            "username": "worker",
            "display_name": "顾倩",
            "role": "user",
            "permissions": '{"can_translate": true, "work_scope_translation": false}',
            "is_active": 1,
        },
    )

    try:
        users.ensure_translation_work_user(5)
    except ValueError as exc:
        assert "翻译工作范围" in str(exc)
    else:
        raise AssertionError("expected ValueError")
