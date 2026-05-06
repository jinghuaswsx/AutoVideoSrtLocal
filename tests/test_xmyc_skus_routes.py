def test_list_xmyc_skus_returns_items(authed_client_no_db, monkeypatch):
    from appcore import xmyc_storage as mod

    captured = {}

    def fake_list(**kwargs):
        captured.update(kwargs)
        return [
            {"sku": "115-18103480", "sku_code": "83527156514", "goods_name": "求生多功能锤",
             "unit_price": "16.57", "stock_available": 50, "product_id": None,
             "match_type": None, "warehouse": "小秘云仓-东莞黄江仓"},
            {"sku": "0331-16555368", "sku_code": "83527075155", "goods_name": "全自动水枪 蓝色",
             "unit_price": "54.52", "stock_available": 3, "product_id": 1,
             "match_type": "auto", "warehouse": "小秘云仓-东莞黄江仓"},
        ]

    monkeypatch.setattr(mod, "list_skus", fake_list)

    resp = authed_client_no_db.get("/medias/api/xmyc-skus?keyword=昆虫&matched=unmatched&limit=10&offset=0")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["items"]) == 2
    assert captured["keyword"] == "昆虫"
    assert captured["matched_filter"] == "unmatched"
    assert captured["limit"] == 10
    assert captured["offset"] == 0


def test_list_xmyc_skus_clamps_invalid_pagination(authed_client_no_db, monkeypatch):
    from appcore import xmyc_storage as mod
    monkeypatch.setattr(mod, "list_skus", lambda **kw: [])
    resp = authed_client_no_db.get("/medias/api/xmyc-skus?limit=abc")
    assert resp.status_code == 400


def test_set_product_xmyc_skus_writes_assignment(authed_client_no_db, monkeypatch):
    from web.routes import medias as r
    from appcore import xmyc_storage as mod

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)

    captured = {}

    def fake_set(product_id, skus, *, matched_by=None):
        captured["product_id"] = product_id
        captured["skus"] = skus
        captured["matched_by"] = matched_by
        return {"product_id": product_id, "cleared": 0, "attached": len(skus), "purchase_price": 16.57}

    monkeypatch.setattr(mod, "set_product_skus", fake_set)

    resp = authed_client_no_db.post(
        "/medias/api/products/317/xmyc-skus",
        json={"skus": ["115-18103480", "  ", "0331-16555368"]},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["attached"] == 2
    assert body["purchase_price"] == 16.57
    assert captured["product_id"] == 317
    assert captured["skus"] == ["115-18103480", "0331-16555368"]


def test_set_product_xmyc_skus_rejects_non_list(authed_client_no_db, monkeypatch):
    from web.routes import medias as r
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    resp = authed_client_no_db.post(
        "/medias/api/products/1/xmyc-skus",
        json={"skus": "not-a-list"},
    )
    assert resp.status_code == 400


def test_get_product_xmyc_skus_returns_attached(authed_client_no_db, monkeypatch):
    from web.routes import medias as r
    from appcore import xmyc_storage as mod

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(mod, "get_skus_for_product", lambda pid: [
        {"sku": "115-18103480", "unit_price": "16.57", "match_type": "manual"},
    ])
    resp = authed_client_no_db.get("/medias/api/products/317/xmyc-skus")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["items"]) == 1
    assert body["items"][0]["match_type"] == "manual"


