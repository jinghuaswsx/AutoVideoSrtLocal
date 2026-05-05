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
