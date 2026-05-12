from __future__ import annotations


def test_update_password_hashes_and_persists_new_password(monkeypatch):
    from appcore import users

    calls = []

    monkeypatch.setattr(users, "hash_password", lambda password: f"hashed:{password}")
    monkeypatch.setattr(users, "execute", lambda sql, args=(): calls.append((sql, args)))

    users.update_password(42, "new-secret")

    assert calls == [
        (
            "UPDATE users SET password_hash = %s WHERE id = %s",
            ("hashed:new-secret", 42),
        )
    ]
