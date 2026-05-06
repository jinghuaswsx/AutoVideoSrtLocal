from __future__ import annotations


def test_list_translators_filters_active_users_with_translate_permission(monkeypatch):
    from appcore import users

    calls = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        return [
            {"id": 1, "username": "alice", "permissions": '{"can_translate": true}'},
            {"id": 2, "username": "bob", "permissions": {"can_translate": True}},
            {"id": 3, "username": "carol", "permissions": '{"can_translate": false}'},
            {"id": 4, "username": "dan", "permissions": ""},
        ]

    monkeypatch.setattr(users, "query", fake_query)

    assert users.list_translators() == [
        {"id": 1, "username": "alice"},
        {"id": 2, "username": "bob"},
    ]
    assert calls == [
        (
            "SELECT id, username, permissions FROM users WHERE is_active=1 ORDER BY username ASC",
            (),
        )
    ]
