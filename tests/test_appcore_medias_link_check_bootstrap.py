from __future__ import annotations

from appcore import medias


def test_find_product_for_link_check_url_prefers_exact_localized_link(monkeypatch):
    rows = [
        {
            "id": 1,
            "product_code": "demo-exact",
            "name": "Exact",
            "localized_links_json": {"de": "https://example.com/de/products/demo?variant=1"},
        },
        {
            "id": 2,
            "product_code": "demo-path",
            "name": "Path",
            "localized_links_json": {"de": "https://example.com/de/products/other"},
        },
    ]

    monkeypatch.setattr(medias, "query", lambda sql, args=(): rows)
    monkeypatch.setattr(medias, "query_one", lambda sql, args=(): None)

    product = medias.find_product_for_link_check_url(
        "https://example.com/de/products/demo?variant=1",
        "de",
    )

    assert product["id"] == 1
    assert product["_matched_by"] == "localized_links_exact"


def test_find_product_for_link_check_url_supports_json_string_links(monkeypatch):
    rows = [
        {
            "id": 8,
            "product_code": "demo-json",
            "name": "Json",
            "localized_links_json": '{"de":"https://example.com/de/products/demo-json?variant=9"}',
        }
    ]

    monkeypatch.setattr(medias, "query", lambda sql, args=(): rows)
    monkeypatch.setattr(medias, "query_one", lambda sql, args=(): None)

    product = medias.find_product_for_link_check_url(
        "https://example.com/de/products/demo-json?variant=9",
        "de",
    )

    assert product["id"] == 8
    assert product["_matched_by"] == "localized_links_exact"


def test_find_product_for_link_check_url_falls_back_to_path_then_product_code(monkeypatch):
    rows = [
        {
            "id": 2,
            "product_code": "demo-path",
            "name": "Path",
            "localized_links_json": {"de": "https://example.com/de/products/demo?variant=2"},
        }
    ]
    product_by_code = {
        "id": 3,
        "product_code": "demo-handle",
        "name": "Code",
    }

    queries: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        queries.append((sql, args))
        return rows

    def fake_query_one(sql, args=()):
        queries.append((sql, args))
        if args == ("demo-handle",):
            return product_by_code
        return None

    monkeypatch.setattr(medias, "query", fake_query)
    monkeypatch.setattr(medias, "query_one", fake_query_one)

    path_product = medias.find_product_for_link_check_url(
        "https://example.com/de/products/demo?variant=3",
        "de",
    )
    code_product = medias.find_product_for_link_check_url(
        "https://example.com/de/products/demo-handle-rjc?variant=1",
        "de",
    )

    assert path_product["id"] == 2
    assert path_product["_matched_by"] == "localized_links_path"
    assert code_product["id"] == 3
    assert code_product["_matched_by"] == "product_code"


def test_list_reference_images_for_lang_returns_cover_then_detail_without_english_fallback(monkeypatch):
    def fake_query(sql, args=()):
        sql = " ".join(sql.split())
        if "FROM media_product_covers" in sql:
            assert args == (7, "de")
            return [{"id": 9, "object_key": "covers/de.jpg"}]
        if "FROM media_product_detail_images" in sql:
            assert args == (7, "de")
            return [
                {"id": 12, "sort_order": 2, "object_key": "details/de_2.jpg"},
                {"id": 11, "sort_order": 1, "object_key": "details/de_1.jpg"},
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(medias, "query", fake_query)

    images = medias.list_reference_images_for_lang(7, "de")

    assert images == [
        {"id": "cover-de", "kind": "cover", "filename": "de.jpg", "object_key": "covers/de.jpg"},
        {"id": "detail-11", "kind": "detail", "filename": "de_1.jpg", "object_key": "details/de_1.jpg"},
        {"id": "detail-12", "kind": "detail", "filename": "de_2.jpg", "object_key": "details/de_2.jpg"},
    ]


def test_list_reference_images_for_lang_cover_query_does_not_require_cover_id(monkeypatch):
    def fake_query(sql, args=()):
        sql = " ".join(sql.split())
        if "FROM media_product_covers" in sql:
            assert "SELECT id," not in sql
            return [{"lang": "de", "object_key": "covers/de.jpg"}]
        if "FROM media_product_detail_images" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(medias, "query", fake_query)

    images = medias.list_reference_images_for_lang(7, "de")

    assert images == [
        {"id": "cover-de", "kind": "cover", "filename": "de.jpg", "object_key": "covers/de.jpg"},
    ]
