from __future__ import annotations

import pytest


def test_serialize_product_skus_prefers_manual_purchase_price_and_goods_name():
    from web.routes.medias._serializers import _serialize_product_skus

    rows = [
        {
            "id": 5,
            "shopify_product_id": "SP1",
            "shopify_variant_id": "V1",
            "shopify_sku": "SHOP-SKU",
            "shopify_price": "12.95",
            "shopify_compare_at_price": None,
            "shopify_currency": "USD",
            "shopify_inventory_quantity": -9,
            "shopify_weight_grams": None,
            "shopify_variant_title": "Blue",
            "dianxiaomi_sku": "DXM-SKU",
            "dianxiaomi_product_sku": "DXM-PRODUCT-SKU",
            "dianxiaomi_sku_code": "ERP-OLD",
            "dianxiaomi_name": "Auto goods",
            "manual_override": 1,
            "manual_unit_price_rmb": "8.66",
            "manual_goods_name": "人工商品名",
            "source": "manual_edit",
            "updated_at": None,
        }
    ]

    result = _serialize_product_skus(
        rows,
        cost_inputs={
            "purchase_price": "99",
            "packet_cost_estimated": "2",
            "packet_cost_actual": "3",
            "standalone_shipping_fee": "4",
        },
        rmb_per_usd=7.0,
        xmyc_index={
            "DXM-SKU": {
                "unit_price": "18.88",
                "goods_name": "xmyc 自动商品名",
                "stock_available": 11,
                "match_type": "auto",
                "sku_code": "ERP-XMYC",
            }
        },
    )

    item = result[0]
    assert item["manual_override"] is True
    assert item["manual_unit_price_rmb"] == 8.66
    assert item["manual_goods_name"] == "人工商品名"
    assert item["dianxiaomi_product_sku"] == "DXM-PRODUCT-SKU"
    assert item["xmyc_unit_price_rmb"] == 8.66
    assert item["xmyc_goods_name"] == "人工商品名"
    assert item["roas_calculation"]["purchase_basis"] == "manual_variant"


def test_manual_product_sku_update_filters_variant_and_serializes_updated_row():
    from web.services.media_product_sku_manual_edit import build_product_sku_update_response

    captured = {}
    product = {
        "id": 42,
        "purchase_price": "20",
        "packet_cost_estimated": "2",
        "packet_cost_actual": "3",
        "standalone_shipping_fee": "4",
    }

    def fake_update(product_id, sku_id, fields, *, edited_by=None):
        captured["update"] = (product_id, sku_id, fields, edited_by)
        return {
            "id": sku_id,
            "product_id": product_id,
            **fields,
            "shopify_variant_id": "V1",
            "shopify_variant_title": "Blue",
            "dianxiaomi_sku": fields["dianxiaomi_sku"],
            "dianxiaomi_product_sku": fields["dianxiaomi_product_sku"],
            "manual_override": 1,
            "source": "manual_edit",
            "updated_at": None,
        }

    result = build_product_sku_update_response(
        42,
        5,
        product,
        {
            "shopify_variant_title": "Should not change",
            "shopify_sku": "  SHOP-EDIT  ",
            "shopify_price": "12.95",
            "shopify_inventory_quantity": "-7",
            "dianxiaomi_sku": "  DXM-EDIT ",
            "dianxiaomi_product_sku": "  DXM-PRODUCT-EDIT ",
            "dianxiaomi_sku_code": " ERP-EDIT ",
            "manual_unit_price_rmb": "8.66",
            "manual_goods_name": "  人工商品名 ",
        },
        edited_by=9,
        update_product_sku_fn=fake_update,
        list_xmyc_unit_prices_fn=lambda skus: captured.update(xmyc_skus=skus) or {},
        get_configured_rmb_per_usd_fn=lambda: 7.0,
        serialize_product_skus_fn=lambda rows, **kwargs: captured.update(
            serialize=(rows, kwargs)
        )
        or [{"id": rows[0]["id"], "manual_override": True}],
    )

    assert result.status_code == 200
    assert result.payload == {"ok": True, "item": {"id": 5, "manual_override": True}}
    assert captured["update"] == (
        42,
        5,
        {
            "shopify_sku": "SHOP-EDIT",
            "shopify_price": 12.95,
            "shopify_inventory_quantity": -7,
            "dianxiaomi_sku": "DXM-EDIT",
            "dianxiaomi_product_sku": "DXM-PRODUCT-EDIT",
            "dianxiaomi_sku_code": "ERP-EDIT",
            "manual_unit_price_rmb": 8.66,
            "manual_goods_name": "人工商品名",
        },
        9,
    )
    assert captured["xmyc_skus"] == ["DXM-EDIT"]
    assert captured["serialize"][1]["cost_inputs"] == {
        "purchase_price": "20",
        "packet_cost_estimated": "2",
        "packet_cost_actual": "3",
        "standalone_shipping_fee": "4",
    }


def test_manual_product_sku_update_rejects_invalid_numeric_value():
    from web.services.media_product_sku_manual_edit import build_product_sku_update_response

    called = []

    result = build_product_sku_update_response(
        42,
        5,
        {"id": 42},
        {"shopify_price": "abc"},
        edited_by=9,
        update_product_sku_fn=lambda *args, **kwargs: called.append("update"),
    )

    assert result.status_code == 400
    assert result.payload == {
        "error": "invalid_fields",
        "message": "shopify_price must be a number",
    }
    assert called == []


