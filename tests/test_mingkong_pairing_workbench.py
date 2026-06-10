from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import web.routes.medias.products as products_route
from appcore import dianxiaomi_mingkong_pairing as pairing


def test_build_workbench_payload_merges_live_dxm03_pairing():
    product = {
        "id": 757,
        "product_code": "adjustable-claw-clippers-rjc",
        "name": "猫指甲剪",
        "shopifyid": "8602536280237",
        "shopify_title": "Adjustable claw clippers.",
        "purchase_1688_url": "https://detail.1688.com/offer/673641403157.html",
    }
    rows = [
        {
            "id": 1,
            "shopify_product_id": "8602536280237",
            "shopify_variant_id": "46078674206893",
            "shopify_variant_title": "Blue",
            "dianxiaomi_sku": "46078674206893",
            "dianxiaomi_sku_code": "11002418",
            "dianxiaomi_name": "推推剪-蓝色",
            "source": "mingkong_pair",
        }
    ]

    def fake_fetch_live(skus):
        assert skus == ["46078674206893"]
        return {
            "46078674206893": {
                "commodity": {"id": "dxm-row", "relation_flag": True},
                "pairing": {
                    "pair_row_id": "pair-row",
                    "is_paired": True,
                    "alibaba_product_id": "673641403157",
                    "sku_id_alibaba": "5722867611779",
                    "supplier_name": "义乌市柚果宠物用品有限公司",
                },
            }
        }

    payload = pairing.build_workbench_payload(
        product,
        rows,
        fetch_live_fn=fake_fetch_live,
    )

    assert payload["product"]["alibaba_product_id"] == "673641403157"
    assert payload["summary"]["sku_count"] == 1
    assert payload["summary"]["paired_count"] == 1
    assert payload["summary"]["missing_count"] == 0
    assert payload["items"][0]["status"] == "paired"
    assert payload["items"][0]["dxm03"]["pairing"]["sku_id_alibaba"] == "5722867611779"


def test_build_workbench_payload_surfaces_live_error_without_dropping_local_rows():
    product = {
        "id": 757,
        "product_code": "adjustable-claw-clippers-rjc",
        "purchase_1688_url": "https://detail.1688.com/offer/673641403157.html",
    }
    rows = [{"shopify_variant_id": "v1", "dianxiaomi_sku": "sku-1"}]

    def fake_fetch_live(_skus):
        raise RuntimeError("DXM03 not logged in")

    payload = pairing.build_workbench_payload(
        product,
        rows,
        fetch_live_fn=fake_fetch_live,
    )

    assert payload["summary"]["live_error"] == "DXM03 not logged in"
    assert payload["summary"]["sku_count"] == 1
    assert payload["items"][0]["status"] == "missing_dxm03_commodity"


def test_build_workbench_payload_marks_combo_components_paired():
    product = {
        "id": 88,
        "product_code": "combo-rjc",
        "purchase_1688_url": "https://detail.1688.com/offer/728762667637.html",
    }
    rows = [
        {
            "shopify_variant_id": "variant-combo",
            "shopify_variant_title": "Black + White",
            "dianxiaomi_sku": "0526-15016029",
            "dianxiaomi_name": "节省空间的6钩收纳架 黑色+白色4个装",
        }
    ]

    def fake_fetch_live(_skus):
        return {
            "0526-15016029": {
                "commodity": {
                    "id": "combo-product",
                    "sku": "0526-15016029",
                    "is_combo": True,
                    "group_state": 1,
                    "image_url": "https://example.test/combo.jpg",
                },
                "pairing": None,
                "combo_components": [
                    {
                        "product_id": "white-product",
                        "sku": "0526-15013079",
                        "name": "白色 1件装",
                        "quantity": 2,
                        "image_url": "https://example.test/white.jpg",
                        "pairing": {"is_paired": True, "sku_id_alibaba": "5053621705941"},
                    },
                    {
                        "product_id": "black-product",
                        "sku": "0526-15015069",
                        "name": "黑色 1件装",
                        "quantity": 2,
                        "image_url": "https://example.test/black.jpg",
                        "pairing": {"is_paired": True, "sku_id_alibaba": "5053621705940"},
                    },
                ],
            }
        }

    payload = pairing.build_workbench_payload(
        product,
        rows,
        fetch_live_fn=fake_fetch_live,
    )

    assert payload["summary"]["paired_count"] == 1
    assert payload["items"][0]["status"] == "combo_components_paired"
    assert payload["items"][0]["is_combo"] is True
    assert payload["items"][0]["image_url"] == "https://example.test/combo.jpg"
    assert payload["items"][0]["combo_components"][0]["quantity"] == 2


def test_build_workbench_payload_marks_incomplete_combo_components_as_gap():
    product = {
        "id": 88,
        "product_code": "combo-rjc",
        "purchase_1688_url": "",
    }
    rows = [{"shopify_variant_id": "variant-combo", "dianxiaomi_sku": "combo-sku"}]

    def fake_fetch_live(_skus):
        return {
            "combo-sku": {
                "commodity": {"id": "combo-product", "is_combo": True, "group_state": 1},
                "pairing": None,
                "combo_components": [
                    {
                        "product_id": "child-product",
                        "sku": "child-sku",
                        "name": "组件 SKU",
                        "quantity": 1,
                        "pairing": None,
                    },
                ],
            }
        }

    payload = pairing.build_workbench_payload(
        product,
        rows,
        fetch_live_fn=fake_fetch_live,
    )

    assert payload["items"][0]["status"] == "combo_components_incomplete"
    assert payload["summary"]["ready_count"] == 0
    assert payload["summary"]["missing_count"] == 1


def test_build_workbench_payload_uses_mingkong_library_when_media_skus_missing(monkeypatch):
    product = {
        "id": 758,
        "product_code": "ultra-absorbent-miracle-cleaning-shammy-rjc",
        "purchase_1688_url": "",
    }
    library_row = {
        "shopify_product_id": "7540269842498",
        "shopify_variant_id": "43237580030146",
        "shopify_variant_title": "3 PCS",
        "dianxiaomi_sku": "43237580030146",
        "dianxiaomi_name": "清洁抹布 3件装",
        "image_url": "https://example.test/shammy.jpg",
        "purchase_1688_url": "https://detail.1688.com/offer/123456789.html",
        "source": "mingkong_library",
        "mingkong_procurement": {
            "supplier_name": "明空供应商",
            "pairing_state": 1,
        },
    }

    monkeypatch.setattr(
        pairing.mingkong_product_library,
        "sku_rows_from_library",
        lambda _product: [library_row],
    )
    monkeypatch.setattr(
        pairing.mingkong_product_library,
        "refresh_product_from_dxm02",
        lambda _product: (_ for _ in ()).throw(AssertionError("should not refresh")),
    )

    payload = pairing.build_workbench_payload(product, [], include_live=False)

    assert payload["summary"]["source"] == "mingkong_library"
    assert payload["summary"]["realtime_refresh"] is None
    assert payload["items"][0]["purchase_1688_url"] == "https://detail.1688.com/offer/123456789.html"
    assert payload["items"][0]["mingkong_procurement"]["supplier_name"] == "明空供应商"
    assert payload["items"][0]["image_url"] == "https://example.test/shammy.jpg"


def test_build_workbench_payload_realtime_refreshes_dxm02_on_library_miss(monkeypatch):
    product = {
        "id": 758,
        "product_code": "ultra-absorbent-miracle-cleaning-shammy-rjc",
        "purchase_1688_url": "",
    }
    library_row = {
        "shopify_product_id": "7540269842498",
        "shopify_variant_id": "43237580030146",
        "dianxiaomi_sku": "43237580030146",
        "purchase_1688_url": "https://detail.1688.com/offer/123456789.html",
        "source": "mingkong_library",
    }
    calls = {"library": 0, "refresh": 0}

    def fake_library(_product):
        calls["library"] += 1
        return [] if calls["library"] == 1 else [library_row]

    def fake_refresh(_product):
        calls["refresh"] += 1
        return {"products_seen": 1, "variants_seen": 1}

    monkeypatch.setattr(pairing.mingkong_product_library, "sku_rows_from_library", fake_library)
    monkeypatch.setattr(pairing.mingkong_product_library, "refresh_product_from_dxm02", fake_refresh)

    payload = pairing.build_workbench_payload(product, [], include_live=False)

    assert calls == {"library": 2, "refresh": 1}
    assert payload["summary"]["source"] == "mingkong_library"
    assert payload["summary"]["realtime_refresh"] == {"products_seen": 1, "variants_seen": 1}
    assert payload["items"][0]["dianxiaomi_sku"] == "43237580030146"


