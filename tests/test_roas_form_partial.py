from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARTIAL = ROOT / "web" / "templates" / "medias" / "_roas_form.html"


def test_partial_file_exists():
    assert PARTIAL.exists(), f"missing partial: {PARTIAL}"


def test_partial_contains_product_card_block():
    html = PARTIAL.read_text(encoding="utf-8")
    assert 'class="oc-roas-product"' in html
    assert 'id="roasProductId"' in html
    assert 'id="roasProductCover"' in html


def test_partial_contains_all_site_fields():
    html = PARTIAL.read_text(encoding="utf-8")
    for field in (
        "purchase_1688_url",
        "purchase_price",
        "standalone_price",
        "standalone_shipping_fee",
        "package_length_cm",
        "package_width_cm",
        "package_height_cm",
        "packet_cost_estimated",
        "packet_cost_actual",
    ):
        assert f'data-roas-field="{field}"' in html, f"missing field {field}"


def test_partial_contains_tk_fields_and_average_shipping_tool():
    html = PARTIAL.read_text(encoding="utf-8")
    for field in ("tk_sea_cost", "tk_air_cost", "tk_sale_price"):
        assert f'data-roas-field="{field}"' in html
    assert 'id="roasAverageShippingInput"' in html
    assert 'id="roasAverageShippingResult"' in html


def test_partial_contains_calculate_button_and_results():
    html = PARTIAL.read_text(encoding="utf-8")
    assert 'id="roasCalculateBtn"' in html
    assert 'id="roasEstimatedValue"' in html
    assert 'id="roasActualValue"' in html
    assert 'id="roasEffectiveValue"' in html


def test_medias_list_includes_partial():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert "medias/_roas_form.html" in html


def test_roas_page_includes_partial():
    html = (ROOT / "web" / "templates" / "medias" / "roas.html").read_text(encoding="utf-8")
    assert "medias/_roas_form.html" in html
