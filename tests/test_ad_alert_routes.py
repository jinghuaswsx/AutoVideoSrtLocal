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
        top_losing_ads=[
            ad_alerts.AdListItem(
                country="DE",
                ad_name="ABC123_DE_01",
                normalized_ad_code="abc123_de_01",
                total_spend=100.0,
                total_purchase=40.0,
                ad_roas=0.4,
                active_days=9,
            )
        ],
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
    assert payload["items"][0]["top_losing_ads"] == [
        {
            "country": "DE",
            "ad_name": "ABC123_DE_01",
            "normalized_ad_code": "abc123_de_01",
            "total_spend": 100.0,
            "total_purchase": 40.0,
            "ad_roas": 0.4,
            "active_days": 9,
        }
    ]


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


def test_api_ad_list_serializes_ad_items(monkeypatch):
    from web.routes import ad_alerts as route
    from flask import Flask

    item = ad_alerts.AdListItem(
        country="DE",
        ad_name="ABC123_DE_01",
        normalized_ad_code="abc123_de_01",
        total_spend=100.0,
        total_purchase=40.0,
        ad_roas=0.4,
        active_days=9,
    )
    captured: dict[str, object] = {}

    def fake_get_ad_list(product_id, lang):
        captured["product_id"] = product_id
        captured["lang"] = lang
        return [item]

    monkeypatch.setattr(route.ad_alerts, "get_ad_list", fake_get_ad_list)

    flask_app = Flask(__name__)
    with flask_app.test_request_context("/ad-alerts/api/ad-list?product_id=10&lang=DE"):
        response = _unwrap(route.api_ad_list)()

    payload = response.get_json()
    assert captured == {"product_id": 10, "lang": "de"}
    assert payload["total"] == 1
    assert payload["ads"][0] == {
        "country": "DE",
        "ad_name": "ABC123_DE_01",
        "normalized_ad_code": "abc123_de_01",
        "total_spend": 100.0,
        "total_purchase": 40.0,
        "ad_roas": 0.4,
        "active_days": 9,
    }

    with flask_app.test_request_context("/ad-alerts/api/ad-list?product_id=bad&lang=de"):
        response, status = _unwrap(route.api_ad_list)()
    assert status == 400
    assert response.get_json()["error"] == "invalid product_id"


def test_api_evaluate_serializes_evaluations(monkeypatch):
    from web.routes import ad_alerts as route
    from flask import Flask

    captured: dict[str, object] = {}

    def fake_evaluate_ads(product_id, lang, threshold=None, user_id=None):
        captured["product_id"] = product_id
        captured["lang"] = lang
        captured["threshold"] = threshold
        captured["user_id"] = user_id
        return [
            ad_alerts.AdEvaluation(
                country="DE",
                ad_name="bad-ad",
                roas=0.4,
                judgment="关停",
                reason="ROAS 低于保本线",
            )
        ]

    monkeypatch.setattr(route.ad_alerts, "evaluate_ads", fake_evaluate_ads)

    flask_app = Flask(__name__)
    with flask_app.test_request_context(
        "/ad-alerts/api/evaluate",
        method="POST",
        json={"product_id": 10, "lang": "DE", "threshold": 1.4},
    ):
        response = _unwrap(route.api_evaluate)()

    payload = response.get_json()
    assert captured["product_id"] == 10
    assert captured["lang"] == "de"
    assert captured["threshold"] == 1.4
    assert payload["total"] == 1
    assert payload["evaluations"][0]["judgment"] == "关停"


def test_api_evaluate_returns_500_when_llm_fails(monkeypatch):
    from web.routes import ad_alerts as route
    from flask import Flask

    monkeypatch.setattr(route.ad_alerts, "evaluate_ads", lambda *args, **kwargs: None)

    flask_app = Flask(__name__)
    with flask_app.test_request_context(
        "/ad-alerts/api/evaluate",
        method="POST",
        json={"product_id": 10, "lang": "de"},
    ):
        response, status = _unwrap(route.api_evaluate)()

    assert status == 500
    assert response.get_json()["error"] == "evaluation failed"


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


