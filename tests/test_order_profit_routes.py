from datetime import date


def _stub_data_quality(monkeypatch):
    """Patch data_quality.build_for_order_profit to a deterministic payload."""
    def fake_build(*, date_from, date_to, allocated_ad_spend_usd=None):
        return {
            "status": "ok",
            "source_mode": "daily_final",
            "business_date_from": date_from.isoformat(),
            "business_date_to": date_to.isoformat(),
            "generated_at": "2026-05-08T18:30:00",
            "watermarks": {},
            "checks": [],
            "warnings": [],
            "errors": [],
            "_test_allocated": allocated_ad_spend_usd,
        }

    monkeypatch.setattr("web.routes.order_profit.dq.build_for_order_profit", fake_build)
    return fake_build


def test_order_profit_summary_includes_data_quality(authed_client_no_db, monkeypatch):
    import web.routes.order_profit as route

    _stub_data_quality(monkeypatch)
    monkeypatch.setattr(
        route,
        "get_order_profit_status_summary",
        lambda **kw: {
            "summary": {"ok": {"ad_cost": 100.0}, "incomplete": {"ad_cost": 25.0}},
            "overview": {},
        },
    )

    resp = authed_client_no_db.get("/order-profit/api/summary")
    payload = resp.get_json()
    assert payload["data_quality"]["status"] == "ok"
    assert payload["data_quality"]["source_mode"] == "daily_final"
    # 已分摊广告费由 ok + incomplete 求和
    assert payload["data_quality"]["_test_allocated"] == 125.0


def test_order_profit_orders_includes_data_quality(authed_client_no_db, monkeypatch):
    import web.routes.order_profit as route

    _stub_data_quality(monkeypatch)
    monkeypatch.setattr(
        route,
        "get_order_profit_list",
        lambda **kw: [
            {"dxm_package_id": "p1", "ad_cost_total_usd": 30.0},
            {"dxm_package_id": "p2", "ad_cost_total_usd": 20.5},
        ],
    )
    monkeypatch.setattr(
        route, "get_order_profit_summary_for_window", lambda **kw: {"total_orders": 2}
    )

    resp = authed_client_no_db.get(
        "/order-profit/api/orders?from=2026-05-01&to=2026-05-03"
    )
    payload = resp.get_json()
    assert payload["data_quality"]["status"] == "ok"
    assert payload["data_quality"]["_test_allocated"] == 50.5


def test_order_profit_lines_includes_data_quality(authed_client_no_db, monkeypatch):
    import web.routes.order_profit as route

    _stub_data_quality(monkeypatch)
    monkeypatch.setattr(route, "list_order_profit_lines", lambda **kw: [])

    resp = authed_client_no_db.get(
        "/order-profit/api/lines?from=2026-05-01&to=2026-05-03"
    )
    payload = resp.get_json()
    assert payload["data_quality"]["status"] == "ok"
    assert payload["data_quality"]["business_date_from"] == "2026-05-01"


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
            "overview": {
                "line_count": 2,
                "revenue_usd": 100.0,
                "confirmed_profit_usd": 25.0,
                "estimated_profit_usd": 0.0,
                "unallocated_ad_spend_usd": 12.5,
                "total_profit_usd": 12.5,
                "total_margin_pct": 12.5,
            },
            "estimate_marks": {
                "shopify_fee": {
                    "estimated": True,
                    "amount_usd": 3.0,
                    "lines": 2,
                    "label": "策略 C 估算",
                },
                "unallocated_ad_spend": {
                    "estimated": False,
                    "amount_usd": 12.5,
                    "lines": 0,
                    "label": "待配对，已扣入总利润",
                },
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
    assert payload["overview"]["total_profit_usd"] == 12.5
    assert payload["estimate_marks"]["shopify_fee"]["label"] == "策略 C 估算"


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


def test_order_profit_orders_route_passes_product_filter(
    authed_client_no_db,
    monkeypatch,
):
    import web.routes.order_profit as route

    captured = {}

    def fake_list(**kwargs):
        captured["list"] = kwargs
        return []

    def fake_summary(**kwargs):
        captured["summary"] = kwargs
        return {"total_orders": 0, "profit_total_usd": 0}

    monkeypatch.setattr(route, "get_order_profit_list", fake_list)
    monkeypatch.setattr(route, "get_order_profit_summary_for_window", fake_summary)

    resp = authed_client_no_db.get(
        "/order-profit/api/orders?from=2026-05-01&to=2026-05-03"
        "&product_id=123&limit=2&offset=1"
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["filter_product_id"] == 123
    assert captured["list"]["product_id"] == 123
    assert captured["summary"]["product_id"] == 123


def test_order_profit_orders_route_defaults_to_meta_business_date(
    authed_client_no_db,
    monkeypatch,
):
    import web.routes.order_profit as route

    captured = {}

    def fake_list(**kwargs):
        captured["list"] = kwargs
        return []

    def fake_summary(**kwargs):
        captured["summary"] = kwargs
        return {"total_orders": 0}

    monkeypatch.setattr(route, "current_meta_business_date", lambda: date(2026, 5, 7), raising=False)
    monkeypatch.setattr(route, "get_order_profit_list", fake_list)
    monkeypatch.setattr(route, "get_order_profit_summary_for_window", fake_summary)

    resp = authed_client_no_db.get("/order-profit/api/orders")

    assert resp.status_code == 200
    assert captured["list"]["date_from"] == date(2026, 4, 30)
    assert captured["list"]["date_to"] == date(2026, 5, 7)
    assert captured["summary"]["date_from"] == date(2026, 4, 30)
    assert captured["summary"]["date_to"] == date(2026, 5, 7)


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


def test_order_profit_incomplete_products_route_delegates_query(
    authed_client_no_db,
    monkeypatch,
):
    import web.routes.order_profit as route

    captured = {}

    def fake_incomplete_products(**kwargs):
        captured.update(kwargs)
        return [
            {
                "product_id": 7,
                "product_name": "阿尔法产品",
                "product_code": "ALPHA-001",
                "display_label": "阿尔法产品 - ALPHA-001",
                "line_count": 3,
                "missing_fields": ["purchase_price"],
                "medias_search_url": "/medias/?q=ALPHA-001",
            }
        ]

    monkeypatch.setattr(
        route,
        "get_order_profit_incomplete_products",
        fake_incomplete_products,
    )

    resp = authed_client_no_db.get(
        "/order-profit/api/incomplete_products?from=2026-05-01&to=2026-05-03"
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["date_from"] == "2026-05-01"
    assert payload["date_to"] == "2026-05-03"
    assert payload["products"][0]["display_label"] == "阿尔法产品 - ALPHA-001"
    assert captured["date_from"].isoformat() == "2026-05-01"
    assert captured["date_to"].isoformat() == "2026-05-03"


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
