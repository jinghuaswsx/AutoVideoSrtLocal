def test_order_profit_summary_route_uses_aggregate_payload(authed_client_no_db, monkeypatch):
    import web.routes.order_profit as route

    monkeypatch.setattr(
        route,
        "get_order_profit_status_summary",
        lambda **kwargs: {
            "date_from": kwargs["date_from"].isoformat(),
            "date_to": kwargs["date_to"].isoformat(),
            "summary": {
                "ok": {"lines": 2, "profit": 25.0},
                "incomplete": {"lines": 0, "profit": 0},
            },
            "unallocated_ad_spend_usd": 12.5,
            "margin_pct": 25.0,
        },
    )

    resp = authed_client_no_db.get("/order-profit/api/summary")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["summary"]["ok"]["lines"] == 2
    assert payload["summary"]["ok"]["profit"] == 25.0
    assert payload["unallocated_ad_spend_usd"] == 12.5
    assert payload["margin_pct"] == 25.0


def test_order_profit_lines_route_delegates_query(authed_client_no_db, monkeypatch):
    import web.routes.order_profit as route

    captured = {}

    def fake_list_order_profit_lines(**kwargs):
        captured.update(kwargs)
        return [{"id": 7, "status": "incomplete"}]

    monkeypatch.setattr(route, "list_order_profit_lines", fake_list_order_profit_lines)

    resp = authed_client_no_db.get(
        "/order-profit/api/lines?from=2026-05-01&to=2026-05-03"
        "&status=incomplete&limit=2&offset=1"
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["lines"] == [{"id": 7, "status": "incomplete"}]
    assert payload["limit"] == 2
    assert payload["offset"] == 1
    assert captured["date_from"].isoformat() == "2026-05-01"
    assert captured["date_to"].isoformat() == "2026-05-03"
    assert captured["status"] == "incomplete"
    assert captured["limit"] == 2
    assert captured["offset"] == 1


def test_order_profit_loss_alerts_route_delegates_query(authed_client_no_db, monkeypatch):
    import web.routes.order_profit as route

    captured = {}

    def fake_loss_alerts(**kwargs):
        captured.update(kwargs)
        return {
            "date_from": kwargs["date_from"].isoformat(),
            "date_to": kwargs["date_to"].isoformat(),
            "loss_lines": [{"product_id": 1}],
            "loss_count": 1,
            "total_loss_usd": -3.5,
        }

    monkeypatch.setattr(route, "get_order_profit_loss_alerts", fake_loss_alerts)

    resp = authed_client_no_db.get(
        "/order-profit/api/loss_alerts?from=2026-05-01&to=2026-05-02&limit=5"
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["loss_lines"] == [{"product_id": 1}]
    assert payload["total_loss_usd"] == -3.5
    assert captured["limit"] == 5


def test_order_profit_products_for_match_route_delegates_query(
    authed_client_no_db,
    monkeypatch,
):
    import web.routes.order_profit as route

    monkeypatch.setattr(
        route,
        "list_products_for_manual_match",
        lambda: [{"id": 1, "product_code": "alpha", "name": "Alpha"}],
    )

    resp = authed_client_no_db.get("/order-profit/api/products_for_match")

    assert resp.status_code == 200
    assert resp.get_json()["products"] == [
        {"id": 1, "product_code": "alpha", "name": "Alpha"}
    ]


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


def test_order_profit_import_sanitizes_source_filename(authed_client_no_db, monkeypatch):
    import io

    captured = {}

    def fake_import_payments_csv(stream, *, source_csv):
        captured["content"] = stream.read()
        captured["source_csv"] = source_csv
        return {"inserted": 1}

    monkeypatch.setattr(
        "web.routes.order_profit.import_payments_csv",
        fake_import_payments_csv,
    )

    resp = authed_client_no_db.post(
        "/order-profit/api/payments_csv/import",
        data={"file": (io.BytesIO(b"amount\n1"), "..\\..\\payments.csv")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    assert captured["source_csv"] == "payments.csv"
    assert captured["content"] == "amount\n1"


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
