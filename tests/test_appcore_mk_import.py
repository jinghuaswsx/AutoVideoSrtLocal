from appcore import mk_import


def test_normalize_strips_rjc_suffix():
    assert mk_import._normalize_product_code("ABC-DEF-RJC") == "abc-def"
    assert mk_import._normalize_product_code("abc-def-rjc") == "abc-def"


def test_normalize_no_suffix():
    assert mk_import._normalize_product_code("ABC-DEF") == "abc-def"


def test_normalize_mixed_case_rjc():
    assert mk_import._normalize_product_code("ABC-DEF-rjc") == "abc-def"
    assert mk_import._normalize_product_code("ABC-DEF-Rjc") == "abc-def"


def test_normalize_empty_returns_empty():
    assert mk_import._normalize_product_code("") == ""
    assert mk_import._normalize_product_code(None) == ""


def test_exception_classes_exist():
    assert issubclass(mk_import.DuplicateError, mk_import.MkImportError)
    assert issubclass(mk_import.DownloadError, mk_import.MkImportError)
    assert issubclass(mk_import.StorageError, mk_import.MkImportError)
    assert issubclass(mk_import.DBError, mk_import.MkImportError)


import pytest
from appcore.db import execute, query_one


@pytest.fixture
def db_test_user():
    from appcore.users import create_user, get_by_username
    username = "_t_mki_user"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="user")
    uid = get_by_username(username)["id"]
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))


@pytest.fixture
def db_test_product(db_test_user):
    # pre-cleanup in case a prior run left a stale row
    execute("DELETE FROM media_products WHERE product_code=%s", ("test-code",))
    pid = execute(
        "INSERT INTO media_products (user_id, name, product_code) VALUES (%s, %s, %s)",
        (db_test_user, "_t_mki_prod", "test-code"),
    )
    yield {"id": pid, "user_id": db_test_user}
    execute("DELETE FROM media_products WHERE id=%s", (pid,))


def test_find_existing_product_matches_normalized_code(db_test_product):
    from appcore import mk_import
    p = mk_import._find_existing_product("test-code")
    assert p is not None
    assert p["id"] == db_test_product["id"]


def test_find_existing_product_no_match(db_test_product):
    from appcore import mk_import
    p = mk_import._find_existing_product("xxx-not-found")
    assert p is None


def test_is_video_already_imported_yes_no(db_test_user, db_test_product):
    from appcore import mk_import
    execute(
        "INSERT INTO media_items (product_id, user_id, filename, object_key, lang) "
        "VALUES (%s, %s, %s, %s, %s)",
        (db_test_product["id"], db_test_user, "_t_mki.mp4", "k/_t_mki.mp4", "en"),
    )
    assert mk_import._is_video_already_imported("_t_mki.mp4") is True
    assert mk_import._is_video_already_imported("non-existent.mp4") is False
    execute("DELETE FROM media_items WHERE product_id=%s", (db_test_product["id"],))
