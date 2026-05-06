def test_order_profit_summary_route_uses_aggregate_payload(authed_client_no_db, monkeypatch):
    import web.routes.order_profit as route

    def fake_query(sql, args=()):
        if "GROUP BY status" in sql:
            return [
                {
                    "status": "ok",
                    "n": 2,
                    "revenue": 100,
                    "profit": 25,
                    "shopify_fee": 3,
                    "ad_cost": 10,
                    "purchase": 40,
                    "shipping_cost": 7,
                    "return_reserve": 1,
                }
            ]
        if "FROM order_profit_runs" in sql:
            return [{"unallocated_ad_spend_usd": 12.5}]
        return []

    monkeypatch.setattr(route, "query", fake_query)

    resp = authed_client_no_db.get("/order-profit/api/summary")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["summary"]["ok"]["lines"] == 2
    assert payload["summary"]["ok"]["profit"] == 25.0
    assert payload["unallocated_ad_spend_usd"] == 12.5
    assert payload["margin_pct"] == 25.0


def test_order_profit_detail_missing_returns_404(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.order_profit.get_order_profit_detail",
        lambda dxm_package_id: None,
    )

    resp = authed_client_no_db.get("/order-profit/api/orders/pkg-404")

    assert resp.status_code == 404
    assert resp.get_json() == {
        "error": "order_not_found",
        "dxm_package_id": "pkg-404",
    }


def test_order_profit_import_rejects_missing_file(authed_client_no_db):
    resp = authed_client_no_db.post("/order-profit/api/payments_csv/import")

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "缺少 file 字段"


def test_order_profit_manual_match_routes_are_no_db(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.order_profit.create_override",
        lambda **kwargs: {"id": 9, **kwargs},
    )
    monkeypatch.setattr(
        "web.routes.order_profit.remove_override",
        lambda override_id: {"removed": override_id},
    )

    missing = authed_client_no_db.post("/order-profit/api/manual_match", json={})
    assert missing.status_code == 400
    assert "error" in missing.get_json()

    created = authed_client_no_db.post(
        "/order-profit/api/manual_match",
        json={
            "normalized_campaign_code": "ABC",
            "product_id": 123,
            "reason": "manual test",
        },
    )
    assert created.status_code == 200
    assert created.get_json()["override"]["product_id"] == 123

    deleted = authed_client_no_db.delete("/order-profit/api/manual_match/9")
    assert deleted.status_code == 200
    assert deleted.get_json() == {"ok": True, "removed": 9}
