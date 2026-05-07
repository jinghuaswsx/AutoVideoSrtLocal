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
