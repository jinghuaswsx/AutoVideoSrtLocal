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


def test_product_profit_tabs_are_pills_above_tab_specific_filters():
    assert TEMPLATE.index('<nav class="ppd-tabs" role="tablist">') < TEMPLATE.index('<section class="ppd-filters">')
    assert "border-radius: 999px;" in TEMPLATE
    assert 'data-filter-control="product"' in TEMPLATE
    assert 'data-filter-control="country"' in TEMPLATE
    assert 'data-filter-control="site"' in TEMPLATE
    assert 'data-filter-control="orders-download"' in TEMPLATE
    assert "function setFilterControlsForTab(tabName)" in TEMPLATE
    assert "orders: ['product', 'site', 'country', 'from', 'to', 'reload', 'orders-download']" in TEMPLATE
    assert "product-country: ['product', 'from', 'to', 'reload']" in TEMPLATE


def test_product_profit_orders_tab_has_store_filter_and_roas_card():
    assert 'id="ppd-site-select"' in TEMPLATE
    assert '<option value="">全部店铺</option>' in TEMPLATE
    assert '<option value="newjoy">newjoyloo</option>' in TEMPLATE
    assert '<option value="omurio">Omurio</option>' in TEMPLATE
    assert 'id="stat-roas"' in TEMPLATE
    assert 'id="stat-roas-sub"' in TEMPLATE
    assert "function formatRoas" in TEMPLATE
    assert "url.searchParams.set('site_code', siteSelect.value || '')" in TEMPLATE
    assert "site_code=' + encodeURIComponent(site)" in TEMPLATE


def test_product_profit_dashboard_defaults_to_meta_business_date():
    assert "function currentMetaBusinessDate(now)" in TEMPLATE
    assert "metaCutoverHourBj = 16" in TEMPLATE
    assert "var today = currentMetaBusinessDate();" in TEMPLATE
    assert "var today = new Date();" not in TEMPLATE


def test_product_profit_date_filters_label_meta_business_day():
    assert "开始日期（Meta业务日）" in TEMPLATE
    assert "结束日期（Meta业务日）" in TEMPLATE
    assert "北京时间16:00切日" in TEMPLATE


def test_product_profit_has_product_country_analysis_tab_matrix():
    assert 'data-tab="product-country"' in TEMPLATE
    assert 'data-panel="product-country"' in TEMPLATE
    assert 'id="ppd-product-country-matrix"' in TEMPLATE
    assert "function loadProductCountryTab()" in TEMPLATE
    assert "function renderProductCountryMatrix(data)" in TEMPLATE
    assert "订单量" in TEMPLATE
    assert "销售额" in TEMPLATE
    assert "ROAS" in TEMPLATE
    assert "by_country" in TEMPLATE


def test_product_profit_mobile_tables_keep_shared_header_and_body_layout():
    """移动端表格不能把 thead/tbody 拆成两张表，否则表头和数据列会错位。"""
    expected_snippets = [
        "docs/superpowers/specs/2026-05-10-product-profit-mobile-table-alignment.md",
        ".ppd-table-wrap table.ppd-table:not(.mobile-no-scroll)",
        "display: table-header-group;",
        "display: table-row-group;",
        "display: table-footer-group;",
        "overflow-x: auto;",
        "white-space: nowrap;",
    ]
    for snippet in expected_snippets:
        assert snippet in TEMPLATE, f"missing mobile table layout override: {snippet}"
