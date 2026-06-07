from __future__ import annotations

import json

from appcore.order_analytics import unmatched_details


def test_enrich_rows_prefers_dianxiaomi_asset_and_local_main_image():
    def fake_query(sql, args=()):
        if "FROM dianxiaomi_product_assets" in sql:
            assert "LOWER(product_code)" in sql
            assert args[0] == "portable-lamp"
            return [
                {
                    "product_code": "portable-lamp",
                    "product_name": "Portable Lamp",
                    "product_english_title": "Portable Lamp",
                    "product_cn_name": "便携灯",
                    "product_main_image_url": "https://cdn.example.com/lamp.jpg",
                    "product_main_image_object_key": "products/lamp main.jpg",
                    "product_url": "https://example.com/products/portable-lamp",
                }
            ]
        return []

    def fail_generate(*args, **kwargs):
        raise AssertionError("asset hit should not call title translation")

    rows = [
        {
            "campaign_name": "Portable Lamp",
            "normalized_campaign_code": "portable-lamp-rjc",
            "allocation_reason": "unmatched_product",
        }
    ]

    enriched = unmatched_details.enrich_rows(
        rows,
        detail_type="ads",
        query_fn=fake_query,
        invoke_generate_fn=fail_generate,
    )

    assert enriched[0]["product_cn_name"] == "便携灯"
    assert enriched[0]["product_title"] == "Portable Lamp"
    assert enriched[0]["product_title_zh_source"] == "dianxiaomi_product_assets"
    assert enriched[0]["product_image_url"] == "https://cdn.example.com/lamp.jpg"
    assert enriched[0]["product_image_object_key"] == "products/lamp main.jpg"
    assert enriched[0]["product_image_local_url"] == (
        "/medias/object?object_key=products%2Flamp%20main.jpg"
    )
    assert enriched[0]["product_code_hint"] == "portable-lamp"


def test_enrich_rows_translates_missing_chinese_title_with_openrouter_flash_lite():
    llm_call = {}

    def fake_query(sql, args=()):
        return []

    def fake_generate(use_case_code, **kwargs):
        llm_call["use_case_code"] = use_case_code
        llm_call.update(kwargs)
        payload_text = kwargs["prompt"].split("输入 JSON：\n", 1)[1]
        payload = json.loads(payload_text)
        item_id = payload["items"][0]["id"]
        return {"json": {"translations": [{"id": item_id, "zh": "太阳能庭院灯套装"}]}}

    rows = [
        {
            "dxm_order_id": "ORDER-1",
            "skus": "solar-garden-light-rjc",
            "product_names": "Solar Garden Light Set",
        },
        {
            "dxm_order_id": "ORDER-2",
            "skus": "solar-garden-light-backup-rjc",
            "product_names": "Solar Garden Light Set",
        }
    ]

    enriched = unmatched_details.enrich_rows(
        rows,
        detail_type="orders",
        user_id=42,
        query_fn=fake_query,
        invoke_generate_fn=fake_generate,
    )

    assert llm_call["use_case_code"] == "order_analytics.unmatched_title_translate"
    assert llm_call["provider_override"] == "openrouter"
    assert llm_call["model_override"] == "google/gemini-3.1-flash-lite"
    assert llm_call["user_id"] == 42
    assert llm_call["temperature"] == 0.0
    assert llm_call["response_schema"]["required"] == ["translations"]
    assert "Solar Garden Light Set" in llm_call["prompt"]
    assert enriched[0]["product_cn_name"] == "太阳能庭院灯套装"
    assert enriched[1]["product_cn_name"] == "太阳能庭院灯套装"
    assert enriched[0]["product_title"] == "Solar Garden Light Set"
    assert enriched[0]["product_title_zh_source"] == "gemini_3_1_flash_lite"
    assert enriched[0]["product_code_hint"] == "solar-garden-light"