def test_build_workbench_payload_realtime_refreshes_when_library_has_no_replicable_sku(monkeypatch):
    product = {
        "id": 758,
        "product_code": "ultra-absorbent-miracle-cleaning-shammy-rjc",
        "purchase_1688_url": "",
    }
    calls = {"library": 0, "refresh": 0}

    def fake_library(_product):
        calls["library"] += 1
        if calls["library"] == 1:
            return [{
                "shopify_product_id": "7540269842498",
                "shopify_variant_id": "43237580030146",
                "shopify_variant_title": "3 PCS",
                "source": "shopify_public",
            }]
        return [{
            "shopify_product_id": "7540269842498",
            "shopify_variant_id": "43237580030146",
            "shopify_variant_title": "3 PCS",
            "dianxiaomi_sku": "43237580030146",
            "dianxiaomi_sku_code": "980001",
            "source": "mingkong_library",
        }]

    def fake_refresh(_product):
        calls["refresh"] += 1
        return {"products_seen": 1, "variants_seen": 1}

    monkeypatch.setattr(pairing.mingkong_product_library, "sku_rows_from_library", fake_library)
    monkeypatch.setattr(pairing.mingkong_product_library, "refresh_product_from_dxm02", fake_refresh)

    payload = pairing.build_workbench_payload(product, [], include_live=False)

    assert calls == {"library": 2, "refresh": 1}
    assert payload["summary"]["realtime_refresh"] == {"products_seen": 1, "variants_seen": 1}
    assert payload["items"][0]["dianxiaomi_sku"] == "43237580030146"


def test_build_workbench_payload_adds_mingkong_reference_when_enabled(monkeypatch):
    product = {
        "id": 747,
        "product_code": "sample-rjc",
        "purchase_1688_url": "",
    }
    local_row = {
        "shopify_variant_id": "variant-1",
        "shopify_variant_title": "Black",
        "dianxiaomi_sku": "0421-13260712",
        "source": "mingkong_library",
    }
    library_row = {
        "shopify_product_id": "8595642876077",
        "shopify_variant_id": "variant-1",
        "shopify_variant_title": "Black",
        "dianxiaomi_sku": "0421-13260712",
        "dianxiaomi_sku_code": "mk-erp-1",
        "dianxiaomi_name": "明空商品名",
        "source": "mingkong_library",
        "image_url": "https://example.test/mingkong-sku.jpg",
        "purchase_1688_url": "https://detail.1688.com/offer/123456789.html",
        "mingkong_procurement": {
            "supplier_name": "明空供应商",
            "alibaba_product_id": "123456789",
            "sku_id_alibaba": "sku-1688",
        },
    }

    monkeypatch.setattr(
        pairing.mingkong_product_library,
        "sku_rows_from_library",
        lambda _product: [library_row],
    )

    payload = pairing.build_workbench_payload(
        product,
        [local_row],
        include_live=False,
        include_mingkong_reference=True,
    )

    assert payload["summary"]["source"] == "media_product_skus"
    assert payload["items"][0]["mingkong"]["sku"] == "0421-13260712"
    assert payload["items"][0]["mingkong"]["image_url"] == "https://example.test/mingkong-sku.jpg"
    assert payload["items"][0]["mingkong"]["supplier_name"] == "明空供应商"
    assert payload["items"][0]["mingkong"]["sku_id_alibaba"] == "sku-1688"


def test_build_workbench_payload_uses_full_shopify_base_then_fills_mingkong(monkeypatch):
    product = {
        "id": 772,
        "product_code": "hygienic-silicone-back-scrub-rjc",
        "product_link": "https://example.test/products/hygienic-silicone-back-scrub-rjc",
    }
    existing_rows = [
        {
            "shopify_product_id": "old-mk-product",
            "shopify_variant_id": "old-mk-variant",
            "shopify_sku": "0422-14563244",
            "shopify_variant_title": "Old Blue",
            "dianxiaomi_sku": "0422-14563244",
            "dianxiaomi_sku_code": "98036085",
            "dianxiaomi_name": "旧本地蓝色",
        }
    ]
    full_base = [
        {
            "shopify_product_id": "our-shopify-product",
            "shopify_variant_id": "our-variant-blue",
            "shopify_sku": "0422-14563244",
            "shopify_variant_title": 'Blue / Standard (23.6") / 1 Pack (Single)',
            "source": "shopify_public",
        },
        {
            "shopify_product_id": "our-shopify-product",
            "shopify_variant_id": "our-variant-purple",
            "shopify_sku": "our-front-purple-sku",
            "shopify_variant_title": 'Purple / Standard (23.6") / 1 Pack (Single)',
            "source": "shopify_public",
        },
    ]
    mingkong_rows = [
        {
            "shopify_product_id": "mk-shopify-product",
            "shopify_variant_id": "mk-variant-purple",
            "shopify_sku": "mk-front-purple-sku",
            "shopify_variant_title": 'Purple / Standard (23.6") / 1 Pack (Single)',
            "dianxiaomi_sku": "0422-14563574",
            "dianxiaomi_sku_code": "98036091",
            "dianxiaomi_name": "明空紫色标准款",
            "source": "mingkong_library",
            "purchase_1688_url": "https://detail.1688.com/offer/921703756878.html",
            "mingkong_procurement": {
                "supplier_name": "明空供应商",
                "alibaba_product_id": "921703756878",
                "sku_id_alibaba": "1688-purple",
            },
        }
    ]

    monkeypatch.setattr(
        pairing.mingkong_product_library,
        "public_shopify_sku_rows_from_product",
        lambda _product: full_base,
    )
    monkeypatch.setattr(
        pairing.mingkong_product_library,
        "sku_rows_from_library",
        lambda _product: mingkong_rows,
    )

    payload = pairing.build_workbench_payload(
        product,
        existing_rows,
        include_live=False,
        include_mingkong_reference=True,
    )

    assert payload["summary"]["source"] == "shopify_public_base"
    assert [item["shopify_variant_id"] for item in payload["items"]] == [
        "our-variant-blue",
        "our-variant-purple",
    ]
    assert payload["items"][0]["dianxiaomi_sku"] == "0422-14563244"
    assert payload["items"][0]["dianxiaomi_sku_code"] == "98036085"
    assert payload["items"][1]["shopify_sku"] == "our-front-purple-sku"
    assert payload["items"][1]["dianxiaomi_sku"] == "0422-14563574"
    assert payload["items"][1]["dianxiaomi_sku_code"] == "98036091"
    assert payload["items"][1]["mingkong"]["sku_id_alibaba"] == "1688-purple"


def test_full_shopify_base_prefers_informative_mingkong_title_match_over_empty_variant_match(monkeypatch):
    product = {
        "id": 769,
        "product_code": "wall-repair-cream-rjc",
        "product_link": "https://example.test/products/wall-repair-cream-rjc",
    }
    full_base = [
        {
            "shopify_product_id": "our-shopify-product",
            "shopify_variant_id": "empty-mk-variant",
            "shopify_variant_title": "1x Wall Repair Cream",
            "source": "shopify_public",
        }
    ]
    mingkong_rows = [
        {
            "shopify_product_id": "empty-mk-product",
            "shopify_variant_id": "empty-mk-variant",
            "shopify_variant_title": "1x Wall Repair Cream",
            "source": "mingkong_library",
        },
        {
            "shopify_product_id": "configured-mk-product",
            "shopify_variant_id": "configured-mk-variant",
            "shopify_variant_title": "1x Wall Repair Cream",
            "dianxiaomi_sku": "49211474542878",
            "dianxiaomi_sku_code": "98008768",
            "dianxiaomi_name": "补墙膏 1 件",
            "source": "mingkong_library",
            "purchase_1688_url": "https://detail.1688.com/offer/624802943115.html",
            "mingkong_procurement": {
                "alibaba_product_id": "624802943115",
                "sku_id_alibaba": "1688-sku-1",
            },
        },
    ]

    monkeypatch.setattr(
        pairing.mingkong_product_library,
        "public_shopify_sku_rows_from_product",
        lambda _product: full_base,
    )
    monkeypatch.setattr(
        pairing.mingkong_product_library,
        "sku_rows_from_library",
        lambda _product: mingkong_rows,
    )

    payload = pairing.build_workbench_payload(
        product,
        [],
        include_live=False,
        include_mingkong_reference=True,
    )

    assert payload["items"][0]["shopify_variant_id"] == "empty-mk-variant"
    assert payload["items"][0]["dianxiaomi_sku"] == "49211474542878"
    assert payload["items"][0]["dianxiaomi_sku_code"] == "98008768"
    assert payload["items"][0]["purchase_1688_url"] == "https://detail.1688.com/offer/624802943115.html"
    assert payload["items"][0]["mingkong"]["sku_id_alibaba"] == "1688-sku-1"


