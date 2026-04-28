import pytest

from appcore import medias
from appcore.product_roas import calculate_break_even_roas
from web.routes import medias as medias_routes


def test_calculates_estimated_and_actual_roas():
    result = calculate_break_even_roas(
        purchase_price=20,
        estimated_packet_cost=10,
        actual_packet_cost=12,
        standalone_price=60,
    )

    assert result["estimated_roas"] == 2.5
    assert result["actual_roas"] == pytest.approx(60 / 22)
    assert result["effective_basis"] == "actual"
    assert result["effective_roas"] == pytest.approx(60 / 22)


def test_uses_estimated_roas_when_actual_packet_cost_missing():
    result = calculate_break_even_roas(
        purchase_price=20,
        estimated_packet_cost=10,
        actual_packet_cost=None,
        standalone_price=60,
    )

    assert result["estimated_roas"] == 2.5
    assert result["actual_roas"] is None
    assert result["effective_basis"] == "estimated"
    assert result["effective_roas"] == 2.5


def test_returns_none_when_margin_cannot_break_even():
    result = calculate_break_even_roas(
        purchase_price=50,
        estimated_packet_cost=10,
        actual_packet_cost=None,
        standalone_price=60,
    )

    assert result["estimated_roas"] is None
    assert result["actual_roas"] is None
    assert result["effective_roas"] is None
    assert result["effective_basis"] == "estimated"


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
    )

    assert "purchase_1688_url=%s" in captured["sql"]
    assert "purchase_price=%s" in captured["sql"]
    assert "standalone_price=%s" in captured["sql"]
    assert captured["args"][-1] == 9
    assert captured["args"][1] == 20.5


def test_update_product_rejects_invalid_roas_number():
    with pytest.raises(ValueError):
        medias.update_product(9, purchase_price="abc")


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
    }

    serialized = medias_routes._serialize_product(product, items_count=0, covers={})

    assert serialized["purchase_1688_url"] == "https://detail.1688.com/example"
    assert serialized["purchase_price"] == 20.0
    assert serialized["packet_cost_estimated"] == 10.0
    assert serialized["packet_cost_actual"] == 12.0
    assert serialized["standalone_price"] == 60.0
    assert serialized["roas_calculation"]["estimated_roas"] == 2.5
    assert serialized["roas_calculation"]["effective_basis"] == "actual"
