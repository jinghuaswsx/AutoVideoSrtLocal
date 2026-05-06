def test_parcel_cost_suggest_returns_suggestion(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)

    captured = {}

    def fake_suggest(pid, *, days):
        captured["pid"] = pid
        captured["days"] = days
        return {
            "product_id": pid,
            "sku": "45697043366061",
            "dxm_shop_id": "8477915",
            "lookback_days": days,
            "settlement_delay_days": 2,
            "window_start": "2026-04-02",
            "window_end": "2026-05-02",
            "orders_pulled": 720,
            "sample_size": 676,
            "median": 61.61,
            "mean": 61.50,
            "min": 51.78,
            "max": 70.00,
        }

    from appcore import parcel_cost_suggest as mod
    monkeypatch.setattr(mod, "suggest_parcel_cost", fake_suggest)

    resp = authed_client_no_db.get("/medias/api/products/317/parcel-cost-suggest?days=30")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    s = body["suggestion"]
    assert s["sku"] == "45697043366061"
    assert s["median"] == 61.61
    assert s["sample_size"] == 676
    assert captured == {"pid": 317, "days": 30}


def test_parcel_cost_suggest_returns_404_when_no_orders(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)

    from appcore import parcel_cost_suggest as mod

    def fake_suggest(pid, *, days):
        raise mod.ParcelCostSuggestError("no_orders")

    monkeypatch.setattr(mod, "suggest_parcel_cost", fake_suggest)

    resp = authed_client_no_db.get("/medias/api/products/999/parcel-cost-suggest")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["error"] == "no_orders"


def test_parcel_cost_suggest_returns_502_on_dxm_error(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)

    from appcore import parcel_cost_suggest as mod

    def fake_suggest(pid, *, days):
        raise mod.ParcelCostSuggestError("dxm_error:login_expired")

    monkeypatch.setattr(mod, "suggest_parcel_cost", fake_suggest)

    resp = authed_client_no_db.get("/medias/api/products/317/parcel-cost-suggest")
    assert resp.status_code == 502
    body = resp.get_json()
    assert body["error"] == "dxm_failed"
    assert "login_expired" in body["message"]


def test_parcel_cost_suggest_clamps_days(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)

    seen = {}
    from appcore import parcel_cost_suggest as mod

    def fake_suggest(pid, *, days):
        seen["days"] = days
        return {"product_id": pid, "sku": "x", "dxm_shop_id": "y",
                "lookback_days": days, "settlement_delay_days": 2,
                "window_start": "", "window_end": "", "orders_pulled": 0,
                "sample_size": 0, "median": None, "mean": None, "min": None, "max": None}

    monkeypatch.setattr(mod, "suggest_parcel_cost", fake_suggest)

    authed_client_no_db.get("/medias/api/products/1/parcel-cost-suggest?days=1")
    assert seen["days"] == 7
    authed_client_no_db.get("/medias/api/products/1/parcel-cost-suggest?days=999")
    assert seen["days"] == 90
    authed_client_no_db.get("/medias/api/products/1/parcel-cost-suggest?days=abc")
    # invalid_days returns 400 without invoking suggest; seen stays at 90
    assert seen["days"] == 90


def test_parcel_cost_suggest_route_delegates_response_building(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    captured = {}
    converted = {}

    class Result:
        payload = {"ok": True, "suggestion": {"product_id": 317}}
        status_code = 202

    def fake_build(pid, args):
        captured["pid"] = pid
        captured["days"] = args.get("days")
        return Result()

    def fake_flask_response(result):
        converted["payload"] = result.payload
        return {"converted": True, **result.payload}, result.status_code

    monkeypatch.setattr(r, "_build_parcel_cost_suggest_response", fake_build)
    monkeypatch.setattr(r, "_parcel_cost_suggest_flask_response", fake_flask_response)

    resp = authed_client_no_db.get("/medias/api/products/317/parcel-cost-suggest?days=30")

    assert resp.status_code == 202
    assert resp.get_json() == {
        "converted": True,
        "ok": True,
        "suggestion": {"product_id": 317},
    }
    assert captured == {"pid": 317, "days": "30"}
    assert converted == {
        "payload": {"ok": True, "suggestion": {"product_id": 317}},
    }