def test_api_problem_ads_serializes_table_rows(monkeypatch):
    from web.routes import ad_alerts as route
    from flask import Flask
    from datetime import date

    item = ad_alerts.ProblemAdItem(
        level="campaign",
        code="glow-campaign",
        name="Glow Campaign",
        ad_account_id="1234",
        ad_account_name="newjoyloo",
        first_active_date="2026-05-01",
        last_active_date="2026-06-12",
        detail_url="/order-analytics?tab=ads&ads_level=campaign&ads_code=glow-campaign",
        metrics={
            "today": ad_alerts.ProblemMetric(spend_usd=12.0, result_count=0, roas=0.0),
            "yesterday": ad_alerts.ProblemMetric(spend_usd=8.0, result_count=1, roas=2.0),
            "last_7d": ad_alerts.ProblemMetric(spend_usd=70.0, result_count=2, roas=0.5),
            "last_30d": ad_alerts.ProblemMetric(spend_usd=300.0, result_count=8, roas=1.5),
            "overall": ad_alerts.ProblemMetric(spend_usd=500.0, result_count=20, roas=2.0),
        },
        product_theme="Home",
        product_main_image="https://img.example.com/item.jpg",
    )
    captured: dict[str, object] = {}

    def fake_get_problem_ads(level, **kwargs):
        if level == "bogus":
            raise ValueError("level must be one of campaign/adset/ad")
        captured["level"] = level
        captured.update(kwargs)
        return date(2026, 6, 12), [item]

    monkeypatch.setattr(route.ad_alerts, "get_problem_ads", fake_get_problem_ads)

    flask_app = Flask(__name__)
    with flask_app.test_request_context("/ad-alerts/api/problem-ads?level=campaign&q=Glow&limit=50"):
        response = _unwrap(route.api_problem_ads)()

    payload = response.get_json()
    assert captured == {"level": "campaign", "search": "Glow", "limit": 50}
    assert payload["business_date"] == "2026-06-12"
    assert payload["total"] == 1
    assert payload["items"][0]["product_theme"] == "Home"
    assert payload["items"][0]["product_main_image"] == "https://img.example.com/item.jpg"
    assert payload["items"][0]["metrics"]["today"] == {
        "spend_usd": 12.0,
        "result_count": 0,
        "roas": 0.0,
    }
    assert payload["items"][0]["detail_url"].startswith("/order-analytics?")

    with flask_app.test_request_context("/ad-alerts/api/problem-ads?level=bogus"):
        response, status = _unwrap(route.api_problem_ads)()
    assert status == 400
    assert response.get_json()["error"] == "invalid_param"


def test_api_high_loss_ads_serializes_card_rows(monkeypatch):
    from web.routes import ad_alerts as route
    from flask import Flask
    from datetime import date

    item = ad_alerts.HighLossAdItem(
        code="glow-ad-1",
        name="Glow Ad 1",
        ad_account_id="1234",
        ad_account_name="newjoyloo",
        country="DE",
        product_id=10,
        product_code="glow-rjc",
        product_name="Glow Product",
        product_main_image="/medias/obj/covers/glow.jpg",
        first_active_date="2026-05-01",
        last_active_date="2026-06-12",
        active_days=12,
        consecutive_loss_days=4,
        detail_url="/order-analytics?tab=ads&ads_level=ad&ads_code=glow-ad-1",
        metrics={
            "today": ad_alerts.HighLossMetric(12.0, 0.0, 0, 0.0, -12.0),
            "last_7d": ad_alerts.HighLossMetric(120.0, 40.0, 1, 0.3333, -80.0),
            "last_30d": ad_alerts.HighLossMetric(300.0, 100.0, 3, 0.3333, -200.0),
            "overall": ad_alerts.HighLossMetric(500.0, 180.0, 6, 0.36, -320.0),
        },
    )
    captured: dict[str, object] = {}

    def fake_get_high_loss_ads(**kwargs):
        captured.update(kwargs)
        return date(2026, 6, 12), [item]

    monkeypatch.setattr(route.ad_alerts, "get_high_loss_ads", fake_get_high_loss_ads)

    flask_app = Flask(__name__)
    with flask_app.test_request_context("/ad-alerts/api/high-loss-ads?q=Glow&limit=30"):
        response = _unwrap(route.api_high_loss_ads)()

    payload = response.get_json()
    assert captured == {"search": "Glow", "limit": 30, "include_handled": False}
    assert payload["business_date"] == "2026-06-12"
    assert payload["total"] == 1
    assert payload["items"][0]["product_code"] == "glow-rjc"
    assert payload["items"][0]["consecutive_loss_days"] == 4
    assert payload["items"][0]["metrics"]["last_7d"] == {
        "spend_usd": 120.0,
        "purchase_value_usd": 40.0,
        "result_count": 1,
        "roas": 0.3333,
        "estimated_loss": -80.0,
    }

    with flask_app.test_request_context("/ad-alerts/api/high-loss-ads?limit=bad"):
        response, status = _unwrap(route.api_high_loss_ads)()
    assert status == 400
    assert response.get_json()["error"] == "invalid limit"


