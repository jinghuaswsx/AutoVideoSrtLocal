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


def test_import_mk_video_warns_when_product_link_probe_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(mk_import, "_is_video_already_imported", lambda filename: False)
    monkeypatch.setattr(mk_import, "_find_existing_product", lambda normalized_code: None)
    monkeypatch.setattr(mk_import, "execute", lambda *args, **kwargs: 123)
    monkeypatch.setattr(mk_import, "_download_cover", lambda *args, **kwargs: None)
    monkeypatch.setattr(mk_import, "_medias_create_item", lambda **kwargs: 456)
    monkeypatch.setattr(mk_import, "_probe_product_link", lambda url: (False, "HTTP 404"), raising=False)

    def fake_download_mp4(url, path, **kwargs):
        with open(path, "wb") as f:
            f.write(b"video")
        return 5

    monkeypatch.setattr(mk_import, "_download_mp4", fake_download_mp4)

    result = mk_import.import_mk_video(
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

    assert result["media_product_id"] == 123
    assert result["media_item_id"] == 456
    assert result["is_new_product"] is True
    assert result["warnings"] == [{
        "type": "product_link_unavailable",
        "message": "商品链接可能不可访问",
        "url": "https://newjoyloo.com/products/abc-def-rjc",
        "detail": "HTTP 404",
    }]


def test_import_mk_video_returns_grouped_step_results(monkeypatch, tmp_path):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(mk_import, "_is_video_already_imported", lambda filename: False)
    monkeypatch.setattr(
        mk_import,
        "_find_existing_product",
        lambda normalized_code: {
            "id": 587,
            "user_id": 42,
            "product_code": "demo-rjc",
            "product_link": "https://newjoyloo.com/products/demo-rjc",
        },
    )
    monkeypatch.setattr(mk_import, "_probe_product_link", lambda url: (False, "HTTP 404"), raising=False)

    def fake_download_mp4(url, path, **kwargs):
        with open(path, "wb") as f:
            f.write(b"video")
        return 5

    monkeypatch.setattr(mk_import, "_download_mp4", fake_download_mp4)
    monkeypatch.setattr(
        mk_import.object_keys,
        "build_media_object_key",
        lambda user_id, product_id, filename: f"{user_id}/medias/{product_id}/{filename}",
    )
    monkeypatch.setattr(mk_import, "_write_file_to_media_store", lambda path, object_key: 5, raising=False)
    monkeypatch.setattr(mk_import, "_medias_create_item", lambda **kwargs: 456)

    result = mk_import.import_mk_video(
        mk_video_metadata={
            "mp4_url": "https://cdn.example/demo.mp4",
            "filename": "demo.mp4",
            "product_name": "Demo",
            "product_code": "DEMO",
            "product_link": "https://newjoyloo.com/products/demo-rjc",
        },
        translator_id=None,
        actor_user_id=1,
    )

    step_results = result["step_results"]
    assert [row["key"] for row in step_results["product"]] == [
        "product_lookup",
        "product_link_probe",
    ]
    assert step_results["product"][0]["status"] == "done"
    assert "复用已有产品" in step_results["product"][0]["message"]
    assert step_results["product"][1]["status"] == "warning"
    assert step_results["product"][1]["logs"] == ["HTTP 404"]
    assert step_results["download"][0]["key"] == "download_mp4"
    assert step_results["download"][0]["status"] == "done"
    assert step_results["store"][0]["key"] == "store_media"
    assert step_results["store"][1]["key"] == "media_item"
    assert step_results["store"][1]["message"] == "素材 ID：456"


def test_import_mk_video_keeps_original_filename_as_display_name(monkeypatch):
    original_filename = "2026.04.09-物理综合实验DIY-混剪-苏齐齐.mp4"
    captured = {}

    monkeypatch.setattr(mk_import, "_is_video_already_imported", lambda filename: False)
    monkeypatch.setattr(
        mk_import,
        "_find_existing_product",
        lambda normalized_code: {
            "id": 587,
            "user_id": 1,
            "product_code": "tool-free-robotics-building-set-rjc",
            "product_link": "https://newjoyloo.com/products/tool-free-robotics-building-set-rjc",
        },
    )
    monkeypatch.setattr(mk_import, "_probe_product_link", lambda url: (True, None), raising=False)

    def fake_download_mp4(url, path, **kwargs):
        with open(path, "wb") as f:
            f.write(b"video")
        return 5

    def fake_create_item(**kwargs):
        captured["created_item"] = kwargs
        return 456

    monkeypatch.setattr(mk_import, "_download_mp4", fake_download_mp4)
    monkeypatch.setattr(
        mk_import.object_keys,
        "build_media_object_key",
        lambda user_id, product_id, filename: f"{user_id}/medias/{product_id}/{filename}",
    )
    monkeypatch.setattr(mk_import, "_write_file_to_media_store", lambda path, object_key: 5, raising=False)
    monkeypatch.setattr(mk_import, "_medias_create_item", fake_create_item)

    result = mk_import.import_mk_video(
        mk_video_metadata={
            "mp4_url": "https://cdn.example/original.mp4",
            "filename": original_filename,
            "product_name": "科学小实验手工玩具",
            "product_code": "tool-free-robotics-building-set",
            "product_link": "https://newjoyloo.com/products/tool-free-robotics-building-set-rjc",
        },
        translator_id=1,
        actor_user_id=1,
    )

    assert result["is_new_product"] is False
    assert captured["created_item"]["filename"] == original_filename
    assert captured["created_item"]["display_name"] == original_filename


def test_import_mk_video_binds_mk_material_with_product_link_metadata(monkeypatch):
    captured = {}

    monkeypatch.setattr(mk_import, "_is_video_already_imported", lambda filename: False)
    monkeypatch.setattr(
        mk_import,
        "_find_existing_product",
        lambda normalized_code: {
            "id": 587,
            "user_id": 1,
            "product_code": "tool-free-robotics-building-set-rjc",
            "product_link": "https://newjoyloo.com/products/tool-free-robotics-building-set-rjc",
        },
    )
    monkeypatch.setattr(mk_import, "_probe_product_link", lambda url: (True, None), raising=False)

    def fake_download_mp4(url, path, **kwargs):
        with open(path, "wb") as f:
            f.write(b"video")
        return 5

    monkeypatch.setattr(mk_import, "_download_mp4", fake_download_mp4)
    monkeypatch.setattr(
        mk_import.object_keys,
        "build_media_object_key",
        lambda user_id, product_id, filename: f"{user_id}/medias/{product_id}/{filename}",
    )
    monkeypatch.setattr(mk_import, "_write_file_to_media_store", lambda path, object_key: 5, raising=False)
    monkeypatch.setattr(mk_import, "_import_image_object", lambda **kwargs: None, raising=False)
    monkeypatch.setattr(mk_import, "_medias_create_item", lambda **kwargs: 456)
    monkeypatch.setattr(
        mk_import,
        "_bind_imported_mk_material",
        lambda **kwargs: captured.__setitem__("binding", kwargs),
        raising=False,
    )

    result = mk_import.import_mk_video(
        mk_video_metadata={
            "mp4_url": "/xuanpin/api/mk-video?path=uploads2%2Fdemo.mp4",
            "filename": "demo.mp4",
            "cover_path": "uploads2/demo.jpg",
            "product_name": "科学小实验手工玩具",
            "product_code": "tool-free-robotics-building-set",
            "product_link": "https://waregami.com/products/tool-free-robotics-building-set-rjc",
            "mk_id": 3528,
        },
        translator_id=1,
        actor_user_id=9,
    )

    assert result["media_item_id"] == 456
    assert captured["binding"]["media_item_id"] == 456
    assert captured["binding"]["mk_product_id"] == 3528
    assert captured["binding"]["mk_video_path"] == "uploads2/demo.mp4"
    assert captured["binding"]["mk_video_image_path"] == "uploads2/demo.jpg"
    assert captured["binding"]["bound_by"] == 9
    assert captured["binding"]["mk_video_metadata"]["product_link"] == (
        "https://waregami.com/products/tool-free-robotics-building-set-rjc"
    )
    assert captured["binding"]["mk_video_metadata"]["product_links"] == [
        "https://waregami.com/products/tool-free-robotics-building-set-rjc"
    ]


def test_import_mk_video_old_product_allows_missing_product_owner(monkeypatch):
    captured = {}

    monkeypatch.setattr(mk_import, "_is_video_already_imported", lambda filename: False)
    monkeypatch.setattr(
        mk_import,
        "_find_existing_product",
        lambda normalized_code: {
            "id": 587,
            "user_id": 42,
            "product_code": "tool-free-robotics-building-set-rjc",
            "product_link": "https://newjoyloo.com/products/tool-free-robotics-building-set-rjc",
        },
    )
    monkeypatch.setattr(mk_import, "_probe_product_link", lambda url: (True, None), raising=False)

    def fake_download_mp4(url, path, **kwargs):
        with open(path, "wb") as f:
            f.write(b"video")
        return 5

    monkeypatch.setattr(mk_import, "_download_mp4", fake_download_mp4)
    monkeypatch.setattr(
        mk_import.object_keys,
        "build_media_object_key",
        lambda user_id, product_id, filename: f"{user_id}/medias/{product_id}/{filename}",
    )
    monkeypatch.setattr(mk_import, "_write_file_to_media_store", lambda path, object_key: 5, raising=False)
    monkeypatch.setattr(mk_import, "_medias_create_item", lambda **kwargs: captured.update(kwargs) or 456)

    result = mk_import.import_mk_video(
        mk_video_metadata={
            "mp4_url": "https://cdn.example/original.mp4",
            "filename": "old-product-video.mp4",
            "product_name": "科学小实验手工玩具",
            "product_code": "tool-free-robotics-building-set",
            "product_link": "https://newjoyloo.com/products/tool-free-robotics-building-set-rjc",
        },
        translator_id=None,
        actor_user_id=1,
    )

    assert result["is_new_product"] is False
    assert captured["user_id"] == 42
    assert captured["object_key"].startswith("42/medias/587/")


def test_import_mk_video_reuses_existing_product_after_product_code_duplicate_race(monkeypatch):
    captured = {}
    product_code = "tool-free-robotics-building-set-rjc"

    monkeypatch.setattr(mk_import, "_is_video_already_imported", lambda filename: False)
    monkeypatch.setattr(mk_import, "_find_existing_product", lambda normalized_code: None)
    monkeypatch.setattr(mk_import, "_probe_product_link", lambda url: (True, None), raising=False)
    monkeypatch.setattr(mk_import, "_find_product_asset", lambda normalized_code: None, raising=False)
    monkeypatch.setattr(mk_import, "_fetch_mk_product_detail", lambda mk_id: {}, raising=False)

    def fake_execute(sql, args=None):
        if "INSERT INTO media_products" in sql:
            raise Exception(
                "(1062, \"Duplicate entry 'tool-free-robotics-building-set-rjc' "
                "for key 'media_products.uk_media_products_product_code'\")"
            )
        raise AssertionError(f"unexpected execute: {sql}")

    def fake_query_one(sql, args=None):
        if "FROM media_products" in sql:
            assert product_code in args
            return {
                "id": 587,
                "user_id": 42,
                "product_code": product_code,
                "product_link": f"https://newjoyloo.com/products/{product_code}",
            }
        raise AssertionError(f"unexpected query_one: {sql}")

    def fake_download_mp4(url, path, **kwargs):
        with open(path, "wb") as f:
            f.write(b"video")
        return 5

    def fake_create_item(**kwargs):
        captured["created_item"] = kwargs
        return 456

    monkeypatch.setattr(mk_import, "execute", fake_execute)
    monkeypatch.setattr(mk_import, "query_one", fake_query_one)
    monkeypatch.setattr(mk_import, "_download_mp4", fake_download_mp4)
    monkeypatch.setattr(
        mk_import.object_keys,
        "build_media_object_key",
        lambda user_id, product_id, filename: f"{user_id}/medias/{product_id}/{filename}",
    )
    monkeypatch.setattr(mk_import, "_write_file_to_media_store", lambda path, object_key: 5, raising=False)
    monkeypatch.setattr(mk_import, "_medias_create_item", fake_create_item)

    result = mk_import.import_mk_video(
        mk_video_metadata={
            "mp4_url": "https://cdn.example/original.mp4",
            "filename": "2026.04.09-物理综合实验DIY-混剪-苏齐齐.mp4",
            "product_name": "科学小实验手工玩具",
            "product_code": "tool-free-robotics-building-set",
            "product_link": f"https://newjoyloo.com/products/{product_code}",
        },
        translator_id=99,
        actor_user_id=1,
    )

    assert result["is_new_product"] is False
    assert result["media_product_id"] == 587
    assert captured["created_item"]["product_id"] == 587
    assert captured["created_item"]["user_id"] == 42
    assert captured["created_item"]["object_key"].startswith("42/medias/587/")


def test_local_media_object_key_from_url_extracts_object_keys():
    assert (
        mk_import._local_media_object_key_from_url(
            "/medias/object?object_key=artifacts%2Fmk%2Fcover.png"
        )
        == "artifacts/mk/cover.png"
    )
    assert (
        mk_import._local_media_object_key_from_url(
            "/medias/obj/1/medias/23/demo%20video.mp4"
        )
        == "1/medias/23/demo video.mp4"
    )


def test_import_mk_video_new_product_enriches_assets_copy_and_media_store(monkeypatch, tmp_path):
    captured = {"created_item": None, "product_cover": None, "copy": None, "copied": [], "stored": []}

    monkeypatch.setattr(mk_import, "_is_video_already_imported", lambda filename: False)
    monkeypatch.setattr(mk_import, "_find_existing_product", lambda normalized_code: None)
    monkeypatch.setattr(mk_import, "_probe_product_link", lambda url: (True, None), raising=False)
    monkeypatch.setattr(
        mk_import,
        "_find_product_asset",
        lambda normalized_code: {
            "product_main_image_url": "https://cdn.example/main.jpg",
            "product_main_image_object_key": "xuanpin/product-main-images/demo/main.jpg",
            "product_cn_name": "科学小实验手工玩具",
            "product_url": "https://shop.example/products/demo",
        },
        raising=False,
    )
    monkeypatch.setattr(
        mk_import,
        "_fetch_mk_product_detail",
        lambda mk_id: {
            "id": mk_id,
            "texts": [{
                "title": "Screen-Free Weekend",
                "message": "Busy kids, better learning.",
                "description": "Get Yours Today",
            }],
        },
        raising=False,
    )

    def fake_execute(sql, args=None):
        if "INSERT INTO media_products" in sql:
            captured["product_insert_args"] = args
            return 123
        raise AssertionError(f"unexpected execute: {sql}")

    monkeypatch.setattr(mk_import, "execute", fake_execute)

    def fake_download_mp4(url, path, **kwargs):
        with open(path, "wb") as f:
            f.write(b"video")
        return 5

    monkeypatch.setattr(mk_import, "_download_mp4", fake_download_mp4)
    monkeypatch.setattr(
        mk_import.object_keys,
        "build_media_object_key",
        lambda user_id, product_id, filename: f"{user_id}/medias/{product_id}/{filename}",
    )
    monkeypatch.setattr(
        mk_import,
        "_write_file_to_media_store",
        lambda path, object_key: captured["stored"].append((object_key, open(path, "rb").read())) or 5,
        raising=False,
    )
    monkeypatch.setattr(
        mk_import,
        "_copy_media_object",
        lambda source_key, dest_key: captured["copied"].append((source_key, dest_key)) or 10,
        raising=False,
    )

    def fake_create_item(**kwargs):
        captured["created_item"] = kwargs
        return 456

    monkeypatch.setattr(mk_import, "_medias_create_item", fake_create_item)
    monkeypatch.setattr(
        mk_import,
        "_medias_set_product_cover",
        lambda product_id, lang, object_key: captured.__setitem__("product_cover", (product_id, lang, object_key)),
        raising=False,
    )
    monkeypatch.setattr(
        mk_import,
        "_medias_replace_copywritings",
        lambda product_id, items, lang="en": captured.__setitem__("copy", (product_id, lang, items)),
        raising=False,
    )

    result = mk_import.import_mk_video(
        mk_video_metadata={
            "mp4_url": "https://cdn.example/demo.mp4",
            "filename": "demo.mp4",
            "cover_url": "/medias/object?object_key=artifacts%2Fmk%2Fcover.png",
            "product_name": "",
            "product_code": "DEMO",
            "product_link": "",
            "mk_id": 3528,
        },
        translator_id=1,
        actor_user_id=1,
    )

    assert result["media_product_id"] == 123
    assert result["media_item_id"] == 456
    assert captured["product_insert_args"][1] == "科学小实验手工玩具"
    assert captured["product_insert_args"][4] == "https://cdn.example/main.jpg"
    assert captured["created_item"]["object_key"] == "1/medias/123/demo.mp4"
    assert captured["created_item"]["cover_object_key"] == "1/medias/123/item_cover_demo.png"
    assert captured["created_item"]["file_size"] == 5
    assert captured["stored"] == [("1/medias/123/demo.mp4", b"video")]
    assert (
        "artifacts/mk/cover.png",
        "1/medias/123/item_cover_demo.png",
    ) in captured["copied"]
    assert (
        "xuanpin/product-main-images/demo/main.jpg",
        "1/medias/123/product_cover_en.jpg",
    ) in captured["copied"]
    assert captured["product_cover"] == (123, "en", "1/medias/123/product_cover_en.jpg")
    assert captured["copy"] == (
        123,
        "en",
        [{
            "body": "标题: Screen-Free Weekend\n文案: Busy kids, better learning.\n描述: Get Yours Today",
        }],
    )


def test_find_existing_product_item_by_meta_warns_when_product_link_probe_fails(monkeypatch):
    monkeypatch.setattr(mk_import, "_probe_product_link", lambda url: (False, "HTTP 404"), raising=False)
    monkeypatch.setattr(
        mk_import,
        "_find_existing_product",
        lambda normalized_code: {
            "id": 123,
            "product_code": "abc-def-rjc",
            "product_link": "https://newjoyloo.com/products/abc-def-rjc",
        },
    )
    monkeypatch.setattr(mk_import, "query_one", lambda *args, **kwargs: {"id": 456})

    assert mk_import.find_existing_product_item_by_meta({"product_code": "ABC-DEF"}) == {
        "product_id": 123,
        "item_id": 456,
        "warnings": [{
            "type": "product_link_unavailable",
            "message": "商品链接可能不可访问",
            "url": "https://newjoyloo.com/products/abc-def-rjc",
            "detail": "HTTP 404",
        }],
    }


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


def test_find_existing_product_uses_indexed_code_candidates_before_regex(monkeypatch):
    calls = []

    def fake_query_one(sql, args=None):
        calls.append((sql, args))
        if args == ("abc-def-rjc",):
            return {"id": 7, "product_code": "abc-def-rjc"}
        if "REGEXP_REPLACE" in sql:
            raise AssertionError("regex lookup should not run after indexed hit")
        return None

    monkeypatch.setattr(mk_import, "query_one", fake_query_one)

    assert mk_import._find_existing_product("abc-def") == {"id": 7, "product_code": "abc-def-rjc"}
    assert [args for _sql, args in calls] == [("abc-def",), ("abc-def-rjc",)]
    assert all("product_code=%s" in sql for sql, _args in calls)


def test_find_existing_product_keeps_regex_fallback_for_legacy_codes(monkeypatch):
    calls = []

    def fake_query_one(sql, args=None):
        calls.append((sql, args))
        if "REGEXP_REPLACE" in sql:
            return {"id": 8, "product_code": "legacy-RJC"}
        return None

    monkeypatch.setattr(mk_import, "query_one", fake_query_one)

    assert mk_import._find_existing_product("legacy") == {"id": 8, "product_code": "legacy-RJC"}
    assert len(calls) == 3
    assert "REGEXP_REPLACE" in calls[-1][0]


def test_import_mk_video_reuses_metadata_media_product_id_without_code_scan(monkeypatch):
    captured = {}

    monkeypatch.setattr(mk_import, "_is_video_already_imported", lambda filename: False)
    monkeypatch.setattr(
        mk_import,
        "_find_existing_product",
        lambda normalized_code: (_ for _ in ()).throw(AssertionError("code lookup should be skipped")),
    )
    monkeypatch.setattr(mk_import, "_probe_product_link", lambda url: (True, None), raising=False)

    def fake_query_one(sql, args=None):
        assert "WHERE id=%s" in sql
        assert args == (587,)
        return {
            "id": 587,
            "user_id": 42,
            "product_code": "tool-free-robotics-building-set-rjc",
            "product_link": "https://newjoyloo.com/products/tool-free-robotics-building-set-rjc",
        }

    def fake_download_mp4(url, path, **kwargs):
        with open(path, "wb") as f:
            f.write(b"video")
        return 5

    monkeypatch.setattr(mk_import, "query_one", fake_query_one)
    monkeypatch.setattr(mk_import, "_download_mp4", fake_download_mp4)
    monkeypatch.setattr(
        mk_import.object_keys,
        "build_media_object_key",
        lambda user_id, product_id, filename: f"{user_id}/medias/{product_id}/{filename}",
    )
    monkeypatch.setattr(mk_import, "_write_file_to_media_store", lambda path, object_key: 5, raising=False)
    monkeypatch.setattr(mk_import, "_medias_create_item", lambda **kwargs: captured.update(kwargs) or 456)

    result = mk_import.import_mk_video(
        mk_video_metadata={
            "mp4_url": "https://cdn.example/original.mp4",
            "filename": "old-product-video.mp4",
            "product_name": "Existing product",
            "product_code": "tool-free-robotics-building-set",
            "product_link": "https://newjoyloo.com/products/tool-free-robotics-building-set-rjc",
            "media_product_id": 587,
        },
        translator_id=None,
        actor_user_id=1,
    )

    assert result["is_new_product"] is False
    assert result["media_product_id"] == 587
    assert captured["user_id"] == 42


def test_probe_product_link_uses_short_timeouts(monkeypatch):
    captured = []

    class FakeResponse:
        status_code = 200

    def fake_head(url, **kwargs):
        captured.append(kwargs)
        return FakeResponse()

    monkeypatch.setattr(mk_import.requests, "head", fake_head)

    assert mk_import._probe_product_link("https://example.test/products/demo") == (True, None)
    assert captured[0]["timeout"] == mk_import._PRODUCT_LINK_HEAD_TIMEOUT_SECONDS
    assert captured[0]["timeout"] <= 3


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


def test_download_mp4_resolves_xuanpin_proxy_url_to_wedev_media(tmp_path, monkeypatch):
    from appcore import mk_import
    from appcore import pushes

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "video/mp4"}

        def iter_content(self, chunk_size):
            yield b"video-bytes"

        def raise_for_status(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        pushes,
        "get_localized_texts_base_url",
        lambda: "https://os.wedev.vip",
        raising=False,
    )
    monkeypatch.setattr(
        pushes,
        "build_localized_texts_headers",
        lambda: {"Authorization": "Bearer token", "Content-Type": "application/json"},
        raising=False,
    )

    captured = {}

    def fake_get(url, *args, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        assert url == "https://os.wedev.vip/medias/uploads2/202604/demo.mp4"
        assert kwargs["headers"] == {
            "Authorization": "Bearer token",
            "Accept": "video/*,*/*;q=0.8",
        }
        return FakeResponse()

    monkeypatch.setattr(mk_import.requests, "get", fake_get)

    dest = tmp_path / "out.mp4"
    n = mk_import._download_mp4(
        "/xuanpin/api/mk-video?path=uploads2%2F202604%2Fdemo.mp4",
        str(dest),
    )

    assert n == len(b"video-bytes")
    assert dest.read_bytes() == b"video-bytes"
    assert captured["kwargs"]["stream"] is True


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