def test_build_mingkong_library_sku_import_payload_refreshes_and_converts(monkeypatch):
    product = {"id": 747, "product_code": "sample-rjc", "shopifyid": "shopify-product"}
    calls = {"library": 0, "refresh": 0}

    def fake_library(_product):
        calls["library"] += 1
        if calls["library"] == 1:
            return []
        return [
            {
                "shopify_product_id": "mk-shopify-product",
                "shopify_variant_id": "variant-1",
                "shopify_variant_title": "Black",
                "shopify_sku": "shopify-sku",
                "dianxiaomi_sku": "0421-13260712",
                "dianxiaomi_product_sku": "mk-product-sku",
                "dianxiaomi_sku_code": "mk-erp-1",
                "dianxiaomi_name": "明空商品名",
            }
        ]

    def fake_refresh(_product):
        calls["refresh"] += 1
        return {"products_seen": 1, "variants_seen": 1}

    monkeypatch.setattr(pairing.mingkong_product_library, "sku_rows_from_library", fake_library)
    monkeypatch.setattr(pairing.mingkong_product_library, "refresh_product_from_dxm02", fake_refresh)

    payload = pairing.build_mingkong_library_sku_import_payload(product)

    assert calls == {"library": 2, "refresh": 1}
    assert payload["ok"] is True
    assert payload["realtime_refresh"] == {"products_seen": 1, "variants_seen": 1}
    assert payload["pairs"][0]["shopify_variant_id"] == "variant-1"
    assert payload["pairs"][0]["dianxiaomi_sku"] == "0421-13260712"
    assert payload["pairs"][0]["dianxiaomi_sku_code"] == "mk-erp-1"


def test_build_target_sku_import_pairs_uses_editable_target_values():
    product = {"id": 747, "product_code": "sample-rjc", "shopifyid": "shopify-product"}
    library_items = [
        {
            "shopify_product_id": "mk-shopify-product",
            "shopify_variant_id": "variant-1",
            "shopify_variant_title": "Black / S",
            "shopify_sku": "shopify-sku",
            "dianxiaomi_sku": "mk-sku-old",
            "dianxiaomi_product_sku": "mk-product-sku",
            "dianxiaomi_sku_code": "mk-erp-old",
            "dianxiaomi_name": "明空原商品名",
            "purchase_1688_url": "https://detail.1688.com/offer/123456789.html",
        }
    ]
    targets = [
        {
            "shopify_variant_id": "variant-1",
            "variant_title": "Black / S edited",
            "dianxiaomi_sku": "mk-sku-edited",
            "dianxiaomi_product_sku": "mk-product-sku-edited",
            "dianxiaomi_sku_code": "mk-erp-edited",
            "dianxiaomi_name": "人工确认商品名",
            "purchase_1688_url": "https://detail.1688.com/offer/987654321.html",
            "product_id_alibaba": "987654321",
            "sku_id_alibaba": "1688-sku-edited",
        }
    ]

    pairs = pairing.build_target_sku_import_pairs(product, library_items, targets)
    purchase_url = pairing.first_purchase_url_from_targets(product, library_items, targets)

    assert pairs == [
        {
            "shopify_product_id": "mk-shopify-product",
            "shopify_variant_id": "variant-1",
            "shopify_sku": "shopify-sku",
            "shopify_price": None,
            "shopify_compare_at_price": None,
            "shopify_currency": "USD",
            "shopify_inventory_quantity": None,
            "shopify_weight_grams": None,
            "shopify_variant_title": "Black / S edited",
            "dianxiaomi_sku": "mk-sku-edited",
            "dianxiaomi_product_sku": "mk-product-sku-edited",
            "dianxiaomi_sku_code": "mk-erp-edited",
            "dianxiaomi_name": "人工确认商品名",
        }
    ]
    assert purchase_url == "https://detail.1688.com/offer/987654321.html"


def test_build_target_sku_import_pairs_keeps_repeated_skus_for_distinct_variants():
    product = {"id": 772, "product_code": "hygienic-silicone-back-scrub-rjc"}
    library_items = [
        {
            "shopify_product_id": "shop-a",
            "shopify_variant_id": "variant-a",
            "shopify_variant_title": "Blue",
            "dianxiaomi_sku": "0422-14563244",
            "dianxiaomi_sku_code": "98036085",
            "dianxiaomi_name": "硅胶搓背巾 蓝色",
        },
        {
            "shopify_product_id": "shop-b",
            "shopify_variant_id": "variant-b",
            "shopify_variant_title": "Blue",
            "dianxiaomi_sku": "0422-14563244",
            "dianxiaomi_sku_code": "98036085",
            "dianxiaomi_name": "硅胶搓背巾 蓝色",
        },
    ]
    targets = [
        {
            "shopify_product_id": "shop-a",
            "shopify_variant_id": "variant-a",
            "variant_title": "Blue",
            "dianxiaomi_sku": "0422-14563244",
            "dianxiaomi_sku_code": "98036085",
        },
        {
            "shopify_product_id": "shop-b",
            "shopify_variant_id": "variant-b",
            "variant_title": "Blue",
            "dianxiaomi_sku": "0422-14563244",
            "dianxiaomi_sku_code": "98036085",
        },
    ]

    pairs = pairing.build_target_sku_import_pairs(product, library_items, targets)

    assert len(pairs) == 2
    assert pairs[0]["shopify_product_id"] == "shop-a"
    assert pairs[0]["shopify_variant_id"] == "variant-a"
    assert pairs[0]["dianxiaomi_sku"] == "0422-14563244"
    assert pairs[1]["shopify_product_id"] == "shop-b"
    assert pairs[1]["shopify_variant_id"] == "variant-b"
    assert pairs[1]["dianxiaomi_sku"] == "0422-14563244"


