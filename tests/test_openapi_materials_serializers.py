from __future__ import annotations

from datetime import datetime


def test_openapi_materials_serializes_product_metadata():
    from web.services.openapi_materials_serializers import serialize_product

    payload = serialize_product(
        {
            "id": 7,
            "product_code": "demo-product",
            "name": "Demo Product",
            "remark": None,
            "ai_score": "88.5",
            "ai_evaluation_result": "ok",
            "ai_evaluation_detail": "detail",
            "listing_status": "下架",
            "archived": 0,
            "created_at": datetime(2026, 5, 5, 8, 0, 0),
            "updated_at": None,
        }
    )

    assert payload["id"] == 7
    assert payload["product_code"] == "demo-product"
    assert payload["remark"] == ""
    assert payload["ai_score"] == 88.5
    assert payload["listing_status"] == "下架"
    assert payload["archived"] is False
    assert payload["created_at"] == "2026-05-05T08:00:00"
    assert payload["updated_at"] is None


def test_openapi_materials_serializes_cover_map_with_local_urls():
    from web.services.openapi_materials_serializers import serialize_cover_map

    payload = serialize_cover_map(
        {
            "en": "media/en.jpg",
            "de": "",
            "fr": "media/fr.jpg",
        },
        media_download_url=lambda key: f"http://local/{key}",
    )

    assert payload == {
        "en": {
            "object_key": "media/en.jpg",
            "download_url": "http://local/media/en.jpg",
            "storage_backend": "local",
        },
        "fr": {
            "object_key": "media/fr.jpg",
            "download_url": "http://local/media/fr.jpg",
            "storage_backend": "local",
        },
    }


def test_openapi_materials_groups_copywritings_by_language():
    from web.services.openapi_materials_serializers import group_copywritings

    payload = group_copywritings(
        [
            {"lang": "en", "title": "Title", "body": "Body"},
            {"lang": "de", "title": "Titel", "description": "Beschreibung"},
            {"title": "Fallback", "ad_copy": "Copy"},
        ]
    )

    assert [item["title"] for item in payload["en"]] == ["Title", "Fallback"]
    assert payload["de"][0]["description"] == "Beschreibung"
    assert payload["en"][1]["ad_copy"] == "Copy"


def test_openapi_materials_serializes_items_and_normalizes_target_url():
    from web.services.openapi_materials_serializers import normalize_target_url, serialize_items

    payload = serialize_items(
        [
            {
                "id": 1,
                "filename": "video.mp4",
                "display_name": "",
                "object_key": "media/video.mp4",
                "cover_object_key": "media/cover.jpg",
                "created_at": datetime(2026, 5, 5, 9, 0, 0),
            }
        ],
        media_download_url=lambda key: f"http://local/{key}",
    )

    assert payload[0]["display_name"] == "video.mp4"
    assert payload[0]["video_download_url"] == "http://local/media/video.mp4"
    assert payload[0]["video_cover_download_url"] == "http://local/media/cover.jpg"
    assert payload[0]["created_at"] == "2026-05-05T09:00:00"
    assert normalize_target_url("https://example.com/p?a=1&b=&a=2#frag") == "https://example.com/p?a=1&b=&a=2"


def test_openapi_materials_serializes_shopify_image_task():
    from web.services.openapi_materials_serializers import serialize_shopify_image_task

    assert serialize_shopify_image_task(None) is None
    assert serialize_shopify_image_task(
        {
            "id": 3,
            "product_id": 7,
            "product_code": "demo",
            "lang": "it",
            "shopify_product_id": "8559391932589",
            "link_url": "https://example.com/products/demo",
            "ignored": "value",
        }
    ) == {
        "id": 3,
        "product_id": 7,
        "product_code": "demo",
        "lang": "it",
        "shopify_product_id": "8559391932589",
        "link_url": "https://example.com/products/demo",
    }


def test_openapi_materials_builds_material_detail_response():
    from web.services import openapi_materials_serializers

    captured: dict = {}

    def fake_get_covers(product_id):
        captured["covers"] = product_id
        return {"en": "cover/en.jpg"}

    def fake_list_copywritings(product_id):
        captured["copywritings"] = product_id
        return [{"lang": "en", "title": "Title", "body": "Body"}]

    def fake_list_items(product_id):
        captured["items"] = product_id
        return [
            {
                "id": 5,
                "filename": "demo.mp4",
                "display_name": "",
                "object_key": "video/demo.mp4",
                "cover_object_key": "cover/demo.jpg",
            }
        ]

    product = {
        "id": 123,
        "product_code": "demo",
        "name": "Demo",
        "archived": 0,
    }

    payload = openapi_materials_serializers.build_material_detail_response(
        product,
        get_product_covers_fn=fake_get_covers,
        list_copywritings_fn=fake_list_copywritings,
        list_items_fn=fake_list_items,
        media_download_url_fn=lambda key: f"https://local/{key}",
    )

    assert captured == {"covers": 123, "copywritings": 123, "items": 123}
    assert payload["product"]["product_code"] == "demo"
    assert payload["covers"]["en"]["download_url"] == "https://local/cover/en.jpg"
    assert payload["copywritings"]["en"][0]["title"] == "Title"
    assert payload["items"][0]["display_name"] == "demo.mp4"
    assert payload["items"][0]["video_download_url"] == "https://local/video/demo.mp4"
    assert payload["storage_backend"] == "local"


def test_openapi_link_check_builds_bootstrap_response():
    from web.services.openapi_link_check import build_link_check_bootstrap_response

    captured: dict = {}

    def fake_list_languages():
        return [
            {"code": "de", "enabled": 1},
            {"code": "fr", "enabled": 0},
            {"code": "en", "enabled": 1},
        ]

    def fake_detect(target_url, enabled_languages):
        captured["detect"] = (target_url, enabled_languages)
        return "de"

    def fake_find_product(target_url, target_language):
        captured["find"] = (target_url, target_language)
        return {
            "id": 7,
            "product_code": "demo",
            "name": "Demo",
            "_matched_by": "localized_links_exact",
        }

    def fake_list_references(product_id, lang):
        captured["references"] = (product_id, lang)
        return [
            {"id": "skip", "kind": "detail", "filename": "empty.jpg", "object_key": ""},
            {"id": "cover", "kind": "cover", "filename": "cover.jpg", "object_key": "covers/de.jpg"},
        ]

    payload = build_link_check_bootstrap_response(
        "https://example.com/de/products/demo?b=&a=1#frag",
        list_languages_fn=fake_list_languages,
        detect_target_language_fn=fake_detect,
        find_product_fn=fake_find_product,
        list_reference_images_fn=fake_list_references,
        get_language_name_fn=lambda lang: "DE",
        media_download_url_fn=lambda key: f"https://local/{key}",
    )

    assert captured["detect"] == (
        "https://example.com/de/products/demo?b=&a=1#frag",
        {"de", "en"},
    )
    assert captured["find"] == (
        "https://example.com/de/products/demo?b=&a=1#frag",
        "de",
    )
    assert captured["references"] == (7, "de")
    assert payload["product"] == {"id": 7, "product_code": "demo", "name": "Demo"}
    assert payload["target_language"] == "de"
    assert payload["target_language_name"] == "DE"
    assert payload["matched_by"] == "localized_links_exact"
    assert payload["normalized_url"] == "https://example.com/de/products/demo?b=&a=1"
    assert payload["reference_images"] == [
        {
            "id": "cover",
            "kind": "cover",
            "filename": "cover.jpg",
            "download_url": "https://local/covers/de.jpg",
            "storage_backend": "local",
        }
    ]
