from __future__ import annotations


def test_build_roas_page_context_serializes_product_and_injects_rate():
    from web.services.media_roas_page import build_roas_page_context

    product = {"id": 6, "product_code": "demo-rjc"}

    context = build_roas_page_context(
        product,
        serialize_product_fn=lambda row: {"id": row["id"], "code": row["product_code"]},
        get_rmb_per_usd_fn=lambda: "6.83",
    )

    assert context == {
        "product": {"id": 6, "code": "demo-rjc"},
        "roas_rmb_per_usd": "6.83",
    }
