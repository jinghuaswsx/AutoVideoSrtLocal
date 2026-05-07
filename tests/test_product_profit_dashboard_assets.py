from pathlib import Path


TEMPLATE = Path("web/templates/product_profit_dashboard.html").read_text(encoding="utf-8")


def test_product_profit_datalist_maps_use_null_prototype_objects():
    assert "function makeProductMap() { return Object.create(null); }" in TEMPLATE
    assert "var productLabelToId = makeProductMap();" in TEMPLATE
    assert "var productCodeToLabel = makeProductMap();" in TEMPLATE
    assert "productLabelToId = makeProductMap();" in TEMPLATE
    assert "productCodeToLabel = makeProductMap();" in TEMPLATE

    assert "var productLabelToId = {};" not in TEMPLATE
    assert "var productCodeToLabel = {};" not in TEMPLATE
    assert "productLabelToId = {};" not in TEMPLATE
    assert "productCodeToLabel = {};" not in TEMPLATE
