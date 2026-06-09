from __future__ import annotations

import json
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
            },
            "dxmProductCustoms": {
                "id": "customs-id",
                "productId": "mk-product",
                "puid": "mk-account",
                "nameCn": "驼背矫正器",
                "weight": 146,
            },
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
    monkeypatch.setattr(pairing, "_open_dxm03_context", lambda _url: ("tpw", "tbrowser", "target"))
    monkeypatch.setattr(pairing, "_close_dxm03_context", lambda _playwright, _browser: None)

    def fake_search(ctx, sku):
        if ctx == "target":
            return {
                "id": "dxm03-product",
                "sku": sku,
                "sku_code": "DXM03-CODE",
                "name": "DXM03 商品",
                "product_sku": "PRODUCT-SKU",
            }
        raise AssertionError("DXM02 source should not be searched when DXM03 already has SKU")

    def fake_replace(product_id, pairs, *, source):
        replaced["product_id"] = product_id
        replaced["pairs"] = pairs
        replaced["source"] = source
        return {"inserted": 0, "updated": 1, "deleted": 0, "preserved": 0}

    monkeypatch.setattr(pairing, "_search_commodity", fake_search)
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
    monkeypatch.setattr(pairing, "_open_dxm03_context", lambda _url: ("tpw", "tbrowser", "target"))
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
    assert created_product["sku"] == "50853279039762"
    assert created_product["skuCode"] == "98012311-MK"
    assert created_product["sourceUrl"] == "https://detail.1688.com/offer/922648495856.html"
    assert replaced["pairs"][0]["dianxiaomi_sku_code"] == "98012311-MK"
    assert updated_product["fields"]["purchase_1688_url"] == "https://detail.1688.com/offer/922648495856.html"
