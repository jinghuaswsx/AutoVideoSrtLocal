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
        serialize_product_fn=lambda row, *args, **kwargs: {"id": row["id"]},
        serialize_item_fn=lambda item, raw_sources_by_id: {
            "id": item["id"],
            "raw_sources_by_id": raw_sources_by_id,
        },
    )

    assert raw_source_calls == []
    assert payload["items"] == [{"id": 11, "raw_sources_by_id": {}}]
