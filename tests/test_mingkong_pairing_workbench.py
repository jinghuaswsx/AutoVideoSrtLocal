from __future__ import annotations

from contextlib import contextmanager

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

    result = pairing.confirm_dxm03_pairing(
        {"id": 1, "product_code": "sample-rjc", "purchase_1688_url": ""},
        [{"dianxiaomi_sku": "sku-1"}],
    )

    assert result["ok"] is False
    assert result["items"][0]["error"] == "missing_purchase_url"
