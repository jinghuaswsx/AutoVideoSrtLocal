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


def test_create_product_payload_uses_rjc_product_code_and_link():
    payload = mk_import._build_create_product_payload(
        {
            "product_name": "Demo",
            "product_code": "ABC-DEF",
            "product_link": "https://omurio.com/products/old-handle",
            "main_image": "https://img.example/a.jpg",
            "mk_id": 123,
        },
        translator_id=1,
    )

    assert payload["product_code"] == "abc-def-rjc"
    assert payload["product_link"] == "https://omurio.com/products/abc-def-rjc"


def test_product_link_precheck_blocks_unavailable_link(monkeypatch):
    monkeypatch.setattr(mk_import, "_probe_product_link", lambda url: (False, "HTTP 404"))

    with pytest.raises(mk_import.ProductLinkUnavailableError, match="HTTP 404"):
        mk_import._assert_product_link_available("https://newjoyloo.com/products/missing-rjc")


def test_import_mk_video_checks_product_link_before_download(monkeypatch):
    monkeypatch.setattr(mk_import, "_is_video_already_imported", lambda filename: False)
    monkeypatch.setattr(mk_import, "_find_existing_product", lambda normalized_code: None)
    monkeypatch.setattr(
        mk_import,
        "_assert_product_link_available",
        lambda link: (_ for _ in ()).throw(mk_import.ProductLinkUnavailableError(link, "HTTP 404")),
    )
    monkeypatch.setattr(
        mk_import,
        "_download_mp4",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("download should not start")),
    )
    monkeypatch.setattr(
        mk_import,
        "execute",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("product should not be inserted")),
    )

    with pytest.raises(mk_import.ProductLinkUnavailableError):
        mk_import.import_mk_video(
            mk_video_metadata={
                "mp4_url": "https://cdn.example/demo.mp4",
                "filename": "demo.mp4",
                "product_name": "Demo",
                "product_code": "ABC-DEF",
                "product_link": "https://newjoyloo.com/products/abc-def-rjc",
            },
            translator_id=1,
            actor_user_id=1,
        )


def test_exception_classes_exist():
    assert issubclass(mk_import.DuplicateError, mk_import.MkImportError)
    assert issubclass(mk_import.DownloadError, mk_import.MkImportError)
    assert issubclass(mk_import.StorageError, mk_import.MkImportError)
    assert issubclass(mk_import.DBError, mk_import.MkImportError)


def test_list_imported_filenames_queries_media_items(monkeypatch):
    captured = {}

    def fake_query_all(sql, args=None):
        captured["sql"] = sql
        captured["args"] = args
        return [{"filename": "a.mp4"}, {"filename": "a.mp4"}]

    monkeypatch.setattr(mk_import, "query_all", fake_query_all)

    assert mk_import.list_imported_filenames(["a.mp4", "b.mp4", "a.mp4"]) == {"a.mp4"}
    assert "FROM media_items" in captured["sql"]
    assert "deleted_at IS NULL" in captured["sql"]
    assert captured["args"] == ("a.mp4", "b.mp4", "a.mp4")


def test_list_imported_filenames_returns_empty_without_db_for_empty_input(monkeypatch):
    def fail_query_all(*args, **kwargs):
        raise AssertionError("empty filename list should not query db")

    monkeypatch.setattr(mk_import, "query_all", fail_query_all)

    assert mk_import.list_imported_filenames([]) == set()


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


