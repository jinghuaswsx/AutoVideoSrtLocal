from __future__ import annotations


def test_product_detail_flask_response_returns_payload(authed_client_no_db):
    from web.services.media_product_detail import product_detail_flask_response

    payload = {"product": {"id": 1}, "covers": {}, "copywritings": [], "items": []}
    with authed_client_no_db.application.app_context():
        response = product_detail_flask_response(payload)

    assert response.get_json() == payload


def test_build_product_detail_response_enriches_product_items_and_raw_sources():
    from web.services.media_product_detail import build_product_detail_response

    product = {"id": 123, "name": "Demo"}
    calls = {}

    def serialize_product(row, items_count, cover_item_id, **kwargs):
        calls["product_kwargs"] = kwargs
        return {
            "id": row["id"],
            "covers": kwargs["covers"],
            "skus": kwargs["skus"],
            "xmyc_keys": sorted(kwargs["xmyc_index"]),
            "roas_rmb_per_usd": kwargs["roas_rmb_per_usd"],
        }

    def serialize_item(item, raw_sources_by_id):
        calls.setdefault("items", []).append((item["id"], dict(raw_sources_by_id)))
        return {
            "id": item["id"],
            "raw_source_name": raw_sources_by_id[int(item["source_raw_id"])]["display_name"],
        }

    def list_xmyc_unit_prices(skus):
        calls["xmyc_skus"] = list(skus)
        return {"sku-a": {"unit_price": 1}}

    payload = build_product_detail_response(
        123,
        product=product,
        get_product_covers_fn=lambda pid: {"en": "cover.jpg"},
        list_items_fn=lambda pid: [{"id": 10, "source_raw_id": "88", "auto_translated": False}],
        list_raw_sources_fn=lambda pid: [{"id": 88, "display_name": "raw-88.mp4"}],
        list_product_skus_fn=lambda pid: [
            {"dianxiaomi_sku": "sku-a"},
            {"dianxiaomi_sku": ""},
        ],
        list_xmyc_unit_prices_fn=list_xmyc_unit_prices,
        list_copywritings_fn=lambda pid: [{"id": 9, "title": "copy"}],
        get_configured_rmb_per_usd_fn=lambda: 7.2,
        count_item_versions_fn=lambda item_ids: {},
        list_item_mk_bindings_fn=lambda item_ids: {},
        serialize_product_fn=serialize_product,
        serialize_item_fn=serialize_item,
    )

    assert calls["xmyc_skus"] == ["sku-a", ""]
    assert calls["product_kwargs"] == {
        "covers": {"en": "cover.jpg"},
        "roas_rmb_per_usd": 7.2,
        "skus": [{"dianxiaomi_sku": "sku-a"}, {"dianxiaomi_sku": ""}],
        "xmyc_index": {"sku-a": {"unit_price": 1}},
    }
    assert calls["items"] == [
        (10, {88: {"id": 88, "display_name": "raw-88.mp4"}}),
    ]
    assert payload == {
        "product": {
            "id": 123,
            "covers": {"en": "cover.jpg"},
            "skus": [{"dianxiaomi_sku": "sku-a"}, {"dianxiaomi_sku": ""}],
            "xmyc_keys": ["sku-a"],
            "roas_rmb_per_usd": 7.2,
        },
        "covers": {"en": "cover.jpg"},
        "copywritings": [{"id": 9, "title": "copy"}],
        "items": [{"id": 10, "raw_source_name": "raw-88.mp4"}],
    }


def test_build_product_detail_response_skips_raw_sources_when_items_do_not_need_them():
    from web.services.media_product_detail import build_product_detail_response

    raw_source_calls = []

    payload = build_product_detail_response(
        123,
        product={"id": 123, "name": "Demo"},
        get_product_covers_fn=lambda pid: {},
        list_items_fn=lambda pid: [{"id": 11, "source_raw_id": None, "auto_translated": False}],
        list_raw_sources_fn=lambda pid: raw_source_calls.append(pid) or [],
        list_product_skus_fn=lambda pid: [],
        list_xmyc_unit_prices_fn=lambda skus: {},
        list_copywritings_fn=lambda pid: [],
        get_configured_rmb_per_usd_fn=lambda: 6.83,
        count_item_versions_fn=lambda item_ids: {},
        list_item_mk_bindings_fn=lambda item_ids: {},
        serialize_product_fn=lambda row, *args, **kwargs: {"id": row["id"]},
        serialize_item_fn=lambda item, raw_sources_by_id: {
            "id": item["id"],
            "raw_sources_by_id": raw_sources_by_id,
        },
    )

    assert raw_source_calls == []
    assert payload["items"] == [{"id": 11, "raw_sources_by_id": {}}]