def test_api_share_high_loss_ads_returns_public_signed_url(monkeypatch):
    from web.routes import ad_alerts as route
    from flask import Flask

    monkeypatch.setattr(route.config, "AD_ALERT_PUBLIC_SHARE_BASE_URL", "http://public.example.test")

    flask_app = Flask(__name__)
    flask_app.config["SECRET_KEY"] = "test-secret"
    flask_app.register_blueprint(route.bp)
    with flask_app.test_request_context(
        "/ad-alerts/api/high-loss-ads/share",
        method="POST",
        json={"q": "Glow", "limit": 99, "expires_in_hours": 24},
    ):
        response = _unwrap(route.api_share_high_loss_ads)()

    payload = response.get_json()
    assert payload["share_url"].startswith("http://public.example.test/ad-alerts/share/high-loss?token=")
    assert "&expires=" in payload["share_url"]
    assert payload["q"] == "Glow"
    assert payload["limit"] == 30
    assert payload["expires_in_hours"] == 24
    verified = route.ad_alerts.verify_high_loss_share_token(
        payload["token"],
        payload["expires_at"],
        "test-secret",
    )
    assert verified["q"] == "Glow"
    assert verified["limit"] == 30


def test_public_high_loss_share_validates_token_and_renders(monkeypatch):
    from datetime import date, datetime, timezone
    from flask import Flask
    from pathlib import Path
    from urllib.parse import quote
    from web.routes import ad_alerts as route

    item = ad_alerts.HighLossAdItem(
        code="glow-ad-1",
        name="Glow Ad 1",
        ad_account_id="1234",
        ad_account_name="newjoyloo",
        country="DE",
        product_id=10,
        product_code="glow-rjc",
        product_name="Glow Product",
        product_main_image="/medias/obj/covers/glow.jpg",
        first_active_date="2026-05-01",
        last_active_date="2026-06-12",
        active_days=12,
        consecutive_loss_days=4,
        detail_url="/order-analytics?tab=ads&ads_level=ad&ads_code=glow-ad-1",
        metrics={
            "today": ad_alerts.HighLossMetric(12.0, 0.0, 0, 0.0, -12.0),
            "last_7d": ad_alerts.HighLossMetric(120.0, 40.0, 1, 0.3333, -80.0),
            "last_30d": ad_alerts.HighLossMetric(300.0, 100.0, 3, 0.3333, -200.0),
            "overall": ad_alerts.HighLossMetric(500.0, 180.0, 6, 0.36, -320.0),
        },
    )
    captured: dict[str, object] = {}

    def fake_get_high_loss_ads(**kwargs):
        captured.update(kwargs)
        return date(2026, 6, 12), [item]

    monkeypatch.setattr(route.ad_alerts, "get_high_loss_ads", fake_get_high_loss_ads)
    payload = ad_alerts.build_high_loss_share_payload(
        search="Glow",
        limit=30,
        expires_in_hours=24,
        now=datetime(2026, 6, 12, 4, 0, 0, tzinfo=timezone.utc),
    )
    token = ad_alerts.sign_share_token(payload, "test-secret")

    flask_app = Flask(__name__, template_folder=str(Path("web/templates").resolve()))
    flask_app.config["SECRET_KEY"] = "test-secret"
    flask_app.register_blueprint(route.bp)
    client = flask_app.test_client()
    response = client.get(
        "/ad-alerts/share/high-loss?token="
        + quote(token)
        + "&expires="
        + quote(payload["expires_at"])
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "高额亏损广告分享" in html
    assert "Glow Product" in html
    assert "2026-06-13T04:00:00Z" in html
    assert captured == {"search": "Glow", "limit": 30, "include_handled": False}

    invalid = client.get("/ad-alerts/share/high-loss?token=bad&expires=2026-06-13T04:00:00Z")
    assert invalid.status_code == 403


def test_ad_alert_page_and_problem_api_route_smoke(authed_client_no_db, monkeypatch):
    from datetime import date
    from web.routes import ad_alerts as route

    raw_client = authed_client_no_db.application.test_client()
    
    # 未登录校验
    unauth_response = raw_client.get("/ad-alerts/")
    assert unauth_response.status_code == 302
    unauth_response_alerts = raw_client.get("/ad-alerts/alerts")
    assert unauth_response_alerts.status_code == 302
    unauth_response_prob = raw_client.get("/ad-alerts/problem")
    assert unauth_response_prob.status_code == 302

    monkeypatch.setattr(route.ad_alerts, "get_threshold", lambda: 1.4)
    
    # 默认高额亏损广告页面
    page_response = authed_client_no_db.get("/ad-alerts/")
    assert page_response.status_code == 200
    assert "高额亏损广告" in page_response.get_data(as_text=True)

    # 广告预警页面
    page_response_alerts = authed_client_no_db.get("/ad-alerts/alerts")
    assert page_response_alerts.status_code == 200
    assert "广告预警" in page_response_alerts.get_data(as_text=True)

    # 兼容：默认页仍包含广告预警 Tab
    assert "广告预警" in page_response.get_data(as_text=True)

    # 问题广告页面
    page_response_prob = authed_client_no_db.get("/ad-alerts/problem")
    assert page_response_prob.status_code == 200
    assert "问题广告" in page_response_prob.get_data(as_text=True)

    monkeypatch.setattr(route.ad_alerts, "get_problem_ads", lambda *args, **kwargs: (date(2026, 6, 12), []))
    api_response = authed_client_no_db.get("/ad-alerts/api/problem-ads?level=campaign")
    assert api_response.status_code == 200
    assert api_response.get_json()["business_date"] == "2026-06-12"

    monkeypatch.setattr(route.ad_alerts, "get_high_loss_ads", lambda *args, **kwargs: (date(2026, 6, 12), []))
    high_loss_response = authed_client_no_db.get("/ad-alerts/api/high-loss-ads")
    assert high_loss_response.status_code == 200
    assert high_loss_response.get_json()["business_date"] == "2026-06-12"


def test_ad_alert_detail_pages_render_with_mocked_data(authed_client_no_db, monkeypatch):
    from web.routes import ad_alerts as route

    judgment = ad_alerts.Judgment(
        severity=ad_alerts.Severity.SEVERE,
        trend=ad_alerts.TrendDirection.WORSENING,
        phase=ad_alerts.Phase.STABLE,
        conclusion="建议关停",
        reason="ROAS 低于保本线",
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
    ad_detail = {
        "product_id": 10,
        "ad_code": "bad-code",
        "ad_name": "Bad Ad",
        "ad_account_id": "act_1",
        "ad_account_name": "Demo Account",
        "first_active_date": "2026-06-01",
        "last_active_date": "2026-06-10",
        "metrics": {
            "today": {"spend_usd": 12.0, "purchase_value_usd": 0.0, "roas": 0.0},
            "yesterday": {"spend_usd": 8.0, "purchase_value_usd": 16.0, "roas": 2.0},
            "last_7d": {"spend_usd": 70.0, "purchase_value_usd": 35.0, "roas": 0.5},
            "last_30d": {"spend_usd": 300.0, "purchase_value_usd": 120.0, "roas": 0.4},
            "overall": {"spend_usd": 500.0, "purchase_value_usd": 200.0, "roas": 0.4},
        },
        "trend": [ad_alerts.DailyPoint(date="2026-06-10", spend_usd=12.0, purchase_value_usd=0.0, roas=0.0)],
    }

    monkeypatch.setattr(route.ad_alerts, "get_threshold", lambda: 1.4)
    monkeypatch.setattr(
        route.ad_alerts,
        "get_product_alert_details",
        lambda product_id, threshold=None: {
            "product_id": product_id,
            "product_code": "ABC123",
            "product_name": "Demo Product",
            "countries": [detail],
            "ads": [],
        },
    )
    monkeypatch.setattr(route.ad_alerts, "get_alert_detail", lambda product_id, lang, threshold=None: detail)
    monkeypatch.setattr(route.ad_alerts, "get_ad_detail_and_trend", lambda product_id, ad_code, ad_account_id: ad_detail)

    product_response = authed_client_no_db.get("/ad-alerts/product/10")
    assert product_response.status_code == 200
    assert "Demo Product" in product_response.get_data(as_text=True)

    country_response = authed_client_no_db.get("/ad-alerts/product/10/country/de")
    assert country_response.status_code == 200
    assert "countryAdList" in country_response.get_data(as_text=True)

    ad_response = authed_client_no_db.get("/ad-alerts/product/10/ad/bad-code?ad_account_id=act_1&lang=de&country=DE")
    ad_html = ad_response.get_data(as_text=True)
    assert ad_response.status_code == 200
    assert "adDetailMetricGrid" in ad_html
    assert "/ad-alerts/product/10/country/de" in ad_html


def test_api_set_alert_action_validates_and_persists(monkeypatch):
    from web.routes import ad_alerts as route
    from appcore import ad_alert_actions
    from flask import Flask

    captured: dict[str, object] = {}

    def fake_set_action(scope, target_key, action, *, note=None, operator_user_id=None):
        if scope not in ("high_loss", "language"):
            raise ValueError("bad scope")
        captured.update(
            scope=scope, target_key=target_key, action=action,
            note=note, operator_user_id=operator_user_id,
        )
        return {"scope": scope, "target_key": target_key, "action": action, "note": note}

    monkeypatch.setattr(ad_alert_actions, "set_action", fake_set_action)

    flask_app = Flask(__name__)
    with flask_app.test_request_context(
        "/ad-alerts/api/actions",
        method="POST",
        json={
            "scope": "high_loss",
            "target_key": "123:120210",
            "action": "resolved",
            "note": "已关停",
        },
    ):
        response = _unwrap(route.api_set_alert_action)()

    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["action"]["action"] == "resolved"
    assert captured["scope"] == "high_loss"
    assert captured["target_key"] == "123:120210"
    assert captured["note"] == "已关停"

    with flask_app.test_request_context(
        "/ad-alerts/api/actions",
        method="POST",
        json={"scope": "bad", "target_key": "x", "action": "resolved"},
    ):
        response, status = _unwrap(route.api_set_alert_action)()
    assert status == 400


def test_api_clear_alert_action(monkeypatch):
    from web.routes import ad_alerts as route
    from appcore import ad_alert_actions
    from flask import Flask

    cleared: list[tuple] = []
    monkeypatch.setattr(
        ad_alert_actions, "clear_action",
        lambda scope, target_key: cleared.append((scope, target_key)) or True,
    )

    flask_app = Flask(__name__)
    with flask_app.test_request_context(
        "/ad-alerts/api/actions",
        method="POST",
        json={"scope": "language", "target_key": "45:de", "action": "clear"},
    ):
        response = _unwrap(route.api_set_alert_action)()

    assert response.get_json()["ok"] is True
    assert cleared == [("language", "45:de")]


def test_list_apis_pass_include_handled(monkeypatch):
    from web.routes import ad_alerts as route
    from flask import Flask
    from datetime import date

    captured: dict[str, object] = {}

    def fake_get_alerts(**kwargs):
        captured["alerts_kwargs"] = kwargs
        return []

    def fake_get_high_loss_ads(**kwargs):
        captured["high_loss_kwargs"] = kwargs
        return date(2026, 6, 12), []

    monkeypatch.setattr(route.ad_alerts, "get_alerts", fake_get_alerts)
    monkeypatch.setattr(route.ad_alerts, "get_high_loss_ads", fake_get_high_loss_ads)
    monkeypatch.setattr(route.ad_alerts, "get_threshold", lambda: 1.5)

    flask_app = Flask(__name__)
    with flask_app.test_request_context("/ad-alerts/api/list?include_handled=1"):
        _unwrap(route.api_list)()
    assert captured["alerts_kwargs"]["include_handled"] is True

    with flask_app.test_request_context("/ad-alerts/api/high-loss-ads?include_handled=1"):
        _unwrap(route.api_high_loss_ads)()
    assert captured["high_loss_kwargs"]["include_handled"] is True
