from decimal import Decimal

import pytest

from appcore import medias
from appcore.product_roas import calculate_break_even_roas
from web.routes import medias as medias_routes


def _estimated_roas(revenue):
    return revenue / (revenue * 0.93 - revenue * 0.10 - revenue * 0.20)


def test_calculates_estimated_and_actual_roas():
    result = calculate_break_even_roas(
        purchase_price=20,
        estimated_packet_cost=10,
        actual_packet_cost=12,
        standalone_price=60,
    )

    revenue = 67
    assert result["estimated_roas"] == pytest.approx(_estimated_roas(revenue))
    assert result["actual_roas"] == pytest.approx(revenue / (revenue * 0.93 - (32 / 6.83)))
    assert result["effective_basis"] == "actual"
    assert result["effective_roas"] == pytest.approx(revenue / (revenue * 0.93 - (32 / 6.83)))
    assert result["shipping_source"] == "fallback_7usd"
    assert result["purchase_source"] == "actual"
    assert result["packet_source"] == "actual"
    assert result["rmb_per_usd"] == 6.83


def test_calculates_roas_with_standalone_shipping_fee_and_missing_packet_uses_fallback():
    result = calculate_break_even_roas(
        purchase_price=20,
        estimated_packet_cost=10,
        actual_packet_cost=None,
        standalone_price=60,
        standalone_shipping_fee=8,
    )

    revenue = 68
    assert result["estimated_roas"] == pytest.approx(_estimated_roas(revenue))
    assert result["actual_roas"] == pytest.approx(
        revenue / (revenue * 0.93 - (20 / 6.83) - (revenue * 0.20))
    )
    assert result["effective_basis"] == "fallback"
    assert result["effective_roas"] == pytest.approx(result["actual_roas"])
    assert result["shipping_source"] == "actual"
    assert result["purchase_source"] == "actual"
    assert result["packet_source"] == "fallback_20pct"


def test_calculates_roas_with_custom_rmb_usd_rate():
    result = calculate_break_even_roas(
        purchase_price=20,
        estimated_packet_cost=10,
        actual_packet_cost=None,
        standalone_price=60,
        rmb_per_usd=5,
    )

    revenue = 67
    assert result["estimated_roas"] == pytest.approx(_estimated_roas(revenue))
    assert result["actual_roas"] == pytest.approx(
        revenue / (revenue * 0.93 - 4 - (revenue * 0.20))
    )
    assert result["rmb_per_usd"] == 5


def test_uses_estimated_roas_when_actual_costs_missing():
    result = calculate_break_even_roas(
        purchase_price=None,
        estimated_packet_cost=10,
        actual_packet_cost=None,
        standalone_price=60,
    )

    revenue = 67
    assert result["estimated_roas"] == pytest.approx(_estimated_roas(revenue))
    assert result["actual_roas"] == pytest.approx(_estimated_roas(revenue))
    assert result["effective_basis"] == "estimated"
    assert result["effective_roas"] == pytest.approx(_estimated_roas(revenue))
    assert result["purchase_source"] == "fallback_10pct"
    assert result["packet_source"] == "fallback_20pct"


def test_returns_none_when_margin_cannot_break_even():
    result = calculate_break_even_roas(
        purchase_price=400,
        estimated_packet_cost=10,
        actual_packet_cost=None,
        standalone_price=60,
    )

    revenue = 67
    assert result["estimated_roas"] == pytest.approx(_estimated_roas(revenue))
    assert result["actual_roas"] is None
    assert result["effective_roas"] is None
    assert result["effective_basis"] == "fallback"


