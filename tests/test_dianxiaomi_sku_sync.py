from __future__ import annotations

import json


def _dxm_shopify_product_payload(variant_ids: list[str]) -> dict:
    return {
        "code": 0,
        "data": {
            "page": {
                "totalPage": 1,
                "list": [
                    {
                        "shopifyProductId": "8552296775853",
                        "handle": "3d-curved-screen-magnifier-for-smartphones-rjc",
                        "title": "3D Curved Screen Magnifier for Smartphones",
                        "shopId": "8477915",
                        "variants": [
                            {
                                "shopifyVariantId": variant_id,
                                "sku": "",
                                "price": "12.95",
                                "option1": color,
                                "option2": size,
                                "inventoryQuantity": -9,
                                "weight": "0.3",
                                "weighUnit": "kg",
                            }
                            for variant_id, color, size in zip(
                                variant_ids,
                                ["White", "Black", "Red", "White", "Black"],
                                ["10 Inch", "10 Inch", "10 Inch", "12 Inch", "12 Inch"],
                            )
                        ],
                    }
                ],
            }
        },
    }


def _public_shopify_product_payload(variant_ids: list[str]) -> dict:
    colors = ["White", "Black", "Red"] * 3
    sizes = ["10 Inch"] * 3 + ["12 Inch"] * 3 + ["14 Inch"] * 3
    return {
        "id": 8552296775853,
        "handle": "3d-curved-screen-magnifier-for-smartphones-rjc",
        "title": "3D Curved Screen Magnifier for Smartphones",
        "variants": [
            {
                "id": int(variant_id),
                "sku": "",
                "price": 1295,
                "compare_at_price": 2495,
                "inventory_quantity": -9,
                "grams": 300,
                "title": f"{color} / {size}",
            }
            for variant_id, color, size in zip(variant_ids, colors, sizes)
        ],
    }


def test_run_sync_uses_public_shopify_variants_when_dianxiaomi_list_is_truncated(tmp_path):
    from tools import dianxiaomi_sku_sync as mod

    dxm_variant_ids = [
        "45910969319597",
        "45910969352365",
        "45910969385133",
        "45910969417901",
        "45910969450669",
    ]
    public_variant_ids = [
        *dxm_variant_ids,
        "45910969483437",
        "45910969516205",
        "45910969548973",
        "45910969581741",
    ]
    captured = {}

    def fake_apply_changes(plan):
        captured["plan"] = plan

    report = mod.run_sync(
        fetch_shopify_page=lambda page_no: _dxm_shopify_product_payload(dxm_variant_ids),
        fetch_dxm_page=lambda page_no: {
            "code": 0,
            "data": {"page": {"totalPage": 1, "list": []}},
        },
        fetch_local_products=lambda: [
            {"id": 320, "shopifyid": "8552296775853", "shopify_title": ""}
        ],
        apply_changes=fake_apply_changes,
        fetch_public_shopify_product=lambda product: mod.extract_public_shopify_product(
            _public_shopify_product_payload(public_variant_ids)
        ),
        output_dir=tmp_path,
        now_text="20260507-180000",
    )

    pairs = captured["plan"]["sku_replacements"][0][1]
    assert [row["shopify_variant_id"] for row in pairs] == public_variant_ids
    assert pairs[-1]["shopify_variant_title"] == "Red / 14 Inch"
    assert pairs[-1]["shopify_price"] == 12.95
    assert pairs[-1]["shopify_weight_grams"] == 300.0
    assert report["summary"]["matched_variant_pairs"] == 9
    assert json.loads((tmp_path / "dianxiaomi-sku-sync-20260507-180000.json").read_text(encoding="utf-8"))[
        "matched_products"
    ][0]["variants"] == 9


def test_public_shopify_variants_do_not_replace_when_product_id_mismatches():
    from tools import dianxiaomi_sku_sync as mod

    products = mod.extract_shopify_products(
        _dxm_shopify_product_payload(["1", "2", "3", "4", "5"])
    )
    wrong_public = mod.extract_public_shopify_product(
        {
            **_public_shopify_product_payload(["10", "11", "12", "13", "14", "15"]),
            "id": 999,
        }
    )

    enriched = mod.enrich_shopify_products_with_public_variants(
        products,
        fetch_public_shopify_product=lambda product: wrong_public,
    )

    assert [row["shopify_variant_id"] for row in enriched[0]["variants"]] == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]


def test_fetch_via_browser_uses_context_request_when_page_execution_context_is_navigating():
    from tools import dianxiaomi_sku_sync as mod

    captured = {}

    class FakeResponse:
        ok = True
        status = 200

        def text(self):
            return '{"code":0,"data":{"page":{"totalPage":1,"list":[]}}}'

    class FakeRequestContext:
        def post(self, api_url, **kwargs):
            captured["api_url"] = api_url
            captured["kwargs"] = kwargs
            return FakeResponse()

    class FakeContext:
        request = FakeRequestContext()

    class FakePage:
        context = FakeContext()
        url = "https://www.dianxiaomi.com/web/shopifyProduct/online"

        def evaluate(self, *_args, **_kwargs):
            raise RuntimeError("Page.evaluate: Execution context was destroyed, most likely because of a navigation")

    payload = mod._fetch_via_browser(
        FakePage(),
        "https://www.dianxiaomi.com/api/shopifyProduct/pageList.json",
        {"pageNo": 1, "shopId": "-1"},
    )

    assert payload["code"] == 0
    assert captured["api_url"] == "https://www.dianxiaomi.com/api/shopifyProduct/pageList.json"
    assert captured["kwargs"]["form"] == {"pageNo": "1", "shopId": "-1"}
    assert captured["kwargs"]["headers"]["X-Requested-With"] == "XMLHttpRequest"
