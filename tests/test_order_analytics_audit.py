from __future__ import annotations

import io
from types import SimpleNamespace


def test_shopify_order_upload_records_audit(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as route_mod

    calls = []
    monkeypatch.setattr(route_mod.oa, "parse_shopify_file", lambda stream, filename: [{"row": 1}])
    monkeypatch.setattr(route_mod.oa, "import_orders", lambda rows: {"imported": 2, "skipped": 1})
    monkeypatch.setattr(route_mod.oa, "match_orders_to_products", lambda: 2)
    monkeypatch.setattr(
        route_mod.oa,
        "get_import_stats",
        lambda: {
            "total_rows": 3,
            "product_count": 2,
            "country_count": 1,
            "matched_rows": 2,
            "min_date": None,
            "max_date": None,
        },
    )
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    response = authed_client_no_db.post(
        "/order-analytics/upload",
        data={"file": (io.BytesIO(b"orders"), "orders.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert calls[0]["action"] == "order_analytics_shopify_orders_uploaded"
    assert calls[0]["module"] == "order_analytics"
    assert calls[0]["target_type"] == "order_import"
    assert calls[0]["target_label"] == "orders.csv"
    assert calls[0]["detail"]["imported"] == 2
    assert calls[0]["detail"]["skipped"] == 1
    assert calls[0]["detail"]["matched"] == 2


def test_meta_ad_upload_records_audit(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as route_mod

    calls = []
    monkeypatch.setattr(route_mod.oa, "parse_meta_ad_file", lambda stream, filename: [{"campaign_name": "demo"}])
    monkeypatch.setattr(
        route_mod.oa,
        "import_meta_ad_rows",
        lambda rows, filename, file_bytes, import_frequency: {
            "batch_id": 9,
            "imported": 1,
            "updated": 0,
            "skipped": 0,
            "matched": 1,
        },
    )
    monkeypatch.setattr(route_mod.oa, "get_meta_ad_stats", lambda: {"total_rows": 1, "matched_rows": 1})
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    response = authed_client_no_db.post(
        "/order-analytics/ad-upload",
        data={
            "frequency": "weekly",
            "file": (io.BytesIO(b"meta"), "meta.csv"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert calls[0]["action"] == "order_analytics_meta_ads_uploaded"
    assert calls[0]["module"] == "order_analytics"
    assert calls[0]["target_type"] == "meta_ad_import"
    assert calls[0]["target_id"] == 9
    assert calls[0]["target_label"] == "meta.csv"
    assert calls[0]["detail"]["frequency"] == "weekly"
    assert calls[0]["detail"]["imported"] == 1


def test_matching_and_refresh_actions_record_audit(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as route_mod

    calls = []
    monkeypatch.setattr(route_mod.oa, "match_orders_to_products", lambda: 3)
    monkeypatch.setattr(route_mod.oa, "match_meta_ads_to_products", lambda: 4)
    monkeypatch.setattr(route_mod.oa, "refresh_product_titles", lambda product_ids: {"updated": 2, "product_ids": product_ids})
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    assert authed_client_no_db.post("/order-analytics/match").status_code == 200
    assert authed_client_no_db.post("/order-analytics/ad-match").status_code == 200
    assert authed_client_no_db.post(
        "/order-analytics/refresh-titles",
        json={"product_ids": [11, 12]},
    ).status_code == 200

    assert [call["action"] for call in calls] == [
        "order_analytics_orders_matched",
        "order_analytics_meta_ads_matched",
        "order_analytics_product_titles_refreshed",
    ]
    assert calls[0]["detail"]["matched"] == 3
    assert calls[1]["detail"]["matched"] == 4
    assert calls[2]["detail"]["product_ids"] == [11, 12]
    assert calls[2]["detail"]["updated"] == 2


def test_meta_ad_manual_match_success_records_audit(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as route_mod

    calls = []

    def fake_manual_match(normalized_campaign_code, product_id):
        return {
            "matched_periodic": 2,
            "matched_daily": 5,
            "product_id": product_id,
            "product_code": "glow-go-insect-set-rjc",
            "product_name": "Glow Set",
        }

    monkeypatch.setattr(route_mod.oa, "manual_match_meta_ad_campaign", fake_manual_match)
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    response = authed_client_no_db.post(
        "/order-analytics/ad-match-manual",
        json={"normalized_campaign_code": "glow-go-insect-set-rjc", "product_id": 42},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["matched_periodic"] == 2
    assert body["matched_daily"] == 5
    assert body["product_code"] == "glow-go-insect-set-rjc"

    assert calls[0]["action"] == "order_analytics_meta_ad_manual_matched"
    assert calls[0]["target_type"] == "meta_ad_campaign"
    assert calls[0]["target_label"] == "glow-go-insect-set-rjc"
    assert calls[0]["status"] == "success"
    assert calls[0]["detail"]["matched_periodic"] == 2
    assert calls[0]["detail"]["matched_daily"] == 5


def test_meta_ad_manual_match_missing_params_returns_400(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as route_mod

    monkeypatch.setattr(
        route_mod.oa,
        "manual_match_meta_ad_campaign",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    r1 = authed_client_no_db.post(
        "/order-analytics/ad-match-manual",
        json={"normalized_campaign_code": "", "product_id": 42},
    )
    assert r1.status_code == 400

    r2 = authed_client_no_db.post(
        "/order-analytics/ad-match-manual",
        json={"normalized_campaign_code": "code", "product_id": "abc"},
    )
    assert r2.status_code == 400

    r3 = authed_client_no_db.post(
        "/order-analytics/ad-match-manual",
        json={"normalized_campaign_code": "code", "product_id": 0},
    )
    assert r3.status_code == 400


def test_meta_ad_manual_match_product_missing_returns_404(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as route_mod

    calls = []

    def boom(normalized_campaign_code, product_id):
        raise LookupError(f"product {product_id} not found or deleted")

    monkeypatch.setattr(route_mod.oa, "manual_match_meta_ad_campaign", boom)
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    response = authed_client_no_db.post(
        "/order-analytics/ad-match-manual",
        json={"normalized_campaign_code": "code-x", "product_id": 999},
    )

    assert response.status_code == 404
    assert calls[0]["action"] == "order_analytics_meta_ad_manual_matched"
    assert calls[0]["status"] == "failure"
    assert "product 999 not found" in calls[0]["detail"]["error"]


def test_dianxiaomi_import_records_success_and_failure_audit(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as route_mod

    calls = []

    def fake_run_import_from_server_browser_locked(**kwargs):
        return {"status": "success", "inserted_lines": 5, "updated_lines": 1}

    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )
    monkeypatch.setattr(
        "tools.dianxiaomi_order_import.run_import_from_server_browser_locked",
        fake_run_import_from_server_browser_locked,
    )

    response = authed_client_no_db.post(
        "/order-analytics/dianxiaomi-import",
        json={
            "start_date": "2026-04-01",
            "end_date": "2026-04-02",
            "site_codes": ["newjoy"],
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    assert calls[0]["action"] == "order_analytics_dianxiaomi_import_run"
    assert calls[0]["module"] == "order_analytics"
    assert calls[0]["target_type"] == "dianxiaomi_import"
    assert calls[0]["status"] == "success"
    assert calls[0]["detail"]["start_date"] == "2026-04-01"
    assert calls[0]["detail"]["site_codes"] == ["newjoy"]
    assert calls[0]["detail"]["dry_run"] is False
    assert calls[0]["detail"]["inserted_lines"] == 5

    def boom(**_kwargs):
        raise RuntimeError("dxm down")

    monkeypatch.setattr("tools.dianxiaomi_order_import.run_import_from_server_browser_locked", boom)

    response = authed_client_no_db.post("/order-analytics/dianxiaomi-import", json={})

    assert response.status_code == 500
    assert calls[1]["action"] == "order_analytics_dianxiaomi_import_run"
    assert calls[1]["status"] == "failure"
    assert calls[1]["detail"]["error"] == "dxm down"