def test_update_product_accepts_roas_fields(monkeypatch):
    captured = {}

    def fake_execute(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(medias, "execute", fake_execute)

    medias.update_product(
        9,
        purchase_1688_url="https://detail.1688.com/example",
        purchase_price="20.50",
        packet_cost_estimated="8.20",
        packet_cost_actual="9.30",
        package_length_cm="10",
        package_width_cm="5",
        package_height_cm="3",
        standalone_price="59.99",
        standalone_shipping_fee="6.99",
    )

    assert "purchase_1688_url=%s" in captured["sql"]
    assert "purchase_price=%s" in captured["sql"]
    assert "standalone_price=%s" in captured["sql"]
    assert "standalone_shipping_fee=%s" in captured["sql"]
    assert captured["args"][-1] == 9
    assert captured["args"][1] == 20.5


def test_update_product_rejects_invalid_roas_number():
    with pytest.raises(ValueError):
        medias.update_product(9, purchase_price="abc")


def test_update_product_rejects_standalone_price_that_matches_sku_cents(monkeypatch):
    monkeypatch.setattr(
        medias,
        "query",
        lambda sql, args=(): [
            {"shopify_price": Decimal("9.99")},
            {"shopify_price": Decimal("19.99")},
        ],
    )

    def fail_execute(_sql, _args=()):
        raise AssertionError("invalid standalone_price should not be written")

    monkeypatch.setattr(medias, "execute", fail_execute)

    with pytest.raises(ValueError, match="standalone_price.*9.99.*999"):
        medias.update_product(9, standalone_price="999")


def test_update_product_allows_legitimate_high_standalone_price(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        medias,
        "query",
        lambda sql, args=(): [{"shopify_price": Decimal("129.99")}],
    )

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(medias, "execute", fake_execute)

    assert medias.update_product(9, standalone_price="129.99") == 1
    assert "standalone_price=%s" in captured["sql"]
    assert captured["args"] == (129.99, 9)


def test_serialize_product_includes_roas_fields_and_calculation():
    product = {
        "id": 9,
        "user_id": 1,
        "name": "测试产品",
        "product_code": "test-product-rjc",
        "mk_id": 1234,
        "shopifyid": "5678",
        "owner_name": "负责人",
        "color_people": None,
        "source": None,
        "remark": "",
        "ai_score": None,
        "ai_evaluation_result": "",
        "ai_evaluation_detail": "",
        "listing_status": medias.LISTING_STATUS_ON,
        "ad_supported_langs": "",
        "archived": 0,
        "created_at": None,
        "updated_at": None,
        "localized_links_json": None,
        "link_check_tasks_json": None,
        "shopify_image_status_json": None,
        "purchase_1688_url": "https://detail.1688.com/example",
        "purchase_price": 20,
        "packet_cost_estimated": 10,
        "packet_cost_actual": 12,
        "package_length_cm": 10,
        "package_width_cm": 5,
        "package_height_cm": 3,
        "tk_sea_cost": None,
        "tk_air_cost": None,
        "tk_sale_price": None,
        "standalone_price": 60,
        "standalone_shipping_fee": 8,
    }

    serialized = medias_routes._serialize_product(product, items_count=0, covers={})

    assert serialized["purchase_1688_url"] == "https://detail.1688.com/example"
    assert serialized["purchase_price"] == 20.0
    assert serialized["packet_cost_estimated"] == 10.0
    assert serialized["packet_cost_actual"] == 12.0
    assert serialized["standalone_price"] == 60.0
    assert serialized["standalone_shipping_fee"] == 8.0
    assert serialized["roas_calculation"]["estimated_roas"] == pytest.approx(_estimated_roas(68))
    assert serialized["roas_calculation"]["actual_roas"] == pytest.approx(68 / (68 * 0.93 - (32 / 6.83)))
    assert serialized["roas_calculation"]["effective_basis"] == "actual"
    assert serialized["roas_calculation"]["shipping_source"] == "actual"
    assert serialized["roas_calculation"]["purchase_source"] == "actual"
    assert serialized["roas_calculation"]["packet_source"] == "actual"
    assert serialized["roas_calculation"]["rmb_per_usd"] == 6.83
