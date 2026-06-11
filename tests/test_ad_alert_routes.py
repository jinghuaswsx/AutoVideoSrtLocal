from __future__ import annotations

from appcore import ad_alerts


def _unwrap(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def test_api_list_serializes_alert_items(monkeypatch):
    from web.routes import ad_alerts as route

    item = ad_alerts.AlertItem(
        product_id=10,
        product_code="ABC123",
        product_name="Demo Product",
        lang="de",
        store_codes=["DE01"],
        ad_spend_usd=100.0,
        purchase_value_usd=40.0,
        ad_roas=0.4,
        active_7d_ad_spend_usd=12.0,
        delivery_status="active",
        ad_roas_7d=0.5,
        computed_at="2026-06-11T08:00:00",
        severity=ad_alerts.Severity.SEVERE,
        trend=ad_alerts.TrendDirection.WORSENING,
        phase=ad_alerts.Phase.STABLE,
        conclusion="建议关停",
        reason="ROAS 低于 1.0",
        estimated_loss=-60.0,
        active_days=10,
    )
    captured: dict[str, object] = {}

    def fake_get_alerts(**kwargs):
        captured.update(kwargs)
        return [item]

    monkeypatch.setattr(route.ad_alerts, "get_alerts", fake_get_alerts)
    from flask import Flask

    flask_app = Flask(__name__)
    with flask_app.test_request_context(
        "/ad-alerts/api/list?threshold=1.4&lang=de&severity=severe&search=ABC"
    ):
        response = _unwrap(route.api_list)()

    payload = response.get_json()
    assert captured["threshold"] == 1.4
    assert captured["lang"] == "de"
    assert captured["severity"] == ad_alerts.Severity.SEVERE
    assert captured["search"] == "ABC"
    assert payload["total"] == 1
    assert payload["items"][0]["severity_label"] == "严重"
    assert payload["items"][0]["active_days"] == 10
    assert payload["items"][0]["estimated_loss"] == -60.0


def test_api_detail_validates_inputs_and_serializes_detail(monkeypatch):
    from web.routes import ad_alerts as route
    from flask import Flask

    judgment = ad_alerts.Judgment(
        severity=ad_alerts.Severity.SEVERE,
        trend=ad_alerts.TrendDirection.STABLE,
        phase=ad_alerts.Phase.STABLE,
        conclusion="建议关停",
        reason="ROAS 低于 1.0",
    )
    detail = ad_alerts.AlertDetail(
        product_id=10,
        product_code="ABC123",
        product_name="Demo Product",
        lang="de",
        lang_label="德语",
        store_codes=["DE01"],
        ad_spend_usd=100.0,
        purchase_value_usd=40.0,
        ad_roas=0.4,
        active_7d_ad_spend_usd=12.0,
        estimated_loss=-60.0,
        delivery_start_time="2026-06-01",
        delivery_end_time="2026-06-10",
        active_days=10,
        computed_at="2026-06-11T08:00:00",
        judgment=judgment,
        trend=[ad_alerts.DailyPoint(date="2026-06-10", spend_usd=10.0, purchase_value_usd=4.0, roas=0.4)],
    )
    monkeypatch.setattr(route.ad_alerts, "get_alert_detail", lambda product_id, lang: detail)

    flask_app = Flask(__name__)
    with flask_app.test_request_context("/ad-alerts/api/detail?product_id=10&lang=de"):
        response = _unwrap(route.api_detail)()

    payload = response.get_json()
    assert payload["detail"]["product_id"] == 10
    assert payload["detail"]["judgment"]["severity_label"] == "严重"
    assert payload["detail"]["trend"][0]["date"] == "2026-06-10"

    with flask_app.test_request_context("/ad-alerts/api/detail?product_id=bad&lang=de"):
        response, status = _unwrap(route.api_detail)()
    assert status == 400
    assert response.get_json()["error"] == "invalid product_id"


def test_api_set_threshold_rejects_invalid_and_persists(monkeypatch):
    from web.routes import ad_alerts as route
    from flask import Flask

    saved: list[float] = []
    monkeypatch.setattr(route.ad_alerts, "set_threshold", lambda value: saved.append(value))

    flask_app = Flask(__name__)
    with flask_app.test_request_context("/ad-alerts/api/threshold", method="POST", json={"threshold": 1.6}):
        response = _unwrap(route.api_set_threshold)()
    assert response.get_json() == {"threshold": 1.6}
    assert saved == [1.6]

    with flask_app.test_request_context("/ad-alerts/api/threshold", method="POST", json={"threshold": 0}):
        response, status = _unwrap(route.api_set_threshold)()
    assert status == 400
    assert response.get_json()["error"] == "threshold must be a positive number"