def test_remote_sku_sync_sql_preserves_manual_override_rows():
    from tools.dianxiaomi_sku_sync import (
        build_remote_apply_sql,
        build_remote_ensure_table_sql,
    )

    ensure_sql = build_remote_ensure_table_sql()
    apply_sql = build_remote_apply_sql(
        title_updates=[],
        sku_replacements=[
            (
                42,
                [
                    {
                        "shopify_product_id": "SP1",
                        "shopify_variant_id": "V1",
                        "shopify_sku": "AUTO-SKU",
                        "shopify_price": 12.95,
                        "shopify_compare_at_price": None,
                        "shopify_inventory_quantity": -1,
                        "shopify_weight_grams": None,
                        "shopify_variant_title": "Blue",
                        "dianxiaomi_sku": "DXM-AUTO",
                        "dianxiaomi_product_sku": "DXM-PRODUCT-AUTO",
                        "dianxiaomi_sku_code": "ERP-AUTO",
                        "dianxiaomi_name": "自动商品名",
                    }
                ],
            )
        ],
    )

    assert "manual_override TINYINT(1) NOT NULL DEFAULT 0" in ensure_sql
    assert "dianxiaomi_product_sku VARCHAR(128) NULL" in ensure_sql
    assert "manual_unit_price_rmb DECIMAL(12,2) NULL" in ensure_sql
    assert "manual_goods_name VARCHAR(512) NULL" in ensure_sql
    assert "DELETE FROM media_product_skus WHERE product_id=42 AND COALESCE(manual_override, 0)=0;" in apply_sql
    assert "ON DUPLICATE KEY UPDATE" in apply_sql
    assert (
        "shopify_sku=IF(COALESCE(manual_override, 0)=1, shopify_sku, VALUES(shopify_sku))"
        in apply_sql
    )
    assert (
        "dianxiaomi_product_sku=IF(COALESCE(manual_override, 0)=1, dianxiaomi_product_sku, VALUES(dianxiaomi_product_sku))"
        in apply_sql
    )


def test_replace_product_skus_preserves_manual_override_rows(monkeypatch):
    from appcore import medias

    class FakeCursor:
        rowcount = 0

        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=None):
            self.calls.append((sql, args))
            if sql.startswith("DELETE FROM media_product_skus"):
                self.rowcount = len(args or ())
            else:
                self.rowcount = 1

        def fetchall(self):
            return [
                {"id": 101, "shopify_variant_id": "V1", "manual_override": 1},
                {"id": 102, "shopify_variant_id": "V2", "manual_override": 0},
                {"id": 103, "shopify_variant_id": "V3", "manual_override": 0},
                {"id": 104, "shopify_variant_id": "V4", "manual_override": 0},
                {"id": 105, "shopify_variant_id": "V5", "manual_override": 1},
            ]

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()
            self.committed = False

        def begin(self):
            pass

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("rollback should not be called")

    conn = FakeConn()
    monkeypatch.setattr(medias, "get_conn", lambda: conn)

    result = medias.replace_product_skus(
        42,
        [
            {"shopify_variant_id": "V1", "shopify_sku": "MANUAL-SHOULD-NOT-WIN"},
            {"shopify_variant_id": "V2", "shopify_sku": "AUTO-UPDATED"},
            {"shopify_variant_id": "V6", "shopify_sku": "AUTO-NEW"},
        ],
        source="auto",
    )

    assert result == {"inserted": 1, "updated": 1, "deleted": 2, "preserved": 1}
    assert conn.committed is True
    update_args = [
        args for sql, args in conn.cursor_obj.calls
        if sql.startswith("UPDATE media_product_skus SET")
    ]
    assert update_args and update_args[0][-1] == "V2"
    assert all(args[-1] != "V1" for args in update_args)
    delete_calls = [
        args for sql, args in conn.cursor_obj.calls
        if sql.startswith("DELETE FROM media_product_skus")
    ]
    assert delete_calls == [(103, 104)]


def test_build_pair_rows_keeps_dianxiaomi_product_sku_separate_from_pair_key():
    from tools.dianxiaomi_sku_sync import build_pair_rows

    rows = build_pair_rows(
        [
            {
                "shopify_product_id": "SP1",
                "variants": [
                    {
                        "shopify_variant_id": "V1",
                        "shopify_sku": "SHOPIFY-PAIR-KEY",
                        "pair_key": "SHOPIFY-PAIR-KEY",
                    }
                ],
            }
        ],
        {
            "SHOPIFY-PAIR-KEY": {
                "dianxiaomi_sku": "SHOPIFY-PAIR-KEY",
                "dianxiaomi_product_sku": "DXM-PRODUCT-SKU",
                "dianxiaomi_sku_code": "ERP-1",
                "dianxiaomi_name": "店小秘商品",
            }
        },
    )

    assert rows["SP1"][0]["dianxiaomi_sku"] == "SHOPIFY-PAIR-KEY"
    assert rows["SP1"][0]["dianxiaomi_product_sku"] == "DXM-PRODUCT-SKU"
