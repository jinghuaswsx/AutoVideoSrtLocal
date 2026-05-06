from __future__ import annotations


def test_build_xmyc_skus_list_response_normalizes_pagination_and_enriches_rows():
    from web.services.media_xmyc_skus import build_xmyc_skus_list_response

    captured = {}

    def fake_list(**kwargs):
        captured["list"] = kwargs
        return [{"sku": "S1", "unit_price": "16.57"}]

    def fake_enrich(rows, rate):
        captured["enrich"] = (rows, rate)
        return [{**rows[0], "roas": {"rate": rate}}]

    result = build_xmyc_skus_list_response(
        {"keyword": "  fan  ", "matched": "bad", "limit": "999", "offset": "-3"},
        list_skus_fn=fake_list,
        get_configured_rmb_per_usd_fn=lambda: 7.1,
        enrich_skus_with_roas_fn=fake_enrich,
    )

    assert result.status_code == 200
    assert result.payload == {
        "ok": True,
        "items": [{"sku": "S1", "unit_price": "16.57", "roas": {"rate": 7.1}}],
        "limit": 500,
        "offset": 0,
    }
    assert captured["list"] == {
        "keyword": "fan",
        "matched_filter": "all",
        "limit": 500,
        "offset": 0,
    }
    assert captured["enrich"][1] == 7.1


def test_build_xmyc_skus_list_response_rejects_invalid_pagination_before_lookup():
    from web.services.media_xmyc_skus import build_xmyc_skus_list_response

    called = []

    result = build_xmyc_skus_list_response(
        {"limit": "abc"},
        list_skus_fn=lambda **kwargs: called.append("list"),
        get_configured_rmb_per_usd_fn=lambda: 7.1,
        enrich_skus_with_roas_fn=lambda rows, rate: called.append("enrich"),
    )

    assert result.status_code == 400
    assert result.payload == {"error": "invalid_pagination"}
    assert called == []


def test_build_product_xmyc_skus_response_enriches_attached_rows():
    from web.services.media_xmyc_skus import build_product_xmyc_skus_response

    result = build_product_xmyc_skus_response(
        42,
        get_skus_for_product_fn=lambda pid: [{"sku": f"S{pid}"}],
        get_configured_rmb_per_usd_fn=lambda: 6.9,
        enrich_skus_with_roas_fn=lambda rows, rate: [{**rows[0], "rate": rate}],
    )

    assert result.status_code == 200
    assert result.payload == {"ok": True, "items": [{"sku": "S42", "rate": 6.9}]}


def test_build_product_xmyc_skus_set_response_validates_and_strips_skus():
    from web.services.media_xmyc_skus import build_product_xmyc_skus_set_response

    captured = {}

    result = build_product_xmyc_skus_set_response(
        42,
        {"skus": [" S1 ", "", "S2"]},
        matched_by=7,
        set_product_skus_fn=lambda pid, skus, matched_by=None: captured.update(
            {"pid": pid, "skus": skus, "matched_by": matched_by}
        )
        or {"attached": len(skus)},
    )

    assert result.status_code == 200
    assert result.payload == {"ok": True, "attached": 2}
    assert captured == {"pid": 42, "skus": ["S1", "S2"], "matched_by": 7}


def test_build_product_xmyc_skus_set_response_rejects_non_list_before_write():
    from web.services.media_xmyc_skus import build_product_xmyc_skus_set_response

    called = []

    result = build_product_xmyc_skus_set_response(
        42,
        {"skus": "S1"},
        matched_by=7,
        set_product_skus_fn=lambda *args, **kwargs: called.append("set"),
    )

    assert result.status_code == 400
    assert result.payload == {"error": "skus_must_be_list"}
    assert called == []


def test_build_xmyc_sku_update_response_enriches_updated_row_and_maps_errors():
    from web.services.media_xmyc_skus import build_xmyc_sku_update_response

    ok = build_xmyc_sku_update_response(
        5,
        {"standalone_price_sku": "25.00"},
        update_sku_fn=lambda sku_id, body: {"id": sku_id, "sku": "S1", **body},
        get_configured_rmb_per_usd_fn=lambda: 6.8,
        enrich_skus_with_roas_fn=lambda rows, rate: [{**rows[0], "rate": rate}],
    )
    invalid = build_xmyc_sku_update_response(
        5,
        {"standalone_price_sku": "abc"},
        update_sku_fn=lambda sku_id, body: (_ for _ in ()).throw(ValueError("bad decimal")),
    )
    missing = build_xmyc_sku_update_response(
        5,
        {},
        update_sku_fn=lambda sku_id, body: (_ for _ in ()).throw(LookupError("missing")),
    )

    assert ok.status_code == 200
    assert ok.payload == {
        "ok": True,
        "item": {"id": 5, "sku": "S1", "standalone_price_sku": "25.00", "rate": 6.8},
    }
    assert invalid.status_code == 400
    assert invalid.payload == {"error": "invalid_fields", "message": "bad decimal"}
    assert missing.status_code == 404
    assert missing.not_found is True


def test_xmyc_sku_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.media_xmyc_skus import XmycSkuResponse, xmyc_sku_flask_response

    with authed_client_no_db.application.app_context():
        response, status_code = xmyc_sku_flask_response(
            XmycSkuResponse({"ok": True, "items": [{"sku": "S1"}]}, 206)
        )

    assert status_code == 206
    assert response.get_json() == {"ok": True, "items": [{"sku": "S1"}]}
