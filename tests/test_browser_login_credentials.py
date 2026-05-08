from __future__ import annotations


def test_mask_username_keeps_tail_without_exposing_full_value():
    from appcore import browser_login_credentials as creds

    assert creds.mask_username("acct000000001025") == "acct********1025"
    assert creds.mask_username("abc") == "***"
    assert creds.mask_username("") == ""


def test_get_credential_reads_enabled_plaintext_row(monkeypatch):
    from appcore import browser_login_credentials as creds

    captured = {}

    def fake_query_one(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return {
            "id": 7,
            "env_code": "DXM01-Meta",
            "provider": "facebook",
            "username": "acct000000001025",
            "password": "plain-password",
            "enabled": 1,
            "last_login_status": "failed",
            "last_error": "login_required",
        }

    monkeypatch.setattr(creds, "query_one", fake_query_one)

    credential = creds.get_credential("DXM01-Meta", "facebook")

    assert credential is not None
    assert credential.username == "acct000000001025"
    assert credential.password == "plain-password"
    assert "enabled=1" in captured["sql"].replace(" ", "")
    assert captured["args"] == ("DXM01-Meta", "facebook")


def test_save_credential_writes_plaintext_password(monkeypatch):
    from appcore import browser_login_credentials as creds

    executed = []
    monkeypatch.setattr(creds, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    creds.save_credential(
        "DXM01-Meta",
        "facebook",
        username="acct000000001025",
        password="plain-password",
        enabled=True,
        updated_by=1,
    )

    sql, args = executed[0]
    assert "browser_login_credentials" in sql
    assert "plain-password" in args
    assert args[:5] == ("DXM01-Meta", "facebook", "acct000000001025", "plain-password", 1)


def test_mark_login_result_updates_status_without_secret(monkeypatch):
    from appcore import browser_login_credentials as creds

    executed = []
    monkeypatch.setattr(creds, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    creds.mark_login_result("DXM01-Meta", "facebook", "needs_human", "checkpoint_required")

    sql, args = executed[0]
    assert "last_login_status" in sql
    assert args == ("needs_human", "checkpoint_required", "DXM01-Meta", "facebook")
