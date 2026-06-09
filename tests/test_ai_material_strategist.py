from appcore import ai_material_strategist as svc


def _row(**overrides):
    base = {
        "product_id": 1,
        "product_code": "demo-rjc",
        "product_name": "Demo",
        "spend_30d": 0,
        "orders_30d": 0,
        "results_30d": 0,
        "ad_count_30d": 0,
        "spend_7d": 0,
        "spend_yesterday": 0,
        "spend_today": 0,
        "orders_7d": 0,
        "revenue_30d": 0,
        "profit_30d": 0,
        "purchase_value_30d": 0,
        "local_material_count": 0,
    }
    base.update(overrides)
    base["true_roas_30d"] = (
        round(base["revenue_30d"] / base["spend_30d"], 4)
        if base["spend_30d"]
        else None
    )
    base["meta_roas_30d"] = (
        round(base["purchase_value_30d"] / base["spend_30d"], 4)
        if base["spend_30d"]
        else None
    )
    return base


def test_strip_rjc_for_mingkong_search_code():
    assert svc.strip_rjc("emergency-choking-relief-kit-rjc") == "emergency-choking-relief-kit"
    assert svc.strip_rjc("demo_RJC") == "demo"
    assert svc.strip_rjc("plain-code") == "plain-code"


def test_score_product_rows_filters_high_roas_low_volume_and_prefers_volume():
    tiny_high_roas = _row(
        product_id=1,
        product_code="tiny-rjc",
        spend_30d=3,
        orders_30d=1,
        revenue_30d=90,
        purchase_value_30d=90,
    )
    strong_volume = _row(
        product_id=2,
        product_code="strong-rjc",
        spend_30d=800,
        spend_7d=180,
        spend_yesterday=45,
        orders_30d=80,
        orders_7d=18,
        revenue_30d=1800,
        purchase_value_30d=1500,
        profit_30d=360,
        results_30d=120,
        ad_count_30d=18,
    )
    moderate = _row(
        product_id=3,
        product_code="moderate-rjc",
        spend_30d=90,
        orders_30d=9,
        revenue_30d=360,
        purchase_value_30d=280,
        profit_30d=80,
        results_30d=15,
        ad_count_30d=4,
    )

    ranked = svc.score_product_rows([tiny_high_roas, moderate, strong_volume], limit=10)

    assert [row["product_id"] for row in ranked] == [2, 3]
    assert "30天消耗有量" in ranked[0]["selection_reasons"]
    assert "真实ROAS较好" in ranked[0]["selection_reasons"]


def test_mk_search_codes_include_stripped_code_first():
    mapping = svc._mk_search_codes(["demo-product-rjc"])

    assert mapping["demo-product-rjc"] == ["demo-product", "demo-product-rjc"]