def test_mingkong_pairing_ai_review_uses_openrouter_use_case_with_images():
    from appcore import mingkong_pairing_ai
    from appcore.llm_use_cases import get_use_case

    use_case = get_use_case("mingkong_pairing.match_candidate")
    assert use_case["default_provider"] == "openrouter"
    assert use_case["default_model"] == "google/gemini-3-flash-preview"

    captured = {}

    def fake_invoke(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {
            "json": {
                "is_same_product": True,
                "confidence": 0.91,
                "recommended_candidate_key": "shop-1",
                "requires_manual_review": False,
                "reason": "标题和 SKU 规格一致",
                "risks": [],
                "variant_mapping_notes": "5 个 SKU 一一对应",
                "candidate_rankings": [
                    {
                        "candidate_key": "shop-1",
                        "score": 0.91,
                        "reason": "采购配对完整",
                        "matched_sku_count": 5,
                        "risks": [],
                    }
                ],
            },
            "usage_log_id": 99,
        }

    result = mingkong_pairing_ai.review_pairing_candidates(
        {
            "id": 772,
            "product_code": "hygienic-silicone-back-scrub-rjc",
            "name": "硅胶搓澡巾",
            "shopify_title": "Hygienic Silicone Back Scrub",
            "main_image": "https://example.test/product.jpg",
        },
        [
            {
                "shopify_product_id": "shop-1",
                "shopify_variant_id": "variant-1",
                "variant_title": "Blue",
                "dianxiaomi_sku": "0422-14563244",
                "image_url": "https://example.test/sku.jpg",
                "mingkong": {
                    "shopify_product_id": "shop-1",
                    "sku": "0422-14563244",
                    "image_url": "https://example.test/sku.jpg",
                    "purchase_1688_url": "https://detail.1688.com/offer/921703756878.html",
                },
            }
        ],
        user_id=7,
        invoke_chat_fn=fake_invoke,
    )

    assert result["ok"] is True
    assert result["usage_log_id"] == 99
    assert captured["use_case_code"] == "mingkong_pairing.match_candidate"
    assert captured["kwargs"]["user_id"] == 7
    assert captured["kwargs"]["temperature"] == 0.1
    assert captured["kwargs"]["response_format"]["type"] == "json_schema"
    content = captured["kwargs"]["messages"][1]["content"]
    assert any(part.get("type") == "image_url" for part in content)
    assert "Hygienic Silicone Back Scrub" in content[0]["text"]


def test_mingkong_pairing_ai_review_route_uses_posted_workbench_items(monkeypatch):
    product = {"id": 772, "product_code": "sample-rjc"}
    items = [{"shopify_variant_id": "variant-1", "dianxiaomi_sku": "sku-1"}]
    calls = {}

    def fake_review(product_arg, items_arg, *, user_id):
        calls["product"] = product_arg
        calls["items"] = items_arg
        calls["user_id"] = user_id
        return {"ok": True, "review": {"confidence": 0.9}, "logs": []}

    monkeypatch.setattr(products_route.mingkong_pairing_ai, "review_pairing_candidates", fake_review)

    result = products_route._build_mingkong_pairing_ai_review_response(
        772,
        product,
        {"workbench_items": items},
        user_id=8,
    )

    assert result["ok"] is True
    assert calls == {"product": product, "items": items, "user_id": 8}


def test_mingkong_pairing_import_skus_refuses_to_overwrite_existing(monkeypatch):
    monkeypatch.setattr(
        products_route.medias,
        "list_product_skus",
        lambda _pid: [{"id": 1, "shopify_variant_id": "variant-1"}],
    )
    monkeypatch.setattr(
        products_route.medias,
        "replace_product_skus",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not write")),
    )

    result = products_route._build_mingkong_pairing_import_skus_response(747, {"id": 747})

    assert result["ok"] is False
    assert result["error"] == "local_skus_exist"
    assert result["logs"][0]["level"] == "warn"


def test_mingkong_pairing_import_skus_writes_mingkong_library_source(monkeypatch):
    product = {"id": 747, "product_code": "sample-rjc"}
    imported_rows = [
        {
            "id": 10,
            "shopify_variant_id": "variant-1",
            "dianxiaomi_sku": "0421-13260712",
            "source": "mingkong_library",
        }
    ]
    calls = {"list": 0, "replace": None}

    def fake_list(_pid):
        calls["list"] += 1
        return [] if calls["list"] == 1 else imported_rows

    def fake_replace(pid, pairs, *, source):
        calls["replace"] = (pid, pairs, source)
        return {"inserted": 1, "updated": 0, "deleted": 0, "preserved": 0}

    monkeypatch.setattr(products_route.medias, "list_product_skus", fake_list)
    monkeypatch.setattr(products_route.medias, "replace_product_skus", fake_replace)
    monkeypatch.setattr(
        products_route.dianxiaomi_mingkong_pairing,
        "build_mingkong_library_sku_import_payload",
        lambda _product: {
            "ok": True,
            "pairs": [{"shopify_variant_id": "variant-1", "dianxiaomi_sku": "0421-13260712"}],
            "items": [],
            "realtime_refresh": None,
        },
    )

    result = products_route._build_mingkong_pairing_import_skus_response(747, product)

    assert result["ok"] is True
    assert calls["replace"][0] == 747
    assert calls["replace"][2] == "mingkong_library"
    assert result["items"] == imported_rows
    assert result["logs"][1]["level"] == "ok"


def test_mingkong_pairing_sync_response_imports_targets_then_replicates_and_confirms(monkeypatch):
    product = {"id": 747, "product_code": "sample-rjc", "shopifyid": "shopify-product"}
    library_items = [
        {
            "shopify_product_id": "shopify-product",
            "shopify_variant_id": "variant-1",
            "shopify_variant_title": "Black",
            "dianxiaomi_sku": "mk-sku",
            "dianxiaomi_sku_code": "mk-erp",
            "dianxiaomi_name": "明空商品名",
            "purchase_1688_url": "https://detail.1688.com/offer/123456789.html",
        }
    ]
    targets = [
        {
            "shopify_variant_id": "variant-1",
            "variant_title": "Black",
            "dianxiaomi_sku": "mk-sku",
            "dianxiaomi_sku_code": "mk-erp",
            "dianxiaomi_name": "明空商品名",
            "product_id_alibaba": "123456789",
            "sku_id_alibaba": "1688-sku",
        }
    ]
    calls = {"replace": [], "update": [], "replicate": None, "confirm": None, "yuncang": None}
    local_rows = [
        {
            "shopify_product_id": "shopify-product",
            "shopify_variant_id": "variant-1",
            "shopify_variant_title": "Black",
            "dianxiaomi_sku": "mk-sku",
            "dianxiaomi_sku_code": "mk-erp",
            "dianxiaomi_name": "明空商品名",
        }
    ]

    monkeypatch.setattr(
        products_route.dianxiaomi_mingkong_pairing,
        "build_mingkong_library_sku_import_payload",
        lambda _product: {
            "ok": True,
            "pairs": [],
            "items": library_items,
            "realtime_refresh": None,
        },
    )
    monkeypatch.setattr(
        products_route.medias,
        "replace_product_skus",
        lambda pid, pairs, *, source: calls["replace"].append((pid, pairs, source))
        or {"inserted": 1, "updated": 0, "deleted": 0, "preserved": 0},
    )
    monkeypatch.setattr(
        products_route.medias,
        "update_product",
        lambda pid, **fields: calls["update"].append((pid, fields)) or 1,
    )
    monkeypatch.setattr(products_route.medias, "get_product", lambda _pid: product)
    monkeypatch.setattr(products_route.medias, "list_product_skus", lambda _pid: local_rows)

    def fake_replicate(product_arg, rows_arg, **kwargs):
        calls["replicate"] = (product_arg, rows_arg, kwargs)
        return {
            "ok": True,
            "message": "复刻完成",
            "logs": [{"level": "ok", "message": "复刻完成"}],
            "items": [{"status": "already_exists", "dianxiaomi_sku": "mk-sku"}],
        }

    def fake_confirm(product_arg, rows_arg, **kwargs):
        calls["confirm"] = (product_arg, rows_arg, kwargs)
        return {
            "ok": True,
            "message": "确认完成",
            "logs": [{"level": "ok", "message": "确认完成"}],
            "items": [{"status": "confirmed", "dianxiaomi_sku": "mk-sku"}],
        }

    monkeypatch.setattr(
        products_route.dianxiaomi_mingkong_pairing,
        "replicate_mingkong_skus_to_dxm03",
        fake_replicate,
    )
    monkeypatch.setattr(
        products_route.dianxiaomi_mingkong_pairing,
        "confirm_dxm03_pairing",
        fake_confirm,
    )

    def fake_yuncang(product_arg, rows_arg, **kwargs):
        calls["yuncang"] = (product_arg, rows_arg, kwargs)
        return {
            "ok": True,
            "message": "云仓完成",
            "logs": [{"level": "ok", "message": "云仓完成"}],
            "items": [{"status": "already_exists", "sku": "mk-sku"}],
        }

    monkeypatch.setattr(
        products_route.dianxiaomi_yuncang,
        "add_product_skus_to_yuncang",
        fake_yuncang,
    )

    result = products_route._build_mingkong_pairing_sync_response(
        747,
        product,
        {"items": targets},
    )

    assert result["ok"] is True
    assert calls["replace"][0][0] == 747
    assert calls["replace"][0][1][0]["dianxiaomi_sku"] == "mk-sku"
    assert calls["replace"][0][2] == "mingkong_replicated"
    assert calls["update"] == [
        (747, {"purchase_1688_url": "https://detail.1688.com/offer/123456789.html"})
    ]
    assert calls["replicate"][2]["selections"] == targets
    assert calls["confirm"][2]["selections"] == targets
    assert calls["yuncang"][2]["pairing_items"][0]["dianxiaomi_sku"] == "mk-sku"
    assert "确认完成" in result["message"]
    assert result["yuncang"]["message"] == "云仓完成"


def test_mingkong_pairing_sync_runs_yuncang_for_successful_items_when_confirm_partially_blocks(monkeypatch):
    product = {"id": 747, "product_code": "sample-rjc", "shopifyid": "shopify-product"}
    library_items = [
        {
            "shopify_product_id": "shopify-product",
            "shopify_variant_id": "variant-1",
            "dianxiaomi_sku": "mk-sku-ok",
            "dianxiaomi_sku_code": "mk-erp-ok",
        },
        {
            "shopify_product_id": "shopify-product",
            "shopify_variant_id": "variant-2",
            "dianxiaomi_sku": "mk-sku-blocked",
            "dianxiaomi_sku_code": "mk-erp-blocked",
        },
    ]
    calls = {"yuncang": None}

    monkeypatch.setattr(
        products_route.dianxiaomi_mingkong_pairing,
        "build_mingkong_library_sku_import_payload",
        lambda _product: {
            "ok": True,
            "pairs": [],
            "items": library_items,
            "realtime_refresh": None,
        },
    )
    monkeypatch.setattr(
        products_route.medias,
        "replace_product_skus",
        lambda *_args, **_kwargs: {"inserted": 0, "updated": 2, "deleted": 0, "preserved": 0},
    )
    monkeypatch.setattr(products_route.medias, "update_product", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(products_route.medias, "get_product", lambda _pid: product)
    monkeypatch.setattr(products_route.medias, "list_product_skus", lambda _pid: library_items)
    monkeypatch.setattr(
        products_route.dianxiaomi_mingkong_pairing,
        "replicate_mingkong_skus_to_dxm03",
        lambda *_args, **_kwargs: {
            "ok": True,
            "message": "复刻完成",
            "logs": [],
            "items": [{"status": "already_exists", "dianxiaomi_sku": "mk-sku-ok"}],
        },
    )
    monkeypatch.setattr(
        products_route.dianxiaomi_mingkong_pairing,
        "confirm_dxm03_pairing",
        lambda *_args, **_kwargs: {
            "ok": False,
            "message": "采购配对存在阻断",
            "logs": [],
            "items": [
                {"status": "already_paired", "dianxiaomi_sku": "mk-sku-ok"},
                {"status": "blocked", "dianxiaomi_sku": "mk-sku-blocked"},
            ],
        },
    )

    def fake_yuncang(product_arg, rows_arg, **kwargs):
        calls["yuncang"] = (product_arg, rows_arg, kwargs)
        return {
            "ok": True,
            "message": "云仓完成",
            "logs": [],
            "items": [{"status": "already_exists", "sku": "mk-sku-ok"}],
        }

    monkeypatch.setattr(
        products_route.dianxiaomi_yuncang,
        "add_product_skus_to_yuncang",
        fake_yuncang,
    )

    result = products_route._build_mingkong_pairing_sync_response(
        747,
        product,
        {"items": library_items},
    )

    assert result["ok"] is False
    assert "已对可用 SKU 执行云仓" in result["message"]
    assert calls["yuncang"][2]["pairing_items"] == [
        {"status": "already_paired", "dianxiaomi_sku": "mk-sku-ok"}
    ]
    assert result["yuncang"]["message"] == "云仓完成"


def test_mingkong_pairing_template_has_review_modal_and_single_sync_entry():
    source = Path("web/templates/medias_mingkong_pairing_workbench.html").read_text(
        encoding="utf-8"
    )

    assert 'id="mkpProgressModal"' in source
    assert 'id="mkpSyncModal"' in source
    assert "同步明空店小秘SKU" in source
    assert "执行后的目标效果" in source
    assert "明空 SKU 图片" in source
    assert 'id="mkpImportSkus"' not in source
    assert 'id="mkpReplicate"' not in source
    assert "/mingkong-pairing/sync" in source
    assert "AI辅助判断" in source
    assert "/mingkong-pairing/ai-review" in source
    assert "non_json_response" in source


def test_mingkong_pairing_action_error_payload_is_json_readable():
    payload = products_route._mingkong_pairing_action_error_payload(
        "复刻明空 SKU",
        RuntimeError("Playwright Sync API inside the asyncio loop"),
    )

    assert payload["ok"] is False
    assert payload["error"] == "mingkong_pairing_internal_error"
    assert "复刻明空 SKU 失败" in payload["message"]
    assert payload["logs"][1]["level"] == "error"


def test_replicate_mingkong_sku_uses_subprocess_by_default(monkeypatch):
    calls = {}
    product = {"id": 736, "product_code": "sample-rjc"}
    sku_rows = [{"dianxiaomi_sku": "sku-1"}]
    selections = [{"dianxiaomi_sku": "sku-1", "product_id_alibaba": "1688-product"}]

    def fake_subprocess(operation, payload):
        calls["operation"] = operation
        calls["payload"] = payload
        return {"ok": True, "logs": [{"level": "ok", "message": "done"}], "items": []}

    def fail_impl(*_args, **_kwargs):
        raise AssertionError("default replicate path should run in a subprocess")

    monkeypatch.setattr(pairing, "_run_pairing_subprocess", fake_subprocess)
    monkeypatch.setattr(pairing, "_replicate_mingkong_skus_to_dxm03_impl", fail_impl)

    result = pairing.replicate_mingkong_skus_to_dxm03(
        product,
        sku_rows,
        selections=selections,
    )

    assert result["ok"] is True
    assert calls["operation"] == "replicate"
    assert calls["payload"]["product"] == product
    assert calls["payload"]["sku_rows"] == sku_rows
    assert calls["payload"]["selections"] == selections


def test_pairing_subprocess_serializes_decimal_payload(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        captured["input"] = kwargs["input"]
        output_path = command[command.index("--output") + 1]
        Path(output_path).write_text(
            json.dumps({"ok": True, "result": {"ok": True}}),
            encoding="utf-8",
        )
        return Completed()

    monkeypatch.setattr(pairing.subprocess, "run", fake_run)

    result = pairing._run_pairing_subprocess(
        "replicate",
        {"product": {"id": 736, "price": Decimal("12.34")}},
    )

    assert result["ok"] is True
    assert json.loads(captured["input"])["product"]["price"] == "12.34"


def test_replicate_mingkong_sku_runs_impl_on_isolated_thread_when_forced(monkeypatch):
    thread_names = []

    def fake_impl(product, sku_rows, **kwargs):
        thread_names.append(threading.current_thread().name)
        assert product["id"] == 736
        assert sku_rows[0]["dianxiaomi_sku"] == "sku-1"
        assert kwargs["selections"] == [{"dianxiaomi_sku": "sku-1"}]
        return {"ok": True, "logs": [{"level": "ok", "message": "done"}]}

    monkeypatch.setattr(pairing, "_replicate_mingkong_skus_to_dxm03_impl", fake_impl)

    result = pairing.replicate_mingkong_skus_to_dxm03(
        {"id": 736, "product_code": "sample-rjc"},
        [{"dianxiaomi_sku": "sku-1"}],
        selections=[{"dianxiaomi_sku": "sku-1"}],
        force_isolated_thread=True,
    )

    assert result["ok"] is True
    assert thread_names
    assert thread_names[0].startswith("mingkong-dxm-cdp")


def test_playwright_operation_isolates_by_default():
    thread_names = []

    result = pairing._run_playwright_operation(
        "test_mingkong_default_isolation",
        lambda: thread_names.append(threading.current_thread().name) or "ok",
    )

    assert result == "ok"
    assert thread_names
    assert thread_names[0].startswith("mingkong-dxm-cdp")


def test_playwright_operation_auto_isolates_inside_running_asyncio_loop():
    thread_names = []

    async def run_operation():
        return pairing._run_playwright_operation(
            "test_mingkong_asyncio_loop",
            lambda: thread_names.append(threading.current_thread().name) or "ok",
        )

    assert asyncio.run(run_operation()) == "ok"
    assert thread_names
    assert thread_names[0].startswith("mingkong-dxm-cdp")


def test_confirm_dxm03_pairing_allows_combo_without_outer_purchase_url(monkeypatch):
    @contextmanager
    def fake_lock(**_kwargs):
        yield

    monkeypatch.setattr(pairing, "browser_automation_lock", fake_lock)
    monkeypatch.setattr(pairing, "_open_dxm03_context", lambda _url: ("pw", "browser", "ctx"))
    monkeypatch.setattr(pairing, "_close_dxm03_context", lambda _playwright, _browser: None)
    monkeypatch.setattr(
        pairing,
        "_search_commodity",
        lambda _ctx, _sku: {"id": "combo-product", "is_combo": True},
    )
    monkeypatch.setattr(
        pairing,
        "_search_child_sku_info",
        lambda _ctx, _product_id: [{"sku": "child-sku", "quantity": 1}],
    )
    monkeypatch.setattr(
        pairing,
        "_search_pair",
        lambda _ctx, sku: {"is_paired": True, "sku_id_alibaba": "1688-sku"}
        if sku == "child-sku"
        else None,
    )

    result = pairing.confirm_dxm03_pairing(
        {"id": 1, "product_code": "combo-rjc", "purchase_1688_url": ""},
        [{"dianxiaomi_sku": "combo-sku"}],
    )

    assert result["ok"] is True
    assert result["items"][0]["status"] == "already_paired_combo_components"
    assert result["logs"][0]["level"] == "info"
    assert result["summary"]["already_paired_count"] == 1


def test_confirm_dxm03_pairing_blocks_single_sku_without_purchase_url(monkeypatch):
    @contextmanager
    def fake_lock(**_kwargs):
        yield

    monkeypatch.setattr(pairing, "browser_automation_lock", fake_lock)
    monkeypatch.setattr(pairing, "_open_dxm03_context", lambda _url: ("pw", "browser", "ctx"))
    monkeypatch.setattr(pairing, "_close_dxm03_context", lambda _playwright, _browser: None)
    monkeypatch.setattr(
        pairing,
        "_search_commodity",
        lambda _ctx, _sku: {"id": "single-product", "is_combo": False},
    )
    monkeypatch.setattr(pairing, "_search_pair", lambda _ctx, _sku: None)

    result = pairing.confirm_dxm03_pairing(
        {"id": 1, "product_code": "sample-rjc", "purchase_1688_url": ""},
        [{"dianxiaomi_sku": "sku-1"}],
    )

    assert result["ok"] is False
    assert result["items"][0]["error"] == "missing_purchase_url"
    assert result["logs"][1]["level"] == "warn"


def test_confirm_dxm03_pairing_preserves_existing_configured_single_sku(monkeypatch):
    @contextmanager
    def fake_lock(**_kwargs):
        yield

    monkeypatch.setattr(pairing, "browser_automation_lock", fake_lock)
    monkeypatch.setattr(pairing, "_open_dxm03_context", lambda _url: ("pw", "browser", "ctx"))
    monkeypatch.setattr(pairing, "_close_dxm03_context", lambda _playwright, _browser: None)
    monkeypatch.setattr(
        pairing,
        "_search_commodity",
        lambda _ctx, _sku: {
            "id": "single-product",
            "is_combo": False,
            "source_url": "https://detail.1688.com/offer/old.html",
        },
    )
    monkeypatch.setattr(
        pairing,
        "_search_pair",
        lambda _ctx, _sku: {
            "pair_row_id": "pair-row",
            "is_paired": True,
            "alibaba_product_id": "old",
            "sku_id_alibaba": "old-sku-id",
        },
    )
    monkeypatch.setattr(
        pairing,
        "_update_source_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sourceUrl must stay unchanged")),
    )
    monkeypatch.setattr(
        pairing,
        "_check_pair",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("existing pair must not be checked")),
    )
    monkeypatch.setattr(
        pairing,
        "_confirm_pair",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("existing pair must not be overwritten")),
    )

    result = pairing.confirm_dxm03_pairing(
        {"id": 1, "product_code": "sample-rjc", "purchase_1688_url": ""},
        [{
            "shopify_variant_id": "variant-1",
            "dianxiaomi_sku": "sku-1",
            "purchase_1688_url": "https://detail.1688.com/offer/new.html",
        }],
        selections=[{
            "shopify_variant_id": "variant-1",
            "dianxiaomi_sku": "sku-1",
            "purchase_1688_url": "https://detail.1688.com/offer/new.html",
            "product_id_alibaba": "new",
            "sku_id_alibaba": "new-sku-id",
        }],
    )

    assert result["ok"] is True
    assert result["items"][0]["status"] == "already_configured_preserved"
    assert result["items"][0]["sku_id_alibaba"] == "old-sku-id"
    assert result["summary"]["already_paired_count"] == 1
    assert result["logs"][1]["level"] == "ok"


