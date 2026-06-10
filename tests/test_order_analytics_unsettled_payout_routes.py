import io
import sys
import types
from pathlib import Path

import pytest


pytestmark = pytest.mark.allow_shopify_browser_automation


@pytest.fixture
def unsettled_client(monkeypatch):
    from flask import Flask

    fake_background = types.ModuleType("web.background")
    fake_background.start_background_task = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "web.background", fake_background)
    fake_unmatched_details = types.ModuleType("appcore.order_analytics.unmatched_details")
    fake_unmatched_details.enrich_rows = lambda rows, *args, **kwargs: rows
    monkeypatch.setitem(
        sys.modules,
        "appcore.order_analytics.unmatched_details",
        fake_unmatched_details,
    )
    fake_weekly_ai_report = types.ModuleType("appcore.order_analytics.weekly_ai_report")
    monkeypatch.setitem(
        sys.modules,
        "appcore.order_analytics.weekly_ai_report",
        fake_weekly_ai_report,
    )

    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: {
            "id": 1,
            "username": "admin",
            "role": "admin",
            "is_active": 1,
        } if int(user_id) == 1 else None,
    )

    from web.auth import login_manager
    from web.routes.order_analytics import bp

    app = Flask(__name__)
    app.secret_key = "test"
    app.config.update(TESTING=True)
    login_manager.init_app(app)
    app.register_blueprint(bp)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    return client


def _sample_project(project_id=11):
    return {
        "id": project_id,
        "store_code": "newjoy",
        "project_name": "2026-06-10 Payments",
        "source_filename": "payments.csv",
        "currency": "USD",
        "imported_row_count": 3,
        "included_row_count": 3,
        "ignored_row_count": 0,
        "created_at": "2026-06-10T10:30:00",
        "updated_at": "2026-06-10T10:30:00",
        "summary": {
            "currency": "USD",
            "buckets": {
                "pending": {
                    "label": "未结算订单",
                    "net_label": "预计打款总额",
                    "order_count": 1,
                    "amount_total": 21.83,
                    "fee_total": 1.52,
                    "net_total": 20.31,
                },
                "paid": {
                    "label": "已结算订单",
                    "net_label": "已打款总额",
                    "order_count": 1,
                    "amount_total": 41.58,
                    "fee_total": 3.78,
                    "net_total": 37.8,
                },
                "scheduled": {
                    "label": "已排期订单",
                    "net_label": "已排期打款总额",
                    "order_count": 1,
                    "amount_total": 36.76,
                    "fee_total": 2.13,
                    "net_total": 34.63,
                },
            },
        },
    }


def test_unsettled_payout_template_contains_tab_and_fetch_wiring():
    html = Path("web/templates/order_analytics.html").read_text(encoding="utf-8")

    assert 'data-tab="unsettledPayouts"' in html
    assert 'id="panelUnsettledPayouts"' in html
    assert "/order-analytics/unsettled-payouts/projects" in html
    assert "X-CSRFToken" in html
    assert "未结算货款" in html


def test_unsettled_payout_projects_api_returns_projects(unsettled_client, monkeypatch):
    project = _sample_project()

    monkeypatch.setattr(
        "web.routes.order_analytics.shopify_unsettled_payouts.list_projects",
        lambda limit=100: {
            "projects": [project],
            "summary": {"project_count": 1, "buckets": project["summary"]["buckets"]},
        },
    )

    response = unsettled_client.get("/order-analytics/unsettled-payouts/projects")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["projects"][0]["project_name"] == "2026-06-10 Payments"


def test_unsettled_payout_project_detail_forwards_status_filter(unsettled_client, monkeypatch):
    calls = {}

    def fake_detail(project_id, *, status, page, page_size):
        calls.update({"project_id": project_id, "status": status, "page": page, "page_size": page_size})
        return {
            "project": _sample_project(project_id),
            "rows": [],
            "page": {"page": page, "page_size": page_size, "total": 0, "pages": 0},
        }

    monkeypatch.setattr(
        "web.routes.order_analytics.shopify_unsettled_payouts.get_project_detail",
        fake_detail,
    )

    response = unsettled_client.get(
        "/order-analytics/unsettled-payouts/projects/9?status=pending&page=2&page_size=50"
    )

    assert response.status_code == 200
    assert response.get_json()["project"]["id"] == 9
    assert calls == {"project_id": 9, "status": "pending", "page": 2, "page_size": 50}


def test_unsettled_payout_project_create_sanitizes_filename_and_uses_service(
    unsettled_client,
    monkeypatch,
):
    calls = {}

    def fake_create_project_from_file(**kwargs):
        calls.update(kwargs)
        return _sample_project(22)

    monkeypatch.setattr(
        "web.routes.order_analytics.shopify_unsettled_payouts.create_project_from_file",
        fake_create_project_from_file,
    )
    monkeypatch.setattr(
        "web.routes.order_analytics._audit_order_analytics_action",
        lambda *args, **kwargs: None,
    )

    response = unsettled_client.post(
        "/order-analytics/unsettled-payouts/projects",
        data={
            "store_code": "newjoy",
            "project_name": "June payout",
            "file": (io.BytesIO(b"Payout Status,Amount,Fee,Net\npending,1,0.1,0.9\n"), "../payments.csv"),
        },
        headers={"X-CSRFToken": "test-token"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["project"]["id"] == 22
    assert calls["store_code"] == "newjoy"
    assert calls["project_name"] == "June payout"
    assert calls["filename"] == "payments.csv"
    assert calls["content"].startswith(b"Payout Status,Amount,Fee,Net")


def test_unsettled_payout_project_create_rejects_unknown_store(unsettled_client):
    response = unsettled_client.post(
        "/order-analytics/unsettled-payouts/projects",
        data={
            "store_code": "unknown",
            "file": (io.BytesIO(b""), "payments.csv"),
        },
        headers={"X-CSRFToken": "test-token"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_store"