def test_build_product_detail_response_includes_item_versions_count():
    from web.services.media_product_detail import build_product_detail_response

    count_calls = []

    payload = build_product_detail_response(
        123,
        product={"id": 123, "name": "Demo"},
        get_product_covers_fn=lambda pid: {},
        list_items_fn=lambda pid: [
            {
                "id": 44,
                "product_id": 123,
                "lang": "fr",
                "filename": "v.mp4",
                "object_key": "k/v.mp4",
                "created_at": None,
            }
        ],
        list_raw_sources_fn=lambda pid: [],
        list_product_skus_fn=lambda pid: [],
        list_xmyc_unit_prices_fn=lambda skus: {},
        list_copywritings_fn=lambda pid: [],
        get_configured_rmb_per_usd_fn=lambda: 6.83,
        count_item_versions_fn=lambda item_ids: count_calls.append(list(item_ids)) or {44: 2},
        list_item_mk_bindings_fn=lambda item_ids: {},
        serialize_product_fn=lambda row, *args, **kwargs: {"id": row["id"]},
        serialize_item_fn=lambda item, raw_sources_by_id: {
            "id": item["id"],
            "versions_count": item["versions_count"],
        },
    )

    assert count_calls == [[44]]
    assert payload["items"] == [{"id": 44, "versions_count": 2}]


def test_build_product_detail_response_links_localized_item_to_english_source_item():
    from web.services.media_product_detail import build_product_detail_response

    source_filename = "2026.03.24-demo-source.mp4"

    payload = build_product_detail_response(
        123,
        product={"id": 123, "name": "Demo"},
        get_product_covers_fn=lambda pid: {},
        list_items_fn=lambda pid: [
            {
                "id": 10,
                "product_id": 123,
                "lang": "en",
                "filename": source_filename,
                "display_name": source_filename,
                "source_raw_id": None,
                "auto_translated": False,
            },
            {
                "id": 44,
                "product_id": 123,
                "lang": "it",
                "filename": "translated.mp4",
                "display_name": "translated.mp4",
                "source_raw_id": 88,
                "source_ref_id": None,
                "auto_translated": False,
            },
        ],
        list_raw_sources_fn=lambda pid: [{"id": 88, "display_name": source_filename}],
        list_product_skus_fn=lambda pid: [],
        list_xmyc_unit_prices_fn=lambda skus: {},
        list_copywritings_fn=lambda pid: [],
        get_configured_rmb_per_usd_fn=lambda: 6.83,
        count_item_versions_fn=lambda item_ids: {},
        list_item_mk_bindings_fn=lambda item_ids: {},
        serialize_product_fn=lambda row, *args, **kwargs: {"id": row["id"]},
        serialize_item_fn=lambda item, raw_sources_by_id: {
            "id": item["id"],
            "source_english_item": item.get("source_english_item"),
        },
    )

    assert payload["items"] == [
        {"id": 10, "source_english_item": None},
        {
            "id": 44,
            "source_english_item": {
                "id": 10,
                "filename": source_filename,
                "display_name": source_filename,
                "lang": "en",
            },
        },
    ]


def test_build_product_detail_response_attaches_mk_source_material_to_source_item():
    from web.services.media_product_detail import build_product_detail_response

    material_key = "a" * 64
    source_filename = "2026.03.24-demo-source.mp4"

    payload = build_product_detail_response(
        123,
        product={"id": 123, "name": "Demo", "product_code": "demo-widget-rjc"},
        get_product_covers_fn=lambda pid: {},
        list_items_fn=lambda pid: [
            {
                "id": 10,
                "product_id": 123,
                "lang": "en",
                "filename": source_filename,
                "display_name": source_filename,
                "source_raw_id": None,
                "auto_translated": False,
            },
            {
                "id": 44,
                "product_id": 123,
                "lang": "it",
                "filename": "translated.mp4",
                "display_name": "translated.mp4",
                "source_raw_id": 88,
                "source_ref_id": None,
                "auto_translated": False,
            },
        ],
        list_raw_sources_fn=lambda pid: [{"id": 88, "display_name": source_filename}],
        list_product_skus_fn=lambda pid: [],
        list_xmyc_unit_prices_fn=lambda skus: {},
        list_copywritings_fn=lambda pid: [],
        get_configured_rmb_per_usd_fn=lambda: 6.83,
        count_item_versions_fn=lambda item_ids: {},
        list_item_mk_bindings_fn=lambda item_ids: {
            10: {
                "media_item_id": 10,
                "mk_product_id": 3528,
                "mk_product_name": "MK Demo",
                "mk_video_path": "uploads2/demo.mp4",
                "mk_video_name": source_filename,
                "mk_video_metadata": {"material_key": material_key},
            }
        },
        serialize_product_fn=lambda row, *args, **kwargs: {"id": row["id"]},
        serialize_item_fn=lambda item, raw_sources_by_id: {
            "id": item["id"],
            "source_mk_material": item.get("source_mk_material"),
            "source_english_item": item.get("source_english_item"),
        },
    )

    expected = {
        "material_key": material_key,
        "detail_url": f"/xuanpin/mk/videos/{material_key}",
        "search_url": f"/xuanpin/mk?q={source_filename}",
        "display_name": source_filename,
        "mk_product_id": 3528,
        "mk_product_name": "MK Demo",
        "video_path": "uploads2/demo.mp4",
    }
    assert payload["items"][0]["source_mk_material"] == expected
    assert payload["items"][1]["source_english_item"]["source_mk_material"] == expected