def test_confirm_dxm03_pairing_can_overwrite_existing_configured_single_sku(monkeypatch):
    calls = {"update": [], "check": [], "confirm": [], "search_pair": 0}

    @contextmanager
    def fake_lock(**_kwargs):
        yield

    def fake_search_pair(_ctx, _sku):
        calls["search_pair"] += 1
        if calls["search_pair"] == 1:
            return {
                "pair_row_id": "pair-row",
                "is_paired": True,
                "alibaba_product_id": "123",
                "sku_id_alibaba": "old-sku-id",
            }
        return {
            "pair_row_id": "pair-row",
            "is_paired": True,
            "alibaba_product_id": "456",
            "sku_id_alibaba": "new-sku-id",
        }

    monkeypatch.setattr(pairing, "browser_automation_lock", fake_lock)
    monkeypatch.setattr(pairing, "_open_dxm03_context", lambda _url: ("pw", "browser", "ctx"))
    monkeypatch.setattr(pairing, "_close_dxm03_context", lambda _playwright, _browser: None)
    monkeypatch.setattr(
        pairing,
        "_search_commodity",
        lambda _ctx, _sku: {
            "id": "single-product",
            "is_combo": False,
            "source_url": "https://detail.1688.com/offer/old.html",
        },
    )
    monkeypatch.setattr(pairing, "_search_pair", fake_search_pair)
    monkeypatch.setattr(
        pairing,
        "_update_source_url",
        lambda _ctx, commodity_id, purchase_url: calls["update"].append((commodity_id, purchase_url)) or {},
    )
    monkeypatch.setattr(
        pairing,
        "_check_pair",
        lambda _ctx, product_id, sku_id: calls["check"].append((product_id, sku_id)) or {},
    )
    monkeypatch.setattr(
        pairing,
        "_confirm_pair",
        lambda _ctx, **kwargs: calls["confirm"].append(kwargs) or {},
    )

    result = pairing.confirm_dxm03_pairing(
        {"id": 1, "product_code": "sample-rjc", "purchase_1688_url": ""},
        [{
            "shopify_variant_id": "variant-1",
            "dianxiaomi_sku": "sku-1",
            "purchase_1688_url": "https://detail.1688.com/offer/456.html",
        }],
        selections=[{
            "shopify_variant_id": "variant-1",
            "dianxiaomi_sku": "sku-1",
            "purchase_1688_url": "https://detail.1688.com/offer/456.html",
            "product_id_alibaba": "456",
            "sku_id_alibaba": "new-sku-id",
        }],
        preserve_existing_pairing=False,
    )

    assert result["ok"] is True
    assert result["items"][0]["status"] == "confirmed"
    assert calls["update"] == [("single-product", "https://detail.1688.com/offer/456.html")]
    assert calls["check"] == [("456", "new-sku-id")]
    assert calls["confirm"] == [{
        "pair_row_id": "pair-row",
        "product_id_alibaba": "456",
        "sku_id_alibaba": "new-sku-id",
    }]


