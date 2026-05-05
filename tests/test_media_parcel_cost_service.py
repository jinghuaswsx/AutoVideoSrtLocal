from __future__ import annotations


def test_build_parcel_cost_suggest_response_clamps_days_and_returns_suggestion():
    from appcore import parcel_cost_suggest
    from web.services.media_parcel_cost import build_parcel_cost_suggest_response

    captured = {}

    result = build_parcel_cost_suggest_response(
        42,
        {"days": "999"},
        default_lookback_days=parcel_cost_suggest.DEFAULT_LOOKBACK_DAYS,
        error_type=parcel_cost_suggest.ParcelCostSuggestError,
        suggest_parcel_cost_fn=lambda pid, *, days: captured.update({"pid": pid, "days": days})
        or {"product_id": pid, "lookback_days": days},
    )

    assert result.status_code == 200
    assert result.payload == {
        "ok": True,
        "suggestion": {"product_id": 42, "lookback_days": 90},
    }
    assert captured == {"pid": 42, "days": 90}


def test_build_parcel_cost_suggest_response_rejects_invalid_days_before_lookup():
    from appcore import parcel_cost_suggest
    from web.services.media_parcel_cost import build_parcel_cost_suggest_response

    called = []

    result = build_parcel_cost_suggest_response(
        42,
        {"days": "abc"},
        default_lookback_days=parcel_cost_suggest.DEFAULT_LOOKBACK_DAYS,
        error_type=parcel_cost_suggest.ParcelCostSuggestError,
        suggest_parcel_cost_fn=lambda *args, **kwargs: called.append("suggest"),
    )

    assert result.status_code == 400
    assert result.payload == {"error": "invalid_days"}
    assert called == []


def test_build_parcel_cost_suggest_response_maps_no_orders_and_dxm_errors():
    from appcore import parcel_cost_suggest
    from web.services.media_parcel_cost import build_parcel_cost_suggest_response

    def no_orders(_pid, *, days):
        raise parcel_cost_suggest.ParcelCostSuggestError("no_orders")

    def dxm_error(_pid, *, days):
        raise parcel_cost_suggest.ParcelCostSuggestError("dxm_error:login_expired")

    no_orders_result = build_parcel_cost_suggest_response(
        42,
        {},
        default_lookback_days=30,
        error_type=parcel_cost_suggest.ParcelCostSuggestError,
        suggest_parcel_cost_fn=no_orders,
    )
    dxm_result = build_parcel_cost_suggest_response(
        42,
        {},
        default_lookback_days=30,
        error_type=parcel_cost_suggest.ParcelCostSuggestError,
        suggest_parcel_cost_fn=dxm_error,
    )

    assert no_orders_result.status_code == 404
    assert no_orders_result.payload["error"] == "no_orders"
    assert dxm_result.status_code == 502
    assert dxm_result.payload == {
        "error": "dxm_failed",
        "message": "dxm_error:login_expired",
    }


def test_build_parcel_cost_suggest_response_maps_unexpected_errors():
    from appcore import parcel_cost_suggest
    from web.services.media_parcel_cost import build_parcel_cost_suggest_response

    result = build_parcel_cost_suggest_response(
        42,
        {},
        default_lookback_days=parcel_cost_suggest.DEFAULT_LOOKBACK_DAYS,
        error_type=parcel_cost_suggest.ParcelCostSuggestError,
        suggest_parcel_cost_fn=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert result.status_code == 502
    assert result.payload == {"error": "dxm_failed", "message": "boom"}