def test_build_product_detail_response_derives_mk_material_key_when_binding_is_legacy():
    import hashlib

    from web.services.media_product_detail import build_product_detail_response

    expected_key = hashlib.sha256(
        "demo-widget|3528|uploads2/demo.mp4".encode("utf-8")
    ).hexdigest()

    payload = build_product_detail_response(
        123,
        product={"id": 123, "name": "Demo", "product_code": "demo-widget-rjc"},
        get_product_covers_fn=lambda pid: {},
        list_items_fn=lambda pid: [
            {
                "id": 10,
                "product_id": 123,
                "lang": "en",
                "filename": "legacy.mp4",
                "display_name": "legacy.mp4",
                "source_raw_id": None,
                "auto_translated": False,
            },
        ],
        list_raw_sources_fn=lambda pid: [],
        list_product_skus_fn=lambda pid: [],
        list_xmyc_unit_prices_fn=lambda skus: {},
        list_copywritings_fn=lambda pid: [],
        get_configured_rmb_per_usd_fn=lambda: 6.83,
        count_item_versions_fn=lambda item_ids: {},
        list_item_mk_bindings_fn=lambda item_ids: {
            10: {
                "media_item_id": 10,
                "mk_product_id": 3528,
                "mk_product_name": "MK Demo",
                "mk_video_path": "uploads2/demo.mp4",
                "mk_video_name": "legacy.mp4",
                "mk_video_metadata": {"product_code": "demo-widget"},
            }
        },
        serialize_product_fn=lambda row, *args, **kwargs: {"id": row["id"]},
        serialize_item_fn=lambda item, raw_sources_by_id: {
            "id": item["id"],
            "source_mk_material": item.get("source_mk_material"),
        },
    )

    assert payload["items"][0]["source_mk_material"]["material_key"] == expected_key
    assert payload["items"][0]["source_mk_material"]["detail_url"] == (
        f"/xuanpin/mk/videos/{expected_key}"
    )


def test_serialize_item_includes_task_center_link_for_task_material():
    from web.routes.medias._serializers import _serialize_item

    payload = _serialize_item(
        {
            "id": 44,
            "product_id": 123,
            "lang": "de",
            "filename": "translated.mp4",
            "display_name": "translated.mp4",
            "object_key": "media/translated.mp4",
            "cover_object_key": None,
            "thumbnail_path": None,
            "duration_seconds": None,
            "file_size": None,
            "source_raw_id": 88,
            "source_ref_id": None,
            "bulk_task_id": "",
            "auto_translated": False,
            "task_id": 456,
            "versions_count": 0,
            "created_at": None,
        },
        {88: {"id": 88, "display_name": "raw-88.mp4"}},
    )

    assert payload["task_id"] == 456
    assert payload["task_url"] == "/tasks/detail/456"


def test_serialize_item_includes_source_english_item_for_source_video_link():
    from web.routes.medias._serializers import _serialize_item

    payload = _serialize_item(
        {
            "id": 44,
            "product_id": 123,
            "lang": "it",
            "filename": "translated.mp4",
            "display_name": "translated.mp4",
            "object_key": "media/translated.mp4",
            "cover_object_key": None,
            "thumbnail_path": None,
            "duration_seconds": None,
            "file_size": None,
            "source_raw_id": 88,
            "source_ref_id": None,
            "bulk_task_id": "",
            "auto_translated": False,
            "task_id": None,
            "versions_count": 0,
            "source_english_item": {
                "id": 10,
                "filename": "2026.03.24-demo-source.mp4",
                "display_name": "2026.03.24-demo-source.mp4",
                "lang": "en",
            },
            "created_at": None,
        },
        {88: {"id": 88, "display_name": "2026.03.24-demo-source.mp4"}},
    )

    assert payload["source_english_item"] == {
        "id": 10,
        "filename": "2026.03.24-demo-source.mp4",
        "display_name": "2026.03.24-demo-source.mp4",
        "lang": "en",
    }


def test_serialize_item_includes_source_mk_material_for_mingkong_link():
    from web.routes.medias._serializers import _serialize_item

    payload = _serialize_item(
        {
            "id": 10,
            "product_id": 123,
            "lang": "en",
            "filename": "source.mp4",
            "display_name": "source.mp4",
            "object_key": "media/source.mp4",
            "cover_object_key": None,
            "thumbnail_path": None,
            "duration_seconds": None,
            "file_size": None,
            "source_raw_id": None,
            "source_ref_id": None,
            "bulk_task_id": "",
            "auto_translated": False,
            "task_id": None,
            "versions_count": 0,
            "source_mk_material": {
                "material_key": "b" * 64,
                "detail_url": "/xuanpin/mk/videos/" + "b" * 64,
                "search_url": "/xuanpin/mk?q=source.mp4",
                "display_name": "source.mp4",
                "mk_product_id": 3528,
                "mk_product_name": "MK Demo",
                "video_path": "uploads2/source.mp4",
            },
            "created_at": None,
        },
        {},
    )

    assert payload["source_mk_material"]["detail_url"] == "/xuanpin/mk/videos/" + "b" * 64
