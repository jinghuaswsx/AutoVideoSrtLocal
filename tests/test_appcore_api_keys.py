import pytest
from appcore.users import create_user
from appcore.api_keys import get_all, get_key, set_key
from appcore.db import execute, query_one


@pytest.fixture
def user_id():
    uid = create_user("_test_keys_user_", "x")
    yield uid
    execute("DELETE FROM api_keys WHERE user_id = %s", (uid,))
    execute("DELETE FROM users WHERE id = %s", (uid,))


def test_set_and_get_key(user_id):
    set_key(user_id, "openrouter", "sk-abc123")
    assert get_key(user_id, "openrouter") == "sk-abc123"


def test_get_key_missing_returns_none(user_id):
    assert get_key(user_id, "elevenlabs") is None


def test_set_key_upsert(user_id):
    set_key(user_id, "elevenlabs", "old-key")
    set_key(user_id, "elevenlabs", "new-key")
    assert get_key(user_id, "elevenlabs") == "new-key"


def test_get_all_returns_all_services(user_id):
    set_key(user_id, "openrouter", "k1")
    set_key(user_id, "elevenlabs", "k2")
    result = get_all(user_id)
    assert result["openrouter"]["key_value"] == "k1"
    assert result["elevenlabs"]["key_value"] == "k2"


def test_set_key_with_extra_config(user_id):
    set_key(user_id, "doubao_asr", "tok", extra={"app_id": "123", "cluster": "prod"})
    row = query_one("SELECT extra_config FROM api_keys WHERE user_id=%s AND service='doubao_asr'", (user_id,))
    import json
    extra = json.loads(row["extra_config"]) if isinstance(row["extra_config"], str) else row["extra_config"]
    assert extra["app_id"] == "123"
