from pathlib import Path


TEMPLATE = Path("web/templates/product_profit_dashboard.html").read_text(encoding="utf-8")


def test_product_profit_picker_maps_use_null_prototype_objects():
    assert "function makeProductMap() { return Object.create(null); }" in TEMPLATE
    assert "var productLabelToId = makeProductMap();" in TEMPLATE
    assert "var productCodeToLabel = makeProductMap();" in TEMPLATE
    assert "productLabelToId = makeProductMap();" in TEMPLATE
    assert "productCodeToLabel = makeProductMap();" in TEMPLATE

    assert "var productLabelToId = {};" not in TEMPLATE
    assert "var productCodeToLabel = {};" not in TEMPLATE
    assert "productLabelToId = {};" not in TEMPLATE
    assert "productCodeToLabel = {};" not in TEMPLATE


def test_product_profit_product_picker_uses_modal_search():
    assert 'id="ppd-product-modal"' in TEMPLATE
    assert 'role="dialog"' in TEMPLATE
    assert 'aria-modal="true"' in TEMPLATE
    assert 'id="ppd-product-search"' in TEMPLATE
    assert 'id="ppd-product-results"' in TEMPLATE
    assert "function openProductModal()" in TEMPLATE
    assert "function renderProductPickerResults(query)" in TEMPLATE
    assert "function selectProductFromPicker(label)" in TEMPLATE
    assert "productSearchInput.focus()" in TEMPLATE
    assert "<datalist" not in TEMPLATE


def test_product_profit_product_input_is_wider_and_modal_trigger():
    assert ".ppd-filters #ppd-product { min-width: 480px;" in TEMPLATE
    assert 'readonly aria-haspopup="dialog"' in TEMPLATE
    assert "productInput.addEventListener('click', openProductModal)" in TEMPLATE


def test_product_profit_product_picker_sorts_and_empty_search_lists_all_products():
    assert "productPickerItems = opts.slice().sort(function(a, b)" in TEMPLATE
    assert "String(a.product_code || '').localeCompare(String(b.product_code || '')" in TEMPLATE
    assert "if (!q) return productPickerItems;" in TEMPLATE


def test_product_profit_product_search_has_no_concrete_default():
    assert "fully-automatic-water-blaster-rjc" not in TEMPLATE
    assert "默认选中" not in TEMPLATE
    assert "productInput.value = ALL_LABEL;" not in TEMPLATE


def test_product_profit_product_picker_redirects_after_selection():
    assert "if (selectedProductId === '0') {" in TEMPLATE
    assert "window.switchTab('list');" in TEMPLATE
    assert "window.switchTab('orders');" in TEMPLATE
    assert "reloadActiveTab();" not in TEMPLATE


def test_product_profit_ads_campaigns_support_manual_unbind_only():
    assert "manual_override_id" in TEMPLATE
    assert "unbindManualCampaign" in TEMPLATE
    assert "DELETE" in TEMPLATE
    assert "解绑" in TEMPLATE


def test_product_profit_country_tab_uses_country_pills_not_bar_chart():
    assert 'id="ppd-country-pills"' in TEMPLATE
    assert "function loadCountryPills()" in TEMPLATE
    assert "function selectCountryPill(country)" in TEMPLATE
    assert "chart-country-tab3" not in TEMPLATE
    assert "按国家利润（点击柱条筛选下钻）" not in TEMPLATE


def test_product_profit_country_tab_ranks_products_when_no_product_selected():
    assert "fetch('/order-analytics/product-profit/countries.json'" in TEMPLATE
    assert "function loadCountryProductRanking(country)" in TEMPLATE
    assert "rows.sort(function(a, b)" in TEMPLATE
    assert "return (Number(b.order_count) || 0) - (Number(a.order_count) || 0);" in TEMPLATE
    assert "renderCountryProductRanking" in TEMPLATE


def test_product_profit_country_fallback_pills_start_with_us_then_gb():
    assert "{ country: 'US', lang: 'en', label: '美国' }" in TEMPLATE
    assert TEMPLATE.index("{ country: 'US', lang: 'en', label: '美国' }") < TEMPLATE.index(
        "{ country: 'GB', lang: 'en', label: '英国' }"
    )