def test_replicated_commodity_form_clears_account_bound_fields():
    detail = {
        "productDTO": {
            "dxmCommodityProduct": {
                "id": "mk-product",
                "idStr": "mk-product",
                "puid": "mk-account",
                "parentId": "mk-parent",
                "developmentId": "mk-dev",
                "fullCid": "1292232-",
                "name": "驼背矫正器-黑色S",
                "nameEn": "Posture Corrector Black S",
                "sku": "50853279039762",
                "skuCode": "98012311",
                "price": 11,
                "weight": 146,
                "sourceUrl": "https://shop.tiktok.com/view/product/1730",
                "groupState": 0,
                "productType": "100",
                "allowWeightError": 0,
                "packageWeight": 12,
            },
            "dxmProductCustoms": {
                "id": "customs-id",
                "productId": "mk-product",
                "puid": "mk-account",
                "nameCn": "驼背矫正器",
                "nameEn": "Posture Corrector",
                "price": 5,
                "weight": 146,
            },
            "dxmProductPacks": [
                {
                    "id": "pack-row",
                    "productId": "mk-product",
                    "puid": "mk-account",
                    "packId": "pack-1",
                    "quantity": 2,
                    "packName": "气泡袋",
                }
            ],
            "dxmWarehouseProductList": [
                {"warehoseId": "mk-warehouse", "supplierId": "mk-supplier"}
            ],
            "supplierProductRelationMapList": [
                {"supplierId": "mk-supplier", "supplierName": "明空供应商"}
            ],
        }
    }

    payload = pairing._replicated_commodity_form(
        detail,
        target_sku="50853279039762",
        target_sku_code="98012311",
        purchase_url="https://detail.1688.com/offer/922648495856.html",
    )

    form = json.loads(payload["obj"])
    product = json.loads(form["dxmCommodityProduct"])
    customs = json.loads(form["dxmProductCustoms"])

    assert payload["shopId"] == "-1"
    assert payload["pt"] == "-1"
    assert payload["orderWarehoseId"] == "-1"
    assert payload["orderCount"] == "0"
    assert product["sku"] == "50853279039762"
    assert product["skuCode"] == "98012311"
    assert product["fullCid"] == pairing.DEFAULT_DXM03_FULL_CID
    assert product["sourceUrl"] == "https://detail.1688.com/offer/922648495856.html"
    assert "id" not in product
    assert "puid" not in product
    assert "parentId" not in product
    assert "developmentId" not in product
    assert "id" not in customs
    assert "productId" not in customs
    assert "puid" not in customs
    assert customs["nameCnBg"] == "驼背矫正器"
    assert customs["nameEnBg"] == "Posture Corrector"
    assert customs["priceBg"] == 5
    assert customs["weightBg"] == 146
    packs = json.loads(form["dxmProductPacks"])
    assert packs == [{"packId": "pack-1", "quantity": 2, "packName": "气泡袋"}]
    assert json.loads(form["dxmWarehouseProductList"]) == []
    assert json.loads(form["supplierProductRelationMapList"]) == []