def test_download_mp4_streams_to_path(tmp_path, monkeypatch):
    from appcore import mk_import

    class FakeResponse:
        status_code = 200
        def iter_content(self, chunk_size):
            yield b"abcdefghij"
            yield b"klmnop"
        def raise_for_status(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr("requests.get", lambda url, stream, timeout: FakeResponse())

    dest = tmp_path / "out.mp4"
    n = mk_import._download_mp4("http://fake/x.mp4", str(dest))
    assert n == 16
    assert dest.read_bytes() == b"abcdefghijklmnop"


def test_download_mp4_404_raises(tmp_path, monkeypatch):
    from appcore import mk_import
    import requests

    class FakeResponse:
        status_code = 404
        def iter_content(self, chunk_size): return []
        def raise_for_status(self):
            raise requests.HTTPError("404 Not Found")
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr("requests.get", lambda url, stream, timeout: FakeResponse())

    with pytest.raises(mk_import.DownloadError, match="404"):
        mk_import._download_mp4("http://fake/x.mp4", str(tmp_path / "x.mp4"))


def test_import_mk_video_new_product(db_test_user, monkeypatch, tmp_path):
    from appcore import mk_import

    def fake_download_mp4(url, path, **kw):
        with open(path, "wb") as f:
            f.write(b"x" * 100)
        return 100

    monkeypatch.setattr(mk_import, "_download_mp4", fake_download_mp4)
    monkeypatch.setattr(mk_import, "_download_cover", lambda url, path, **kw: None)

    meta = {
        "mp4_url": "http://fake/_t_mki_new.mp4",
        "filename": "_t_mki_new.mp4",
        "duration_seconds": 30,
        "cover_url": None,
        "product_name": "_t_mki_NewProd",
        "product_link": "https://fake.shop/p/x",
        "main_image": None,
        "product_code": "TEST-NEWMK-RJC",
        "mk_id": 99999,
    }
    result = mk_import.import_mk_video(
        mk_video_metadata=meta,
        translator_id=db_test_user,
        actor_user_id=db_test_user,
    )
    assert result["is_new_product"] is True
    assert result["media_item_id"] > 0
    assert result["media_product_id"] > 0
    pid = result["media_product_id"]
    iid = result["media_item_id"]
    execute("DELETE FROM media_items WHERE id=%s", (iid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))


def test_import_mk_video_old_product_ignores_translator(db_test_user, db_test_product, monkeypatch):
    from appcore import mk_import

    def fake_download_mp4(url, path, **kw):
        with open(path, "wb") as f:
            f.write(b"x" * 100)
        return 100
    monkeypatch.setattr(mk_import, "_download_mp4", fake_download_mp4)
    monkeypatch.setattr(mk_import, "_download_cover", lambda url, path, **kw: None)

    other_uid = db_test_user + 999
    meta = {
        "mp4_url": "http://fake/_t_mki_old.mp4",
        "filename": "_t_mki_old.mp4",
        "duration_seconds": 30, "cover_url": None,
        "product_name": "ignored", "product_link": None, "main_image": None,
        "product_code": "TEST-CODE-RJC", "mk_id": None,
    }
    result = mk_import.import_mk_video(
        mk_video_metadata=meta, translator_id=other_uid,
        actor_user_id=db_test_user,
    )
    assert result["is_new_product"] is False
    assert result["media_product_id"] == db_test_product["id"]
    p = query_one("SELECT user_id FROM media_products WHERE id=%s", (db_test_product["id"],))
    assert p["user_id"] == db_test_user

    iid = result["media_item_id"]
    execute("DELETE FROM media_items WHERE id=%s", (iid,))


def test_import_mk_video_dedupes_by_filename(db_test_user, db_test_product, monkeypatch):
    from appcore import mk_import

    execute(
        "INSERT INTO media_items (product_id, user_id, filename, object_key, lang) "
        "VALUES (%s, %s, %s, %s, %s)",
        (db_test_product["id"], db_test_user, "_t_mki_dup.mp4", "k/dup.mp4", "en"),
    )

    meta = {
        "mp4_url": "http://fake/_t_mki_dup.mp4", "filename": "_t_mki_dup.mp4",
        "duration_seconds": 30, "cover_url": None,
        "product_name": "x", "product_link": None, "main_image": None,
        "product_code": "test-code", "mk_id": None,
    }
    with pytest.raises(mk_import.DuplicateError):
        mk_import.import_mk_video(
            mk_video_metadata=meta, translator_id=db_test_user, actor_user_id=db_test_user,
        )

    execute("DELETE FROM media_items WHERE product_id=%s AND filename='_t_mki_dup.mp4'", (db_test_product["id"],))
