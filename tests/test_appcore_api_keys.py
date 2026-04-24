import json

import pytest

from appcore.api_keys import get_all, get_key, resolve_extra, resolve_key, set_key


@pytest.fixture
def fake_api_key_db(monkeypatch):
    users = {
        1: {"id": 1, "username": "admin", "is_active": 1},
        2: {"id": 2, "username": "alice", "is_active": 1},
    }
    rows: dict[tuple[int, str], dict] = {}

    def fake_query_one(sql, params=()):
        if "FROM users WHERE username = %s" in sql:
            username = params[0]
            return next((row for row in users.values() if row["username"] == username), None)
        if "FROM users WHERE id = %s" in sql:
            return users.get(int(params[0]))
        if "SELECT key_value FROM api_keys" in sql:
            row = rows.get((int(params[0]), params[1]))
            return {"key_value": row["key_value"]} if row else None
        if "SELECT extra_config FROM api_keys" in sql:
            row = rows.get((int(params[0]), params[1]))
            return {"extra_config": row["extra_config"]} if row else None
        return None

    def fake_query(sql, params=()):
        if "FROM api_keys WHERE user_id = %s" in sql:
            uid = int(params[0])
            return [
                {
                    "service": service,
                    "key_value": row["key_value"],
                    "extra_config": row["extra_config"],
                }
                for (row_uid, service), row in rows.items()
                if row_uid == uid
            ]
        return []

    def fake_execute(sql, params=()):
        uid, service, key_value, extra_json = params
        rows[(int(uid), service)] = {
            "key_value": key_value,
            "extra_config": extra_json,
        }
        return 1

    monkeypatch.setattr("appcore.api_keys.query_one", fake_query_one)
    monkeypatch.setattr("appcore.api_keys.query", fake_query)
    monkeypatch.setattr("appcore.api_keys.execute", fake_execute)
    return rows


def test_admin_can_set_and_all_users_read_admin_key(fake_api_key_db):
    set_key(1, "openrouter", "admin-key")
    fake_api_key_db[(2, "openrouter")] = {"key_value": "alice-key", "extra_config": None}

    assert get_key(2, "openrouter") == "admin-key"


def test_resolve_key_uses_admin_key_before_env(fake_api_key_db, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    set_key(1, "openrouter", "admin-key")

    assert resolve_key(2, "openrouter", "OPENROUTER_API_KEY") == "admin-key"


def test_resolve_key_falls_back_to_env_when_admin_missing(fake_api_key_db, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-eleven")

    assert resolve_key(2, "elevenlabs", "ELEVENLABS_API_KEY") == "env-eleven"


def test_get_all_returns_admin_services_for_any_user(fake_api_key_db):
    set_key(1, "openrouter", "k1")
    set_key(1, "elevenlabs", "k2")
    fake_api_key_db[(2, "openrouter")] = {"key_value": "ignored", "extra_config": None}

    result = get_all(2)

    assert result["openrouter"]["key_value"] == "k1"
    assert result["elevenlabs"]["key_value"] == "k2"


def test_admin_set_key_with_extra_config(fake_api_key_db):
    set_key(1, "doubao_asr", "tok", extra={"app_id": "123", "cluster": "prod"})

    row = fake_api_key_db[(1, "doubao_asr")]
    extra = json.loads(row["extra_config"])
    assert extra["app_id"] == "123"


def test_non_admin_cannot_set_api_config(fake_api_key_db):
    with pytest.raises(PermissionError):
        set_key(2, "openrouter", "alice-key")


def test_resolve_extra_reads_admin_extra(fake_api_key_db):
    set_key(1, "doubao_llm", "tok", extra={"base_url": "https://ark.example"})
    fake_api_key_db[(2, "doubao_llm")] = {
        "key_value": "ignored",
        "extra_config": json.dumps({"base_url": "https://alice.example"}),
    }

    assert resolve_extra(2, "doubao_llm") == {"base_url": "https://ark.example"}


def test_jianying_remains_user_scoped(fake_api_key_db):
    set_key(1, "jianying", "", extra={"project_root": r"C:\AdminDrafts"})
    set_key(2, "jianying", "", extra={"project_root": r"D:\AliceDrafts"})

    assert resolve_extra(2, "jianying") == {"project_root": r"D:\AliceDrafts"}