def test_target_sku_code_renames_only_when_code_conflicts(monkeypatch):
    seen = []

    def fake_search(_ctx, sku_code):
        seen.append(sku_code)
        if sku_code in {"98012311", "98012311-MK"}:
            return {"sku": "OTHER-SKU", "sku_code": sku_code}
        return None

    monkeypatch.setattr(pairing, "_search_commodity_by_sku_code", fake_search)

    final_code, strategy = pairing._target_sku_code(
        "ctx",
        "98012311",
        target_sku="50853279039762",
    )

    assert final_code == "98012311-MK2"
    assert strategy == "renamed"
    assert seen == ["98012311", "98012311-MK", "98012311-MK2"]


def test_replicate_mingkong_sku_reuses_existing_dxm03_commodity(monkeypatch):
    @contextmanager
    def fake_lock(**_kwargs):
        yield

    replaced = {}

    monkeypatch.setattr(pairing, "browser_automation_lock", fake_lock)
    monkeypatch.setattr(pairing, "_open_dxm02_context", lambda _url: ("spw", "sbrowser", "source"))
    monkeypatch.setattr(pairing, "_connect_dxm_context", lambda _playwright, _url: ("tbrowser", "target"))
    monkeypatch.setattr(pairing, "_close_dxm03_context", lambda _playwright, _browser: None)

    def fake_search(ctx, sku):
        if ctx == "source":
            return {
                "id": "mk-product",
                "sku": sku,
                "sku_code": "98012311",
                "name": "明空商品",
                "is_combo": False,
            }
        if ctx == "target":
            return {
                "id": "dxm03-product",
                "sku": sku,
                "sku_code": "DXM03-CODE",
                "name": "DXM03 商品",
                "product_sku": "PRODUCT-SKU",
            }
        return None

    def fake_detail(_ctx, product_id, **_kwargs):
        if product_id == "mk-product":
            return {
                "productDTO": {
                    "dxmCommodityProduct": {
                        "id": "mk-product",
                        "name": "明空商品",
                        "nameEn": "Source Product",
                        "sku": "50853279039762",
                        "skuCode": "98012311",
                        "weight": 146,
                        "length": 10,
                        "width": 5,
                        "height": 2,
                        "groupState": 0,
                        "productType": "100",
                    },
                    "dxmProductCustoms": {
                        "nameCn": "驼背矫正器",
                        "nameEn": "Posture Corrector",
                        "price": 5,
                        "weight": 146,
                        "dangerDes": 0,
                    },
                    "dxmProductPacks": [{"packId": "pack-1", "quantity": 1}],
                }
            }
        return {
            "productDTO": {
                "dxmCommodityProduct": {
                    "id": "dxm03-product",
                    "name": "DXM03 商品",
                    "nameEn": "Target Product",
                    "sku": "50853279039762",
                    "skuCode": "DXM03-CODE",
                    "weight": 0,
                    "length": 0,
                    "width": 0,
                    "height": 0,
                    "groupState": 0,
                    "productType": "100",
                    "fullCid": pairing.DEFAULT_DXM03_FULL_CID,
                },
                "dxmProductCustoms": {},
                "dxmProductPacks": [],
                "supplierProductRelationMapList": [{"supplierId": "dxm03-supplier"}],
            }
        }

    edit_calls = []

    def fake_post(_ctx, path, payload, **_kwargs):
        edit_calls.append((path, payload))
        return {"code": 0, "data": {"code": 0}}

    def fake_replace(product_id, pairs, *, source):
        replaced["product_id"] = product_id
        replaced["pairs"] = pairs
        replaced["source"] = source
        return {"inserted": 0, "updated": 1, "deleted": 0, "preserved": 0}

    monkeypatch.setattr(pairing, "_search_commodity", fake_search)
    monkeypatch.setattr(pairing, "_view_commodity_detail", fake_detail)
    monkeypatch.setattr(pairing, "_post_form", fake_post)
    result = pairing.replicate_mingkong_skus_to_dxm03(
        {"id": 747, "product_code": "posture-rjc", "purchase_1688_url": ""},
        [
            {
                "shopify_product_id": "mk-product",
                "shopify_variant_id": "mk-variant",
                "shopify_variant_title": "Black / S",
                "dianxiaomi_sku": "50853279039762",
                "dianxiaomi_sku_code": "98012311",
                "dianxiaomi_name": "明空商品",
            }
        ],
        replace_product_skus_fn=fake_replace,
    )

    assert result["ok"] is True
    assert result["items"][0]["status"] == "already_exists"
    assert result["items"][0]["dxm03_sku_code"] == "DXM03-CODE"
    assert result["items"][0]["logistics_packaging"]["status"] == "updated"
    assert result["summary"]["logistics_packaging_updated_count"] == 1
    assert result["logs"][1]["level"] == "ok"
    assert edit_calls[0][0] == pairing.DXM_EDIT_COMMODITY_API
    edit_form = json.loads(edit_calls[0][1]["obj"])
    edit_product = json.loads(edit_form["dxmCommodityProduct"])
    edit_customs = json.loads(edit_form["dxmProductCustoms"])
    edit_packs = json.loads(edit_form["dxmProductPacks"])
    assert edit_calls[0][1]["pid"] == "dxm03-product"
    assert edit_product["productId"] == "dxm03-product"
    assert edit_product["skuCode"] == "DXM03-CODE"
    assert edit_product["weight"] == 146
    assert edit_product["length"] == 10
    assert edit_customs["nameCnBg"] == "驼背矫正器"
    assert edit_customs["priceBg"] == 5
    assert edit_packs == [{"packId": "pack-1", "quantity": 1}]
    assert replaced["product_id"] == 747
    assert replaced["source"] == "mingkong_replicated"
    assert replaced["pairs"][0]["dianxiaomi_sku_code"] == "DXM03-CODE"
    assert replaced["pairs"][0]["dianxiaomi_product_sku"] == "PRODUCT-SKU"


def test_replicate_mingkong_sku_creates_missing_dxm03_commodity(monkeypatch):
    @contextmanager
    def fake_lock(**_kwargs):
        yield

    added = {}
    replaced = {}
    updated_product = {}

    source_detail = {
        "productDTO": {
            "dxmCommodityProduct": {
                "id": "mk-product",
                "name": "驼背矫正器-黑色S",
                "sku": "50853279039762",
                "skuCode": "98012311",
                "price": 11,
                "weight": 146,
                "groupState": 0,
                "productType": "100",
            }
        }
    }

    monkeypatch.setattr(pairing, "browser_automation_lock", fake_lock)
    monkeypatch.setattr(pairing, "_open_dxm02_context", lambda _url: ("spw", "sbrowser", "source"))
    monkeypatch.setattr(pairing, "_connect_dxm_context", lambda _playwright, _url: ("tbrowser", "target"))
    monkeypatch.setattr(pairing, "_close_dxm03_context", lambda _playwright, _browser: None)

    def fake_search(ctx, sku):
        if ctx == "source":
            return {
                "id": "mk-product",
                "sku": sku,
                "sku_code": "98012311",
                "name": "驼背矫正器-黑色S",
                "source_url": "",
                "is_combo": False,
            }
        if ctx == "target":
            return None
        return None

    def fake_add(_ctx, form_payload):
        added["payload"] = form_payload
        return {"code": 0, "data": {"code": 0}}

    def fake_replace(product_id, pairs, *, source):
        replaced["product_id"] = product_id
        replaced["pairs"] = pairs
        replaced["source"] = source
        return {"inserted": 0, "updated": 1, "deleted": 0, "preserved": 0}

    def fake_update(product_id, **fields):
        updated_product["product_id"] = product_id
        updated_product["fields"] = fields
        return 1

    monkeypatch.setattr(pairing, "_search_commodity", fake_search)
    monkeypatch.setattr(pairing, "_view_commodity_detail", lambda *_args, **_kwargs: source_detail)
    monkeypatch.setattr(pairing, "_target_sku_code", lambda *_args, **_kwargs: ("98012311-MK", "renamed"))
    monkeypatch.setattr(pairing, "_add_replicated_commodity", fake_add)
    monkeypatch.setattr(
        pairing,
        "_wait_for_commodity",
        lambda _ctx, sku: {
            "id": "dxm03-product",
            "sku": sku,
            "sku_code": "98012311-MK",
            "name": "驼背矫正器-黑色S",
            "product_sku": "PRODUCT-SKU",
        },
    )
    result = pairing.replicate_mingkong_skus_to_dxm03(
        {"id": 747, "product_code": "posture-rjc", "purchase_1688_url": ""},
        [
            {
                "shopify_product_id": "mk-product",
                "shopify_variant_id": "mk-variant",
                "shopify_variant_title": "Black / S",
                "dianxiaomi_sku": "50853279039762",
                "dianxiaomi_sku_code": "98012311",
                "dianxiaomi_name": "明空商品",
            }
        ],
        selections=[
            {
                "dianxiaomi_sku": "50853279039762",
                "product_id_alibaba": "922648495856",
            }
        ],
        replace_product_skus_fn=fake_replace,
        update_product_fn=fake_update,
    )

    form = json.loads(added["payload"]["obj"])
    created_product = json.loads(form["dxmCommodityProduct"])
    assert result["ok"] is True
    assert result["items"][0]["status"] == "created"
    assert result["items"][0]["sku_code_strategy"] == "renamed"
    assert result["summary"]["created_count"] == 1
    assert created_product["sku"] == "50853279039762"
    assert created_product["skuCode"] == "98012311-MK"
    assert created_product["sourceUrl"] == "https://detail.1688.com/offer/922648495856.html"
    assert replaced["pairs"][0]["dianxiaomi_sku_code"] == "98012311-MK"
    assert updated_product["fields"]["purchase_1688_url"] == "https://detail.1688.com/offer/922648495856.html"