def test_update_xmyc_sku_writes_field(authed_client_no_db, monkeypatch):
    from appcore import xmyc_storage as mod

    def fake_update(sku_id, fields):
        return {
            "id": sku_id, "sku": "115-18103480", "sku_code": "83527156514",
            "goods_name": "求生多功能锤", "unit_price": "16.57",
            "standalone_price_sku": "25.00",
            "standalone_shipping_fee_sku": None,
            "packet_cost_actual_sku": None,
        }

    monkeypatch.setattr(mod, "update_sku", fake_update)
    resp = authed_client_no_db.patch(
        "/medias/api/xmyc-skus/42",
        json={"standalone_price_sku": "25.00"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["item"]["standalone_price_sku"] == "25.00"


def test_update_xmyc_sku_rejects_invalid_field(authed_client_no_db, monkeypatch):
    from appcore import xmyc_storage as mod

    def fake_update(sku_id, fields):
        raise ValueError("invalid decimal for standalone_price_sku: 'abc'")

    monkeypatch.setattr(mod, "update_sku", fake_update)
    resp = authed_client_no_db.patch(
        "/medias/api/xmyc-skus/42",
        json={"standalone_price_sku": "abc"},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert "invalid_fields" in body.get("error", "")


def test_update_xmyc_sku_404_on_missing(authed_client_no_db, monkeypatch):
    from appcore import xmyc_storage as mod

    def fake_update(sku_id, fields):
        raise LookupError("not found")

    monkeypatch.setattr(mod, "update_sku", fake_update)
    resp = authed_client_no_db.patch(
        "/medias/api/xmyc-skus/99999",
        json={"standalone_shipping_fee_sku": "5.00"},
    )
    assert resp.status_code == 404


def test_xmyc_skus_list_route_delegates_response_building(authed_client_no_db, monkeypatch):
    captured = {}
    converted = {}

    class Result:
        payload = {"ok": True, "items": [{"sku": "S1"}], "limit": 10, "offset": 0}
        status_code = 206

    def fake_build(args):
        captured["keyword"] = args.get("keyword")
        return Result()

    def fake_flask_response(result):
        converted["payload"] = result.payload
        return {"converted": True, **result.payload}, result.status_code

    monkeypatch.setattr("web.routes.medias._build_xmyc_skus_list_response", fake_build)
    monkeypatch.setattr("web.routes.medias._xmyc_sku_flask_response", fake_flask_response)

    resp = authed_client_no_db.get("/medias/api/xmyc-skus?keyword=fan")

    assert resp.status_code == 206
    assert resp.get_json()["converted"] is True
    assert resp.get_json()["items"] == [{"sku": "S1"}]
    assert captured == {"keyword": "fan"}
    assert converted == {
        "payload": {"ok": True, "items": [{"sku": "S1"}], "limit": 10, "offset": 0},
    }


def test_product_xmyc_skus_get_route_delegates_response_building(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    captured = {}
    converted = {}

    class Result:
        payload = {"ok": True, "items": [{"sku": "S1"}]}
        status_code = 207

    def fake_build(pid):
        captured["pid"] = pid
        return Result()

    def fake_flask_response(result):
        converted["payload"] = result.payload
        return {"converted": True, **result.payload}, result.status_code

    monkeypatch.setattr(r, "_build_product_xmyc_skus_response", fake_build)
    monkeypatch.setattr(r, "_xmyc_sku_flask_response", fake_flask_response)

    resp = authed_client_no_db.get("/medias/api/products/317/xmyc-skus")

    assert resp.status_code == 207
    assert resp.get_json()["converted"] is True
    assert resp.get_json()["items"] == [{"sku": "S1"}]
    assert captured == {"pid": 317}
    assert converted == {"payload": {"ok": True, "items": [{"sku": "S1"}]}}


def test_product_xmyc_skus_set_route_delegates_response_building(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    captured = {}
    converted = {}

    class Result:
        payload = {"ok": True, "attached": 1}
        status_code = 208

    def fake_build(pid, body, *, matched_by):
        captured["pid"] = pid
        captured["body"] = body
        captured["matched_by"] = matched_by
        return Result()

    def fake_flask_response(result):
        converted["payload"] = result.payload
        return {"converted": True, **result.payload}, result.status_code

    monkeypatch.setattr(r, "_build_product_xmyc_skus_set_response", fake_build)
    monkeypatch.setattr(r, "_xmyc_sku_flask_response", fake_flask_response)

    resp = authed_client_no_db.post(
        "/medias/api/products/317/xmyc-skus",
        json={"skus": ["S1"]},
    )

    assert resp.status_code == 208
    assert resp.get_json() == {"converted": True, "ok": True, "attached": 1}
    assert captured["pid"] == 317
    assert captured["body"] == {"skus": ["S1"]}
    assert isinstance(captured["matched_by"], int)
    assert converted == {"payload": {"ok": True, "attached": 1}}


def test_xmyc_sku_update_route_delegates_response_building(authed_client_no_db, monkeypatch):
    captured = {}
    converted = {}

    class Result:
        payload = {"ok": True, "item": {"id": 42}}
        status_code = 209
        not_found = False

    def fake_build(sku_id, body):
        captured["sku_id"] = sku_id
        captured["body"] = body
        return Result()

    def fake_flask_response(result):
        converted["payload"] = result.payload
        return {"converted": True, **result.payload}, result.status_code

    monkeypatch.setattr("web.routes.medias._build_xmyc_sku_update_response", fake_build)
    monkeypatch.setattr("web.routes.medias._xmyc_sku_flask_response", fake_flask_response)

    resp = authed_client_no_db.patch(
        "/medias/api/xmyc-skus/42",
        json={"standalone_price_sku": "25.00"},
    )

    assert resp.status_code == 209
    assert resp.get_json() == {"converted": True, "ok": True, "item": {"id": 42}}
    assert captured == {"sku_id": 42, "body": {"standalone_price_sku": "25.00"}}
    assert converted == {"payload": {"ok": True, "item": {"id": 42}}}
