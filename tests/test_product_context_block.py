from pipeline.localization import build_product_context_block


def test_empty_context_returns_empty():
    assert build_product_context_block({}) == ""
    assert build_product_context_block(None) == ""
    assert build_product_context_block({"name": "", "selling_points": []}) == ""


def test_minimal_name_only():
    block = build_product_context_block({"name": "Ice Ball Mold"})

    assert "PRODUCT CONTEXT" in block
    assert "Ice Ball Mold" in block
    assert "Official name" not in block


def test_full_context():
    block = build_product_context_block({
        "name": "冰球模具",
        "name_target_lang": "Eisball-Form",
        "category": "Kitchen",
        "selling_points": ["slow melt", "easy release"],
        "brand_terms": ["IceMax"],
    })

    assert "Eisball-Form" in block and "Kitchen" in block
    assert "slow melt; easy release" in block
    assert "IceMax" in block and "Never translate brand terms" in block
