import pytest
from appcore.users import create_user, get_by_username, get_by_id, list_users, set_active
from appcore.db import execute


@pytest.fixture(autouse=True)
def cleanup():
    execute("DELETE FROM users WHERE username LIKE '\\_test\\_%'")
    yield
    execute("DELETE FROM users WHERE username LIKE '\\_test\\_%'")


def test_create_and_get_by_username():
    create_user("_test_alice_", "secret123", role="user")
    u = get_by_username("_test_alice_")
    assert u is not None
    assert u["username"] == "_test_alice_"
    assert u["role"] == "user"
    assert u["is_active"] == 1


def test_password_hash_not_plaintext():
    create_user("_test_bob_", "mypassword")
    u = get_by_username("_test_bob_")
    assert u["password_hash"] != "mypassword"


def test_check_password():
    from appcore.users import check_password
    create_user("_test_carol_", "pass1")
    u = get_by_username("_test_carol_")
    assert check_password("pass1", u["password_hash"]) is True
    assert check_password("wrong", u["password_hash"]) is False


def test_get_by_id():
    create_user("_test_dan_", "x")
    u = get_by_username("_test_dan_")
    u2 = get_by_id(u["id"])
    assert u2["username"] == "_test_dan_"


def test_set_active():
    create_user("_test_eve_", "x")
    u = get_by_username("_test_eve_")
    set_active(u["id"], False)
    u2 = get_by_id(u["id"])
    assert u2["is_active"] == 0


def test_list_users_returns_list():
    result = list_users()
    assert isinstance(result, list)