def test_replicate_mingkong_sku_creates_combo_from_target_components(monkeypatch):
    @contextmanager
    def fake_lock(**_kwargs):
        yield

    added = {}
    replaced = {}

    source_detail = {
        "productDTO": {
            "dxmCommodityProduct": {
                "id": "mk-combo",
                "name": "组合商品 2件装",
                "nameEn": "Combo 2-Pack",
                "sku": "combo-sku",
                "skuCode": "combo-code",
                "price": 0,
                "weight": 200,
                "groupState": 1,
                "productType": "100",
                "imgUrl": "/productimage/combo.jpg",
            }
        }
    }

    monkeypatch.setattr(pairing, "browser_automation_lock", fake_lock)
    monkeypatch.setattr(pairing, "_open_dxm02_context", lambda _url: ("spw", "sbrowser", "source"))
    monkeypatch.setattr(pairing, "_connect_dxm_context", lambda _playwright, _url: ("tbrowser", "target"))
    monkeypatch.setattr(pairing, "_close_dxm03_context", lambda _playwright, _browser: None)

    def fake_search(ctx, sku):
        if ctx == "source" and sku == "combo-sku":
            return {"id": "mk-combo", "sku": sku, "sku_code": "combo-code", "is_combo": True}
        if ctx == "target" and sku == "combo-sku":
            return None
        if ctx == "target" and sku == "child-sku":
            return {"id": "dxm03-child", "sku": sku, "sku_code": "child-code", "name": "组件"}
        return None

    def fake_add_combo(_ctx, form_payload):
        added["payload"] = form_payload
        return {"code": 0, "data": {"code": 0}}

    def fake_replace(product_id, pairs, *, source):
        replaced["product_id"] = product_id
        replaced["pairs"] = pairs
        replaced["source"] = source
        return {"inserted": 0, "updated": 1, "deleted": 0, "preserved": 0}

    def fake_update(_product_id, **_fields):
        return 1

    monkeypatch.setattr(pairing, "_search_commodity", fake_search)
    monkeypatch.setattr(
        pairing,
        "_search_child_sku_info",
        lambda _ctx, _product_id: [{"sku": "child-sku", "quantity": 2}],
    )
    monkeypatch.setattr(pairing, "_view_commodity_detail", lambda *_args, **_kwargs: source_detail)
    monkeypatch.setattr(pairing, "_target_sku_code", lambda *_args, **_kwargs: ("combo-code", "preserved"))
    monkeypatch.setattr(pairing, "_add_replicated_combo_commodity", fake_add_combo)
    monkeypatch.setattr(
        pairing,
        "_wait_for_commodity",
        lambda _ctx, sku: {
            "id": "dxm03-combo",
            "sku": sku,
            "sku_code": "combo-code",
            "name": "组合商品 2件装",
        },
    )

    result = pairing.replicate_mingkong_skus_to_dxm03(
        {"id": 747, "product_code": "combo-rjc", "purchase_1688_url": ""},
        [
            {
                "shopify_variant_id": "combo-variant",
                "shopify_variant_title": "Combo",
                "dianxiaomi_sku": "combo-sku",
                "dianxiaomi_sku_code": "combo-code",
                "dianxiaomi_name": "组合商品 2件装",
            }
        ],
        selections=[{"dianxiaomi_sku": "combo-sku", "product_id_alibaba": "888"}],
        replace_product_skus_fn=fake_replace,
        update_product_fn=fake_update,
    )

    form = json.loads(added["payload"]["obj"])
    created_product = json.loads(form["dxmCommodityProduct"])
    assert result["ok"] is True
    assert result["items"][0]["status"] == "created"
    assert created_product["groupState"] == "1"
    assert created_product["childIds"] == "dxm03-child"
    assert created_product["childNums"] == "2"
    assert added["payload"]["shopId"] == "-1"
    assert replaced["pairs"][0]["dianxiaomi_sku_code"] == "combo-code"


def test_replicate_mingkong_sku_creates_components_before_combo(monkeypatch):
    @contextmanager
    def fake_lock(**_kwargs):
        yield

    created = {"child": False}
    add_calls = []

    def fake_search(ctx, sku):
        if ctx == "source" and sku == "combo-sku":
            return {"id": "mk-combo", "sku": sku, "sku_code": "combo-code", "is_combo": True}
        if ctx == "source" and sku == "child-sku":
            return {"id": "mk-child", "sku": sku, "sku_code": "child-code", "is_combo": False}
        if ctx == "target" and sku == "child-sku" and created["child"]:
            return {"id": "dxm03-child", "sku": sku, "sku_code": "child-code", "name": "组件"}
        return None

    def fake_wait(_ctx, sku):
        if sku == "child-sku":
            created["child"] = True
            return {"id": "dxm03-child", "sku": sku, "sku_code": "child-code", "name": "组件"}
        if sku == "combo-sku":
            return {"id": "dxm03-combo", "sku": sku, "sku_code": "combo-code", "name": "组合"}
        return None

    monkeypatch.setattr(pairing, "browser_automation_lock", fake_lock)
    monkeypatch.setattr(pairing, "_open_dxm02_context", lambda _url: ("spw", "sbrowser", "source"))
    monkeypatch.setattr(pairing, "_connect_dxm_context", lambda _playwright, _url: ("tbrowser", "target"))
    monkeypatch.setattr(pairing, "_close_dxm03_context", lambda _playwright, _browser: None)
    monkeypatch.setattr(pairing, "_search_commodity", fake_search)
    monkeypatch.setattr(
        pairing,
        "_search_child_sku_info",
        lambda _ctx, _product_id: [{"sku": "child-sku", "quantity": 1}],
    )
    monkeypatch.setattr(
        pairing,
        "_view_commodity_detail",
        lambda _ctx, product_id, **_kwargs: {
            "productDTO": {
                "dxmCommodityProduct": {
                    "id": product_id,
                    "name": "商品",
                    "sku": "combo-sku" if product_id == "mk-combo" else "child-sku",
                    "skuCode": "combo-code" if product_id == "mk-combo" else "child-code",
                    "groupState": 1 if product_id == "mk-combo" else 0,
                    "productType": "100",
                }
            }
        },
    )
    monkeypatch.setattr(pairing, "_target_sku_code", lambda _ctx, sku_code, **_kwargs: (sku_code, "preserved"))
    monkeypatch.setattr(pairing, "_add_replicated_commodity", lambda *_args, **_kwargs: add_calls.append("child") or {})
    monkeypatch.setattr(pairing, "_add_replicated_combo_commodity", lambda *_args, **_kwargs: add_calls.append("combo") or {})
    monkeypatch.setattr(pairing, "_wait_for_commodity", fake_wait)

    result = pairing.replicate_mingkong_skus_to_dxm03(
        {"id": 747, "product_code": "combo-rjc", "purchase_1688_url": ""},
        [
            {
                "shopify_variant_id": "combo-variant",
                "shopify_variant_title": "Combo",
                "dianxiaomi_sku": "combo-sku",
                "dianxiaomi_sku_code": "combo-code",
                "is_combo": True,
            },
            {
                "shopify_variant_id": "child-variant",
                "shopify_variant_title": "Child",
                "dianxiaomi_sku": "child-sku",
                "dianxiaomi_sku_code": "child-code",
            },
        ],
        replace_product_skus_fn=lambda *_args, **_kwargs: {"inserted": 0, "updated": 2, "deleted": 0, "preserved": 0},
        update_product_fn=lambda *_args, **_kwargs: 1,
    )

    assert result["ok"] is True
    assert [item["status"] for item in result["items"]] == ["created", "created"]
    assert add_calls == ["child", "combo"]
