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
