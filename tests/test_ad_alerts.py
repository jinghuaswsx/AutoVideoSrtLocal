from __future__ import annotations

from datetime import date, datetime


def test_threshold_defaults_and_persists_json(monkeypatch):
    from appcore import ad_alerts

    stored: dict[str, str | None] = {"value": None}

    monkeypatch.setattr(
        ad_alerts.system_settings,
        "get_setting",
        lambda key: stored["value"],
    )
    monkeypatch.setattr(
        ad_alerts.system_settings,
        "set_setting",
        lambda key, value: stored.__setitem__("value", value),
    )

    assert ad_alerts.get_threshold() == ad_alerts.DEFAULT_THRESHOLD

    ad_alerts.set_threshold(1.35)

    assert stored["value"] == '{"threshold": 1.35}'
    assert ad_alerts.get_threshold() == 1.35

    stored["value"] = "not-json"
    assert ad_alerts.get_threshold() == ad_alerts.DEFAULT_THRESHOLD


def test_judge_alert_outputs_expected_conclusions():
    from appcore import ad_alerts

    severe_stable = ad_alerts.judge_alert(
        0.8,
        recent_7d_roas=0.75,
        trend_series=[],
        prior_7d=0.9,
        active_days=12,
    )
    assert severe_stable.severity == ad_alerts.Severity.SEVERE
    assert severe_stable.phase == ad_alerts.Phase.STABLE
    assert severe_stable.conclusion == "建议关停"

    learning = ad_alerts.judge_alert(
        0.7,
        recent_7d_roas=0.7,
        trend_series=[],
        prior_7d=1.0,
        active_days=3,
    )
    assert learning.phase == ad_alerts.Phase.LEARNING
    assert learning.conclusion == "建议观察"

    worsening = ad_alerts.judge_alert(
        1.2,
        recent_7d_roas=0.8,
        trend_series=[],
        prior_7d=1.0,
        active_days=9,
    )
    assert worsening.severity == ad_alerts.Severity.MODERATE
    assert worsening.trend == ad_alerts.TrendDirection.WORSENING
    assert worsening.conclusion == "建议优化"


def test_get_alerts_queries_cache_and_filters_by_severity(monkeypatch):
    from appcore import ad_alerts

    captured: dict[str, object] = {}
    ad_list_calls: list[tuple[int, str]] = []

    def fake_query(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "product_id": 10,
                "lang": "de",
                "ad_spend_usd": "100.00",
                "purchase_value_usd": "40.00",
                "ad_roas": "0.4000",
                "active_7d_ad_spend_usd": "12.00",
                "computed_at": datetime(2026, 6, 11, 8, 0, 0),
                "product_code": "ABC123",
                "product_name": "Demo Product",
                "store_code": "DE01",
            },
            {
                "product_id": 11,
                "lang": "fr",
                "ad_spend_usd": "100.00",
                "purchase_value_usd": "125.00",
                "ad_roas": "1.2500",
                "active_7d_ad_spend_usd": "8.00",
                "computed_at": datetime(2026, 6, 11, 8, 0, 0),
                "product_code": "DEF456",
                "product_name": "Other Product",
                "store_code": "FR01",
            },
        ]

    monkeypatch.setattr(ad_alerts, "query", fake_query)
    monkeypatch.setattr(
        ad_alerts,
        "_get_active_window",
        lambda product_id, lang: ad_alerts.ActiveWindow(None, None, 8),
    )
    monkeypatch.setattr(
        ad_alerts,
        "_alert_trend_inputs",
        lambda product_id, lang: (None, None),
    )
    monkeypatch.setattr(
        ad_alerts,
        "get_ad_list",
        lambda product_id, lang: ad_list_calls.append((product_id, lang)) or [
            ad_alerts.AdListItem("DE", "worst-ad", "worst-code", 100.0, 40.0, 0.4, 10),
            ad_alerts.AdListItem("AT", "safe-ad", "safe-code", 100.0, 180.0, 1.8, 10),
        ],
    )

    items = ad_alerts.get_alerts(
        threshold=1.5,
        lang="de",
        severity=ad_alerts.Severity.SEVERE,
        search="ABC",
    )

    assert "FROM media_product_lang_ad_summary_cache c" in captured["sql"]
    assert "c.ad_roas < %(threshold)s" in captured["sql"]
    assert "c.active_7d_ad_spend_usd > 0" in captured["sql"]
    assert captured["params"] == {"threshold": 1.5, "lang": "de", "search": "%ABC%"}
    assert len(items) == 1
    assert items[0].product_id == 10
    assert items[0].severity == ad_alerts.Severity.SEVERE
    assert items[0].phase == ad_alerts.Phase.STABLE
    assert items[0].estimated_loss == -60.0
    assert ad_list_calls == [(10, "de")]
    assert [ad.ad_name for ad in items[0].top_losing_ads] == ["worst-ad"]


def test_get_top_losing_ads_filters_sorts_and_limits(monkeypatch):
    from appcore import ad_alerts

    monkeypatch.setattr(
        ad_alerts,
        "get_ad_list",
        lambda product_id, lang: [
            ad_alerts.AdListItem("DE", "warn-ad", "warn-code", 100.0, 120.0, 1.2, 8),
            ad_alerts.AdListItem("AT", "worst-ad", "worst-code", 100.0, 20.0, 0.2, 8),
            ad_alerts.AdListItem("CH", "no-roas", "no-roas-code", 0.0, 0.0, None, 0),
            ad_alerts.AdListItem("FR", "safe-ad", "safe-code", 100.0, 180.0, 1.8, 8),
            ad_alerts.AdListItem("ES", "second-ad", "second-code", 100.0, 70.0, 0.7, 8),
        ],
    )

    losing_ads = ad_alerts._get_top_losing_ads(10, "DE", threshold=1.5, limit=2)

    assert [ad.ad_name for ad in losing_ads] == ["worst-ad", "second-ad"]


def test_detail_uses_language_matched_trend_series(monkeypatch):
    from appcore import ad_alerts

    queries: list[tuple[str, object]] = []

    def fake_query_one(sql, params=None):
        queries.append((sql, params))
        if "FROM media_product_lang_ad_summary_cache c" in sql:
            return {
                "product_id": 10,
                "lang": "de",
                "ad_spend_usd": "100.00",
                "purchase_value_usd": "40.00",
                "ad_roas": "0.4000",
                "active_7d_ad_spend_usd": "12.00",
                "computed_at": datetime(2026, 6, 11, 8, 0, 0),
                "product_code": "ABC123",
                "product_name": "Demo Product",
                "store_code": "DE01",
            }
        return {
            "delivery_start": date(2026, 6, 1),
            "delivery_end": date(2026, 6, 10),
            "active_days": 10,
        }

    def fake_query(sql, params=None):
        queries.append((sql, params))
        assert "JOIN media_items i" in sql
        assert "LOWER(i.lang) = %(lang)s" in sql
        assert "CASE UPPER(m.market_country)" in sql
        return [
            {"ad_date": date(2026, 6, 3), "spend_usd": "10.00", "purchase_value_usd": "15.00"},
            {"ad_date": date(2026, 6, 4), "spend_usd": "20.00", "purchase_value_usd": "10.00"},
        ]

    monkeypatch.setattr(ad_alerts, "query_one", fake_query_one)
    monkeypatch.setattr(ad_alerts, "query", fake_query)

    detail = ad_alerts.get_alert_detail(10, "de", threshold=1.5)

    assert detail is not None
    assert detail.lang_label == "德语"
    assert detail.active_days == 10
    assert [point.date for point in detail.trend] == ["2026-06-03", "2026-06-04"]
    assert detail.trend[0].roas == 1.5
    assert detail.estimated_loss == -60.0


def test_get_ad_list_aggregates_language_matched_ads(monkeypatch):
    from appcore import ad_alerts

    captured: dict[str, object] = {}

    def fake_query(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "country": "de",
                "ad_name": "ABC123_DE_01",
                "normalized_ad_code": "abc123_de_01",
                "total_spend": "100.00",
                "total_purchase": "40.00",
                "active_days": 9,
            }
        ]

    monkeypatch.setattr(ad_alerts, "query", fake_query)

    items = ad_alerts.get_ad_list(10, "DE")

    assert "FROM meta_ad_daily_ad_metrics m" in captured["sql"]
    assert "EXISTS (" in captured["sql"]
    assert "LOWER(i.lang) = %(lang)s" in captured["sql"]
    assert "CASE UPPER(m.market_country)" in captured["sql"]
    assert captured["params"] == {"product_id": 10, "lang": "de"}
    assert len(items) == 1
    assert items[0].country == "DE"
    assert items[0].ad_name == "ABC123_DE_01"
    assert items[0].total_spend == 100.0
    assert items[0].total_purchase == 40.0
    assert items[0].ad_roas == 0.4
    assert items[0].active_days == 9


def test_evaluate_ads_calls_gemini_for_losing_ads(monkeypatch):
    from appcore import ad_alerts, llm_client

    monkeypatch.setattr(
        ad_alerts,
        "get_ad_list",
        lambda product_id, lang: [
            ad_alerts.AdListItem("DE", "bad-ad", "bad-code", 100.0, 40.0, 0.4, 10),
            ad_alerts.AdListItem("AT", "good-ad", "good-code", 100.0, 190.0, 1.9, 10),
        ],
    )
    monkeypatch.setattr(
        ad_alerts,
        "query_one",
        lambda sql, params=None: {"product_code": "ABC123", "name": "Demo Product"},
    )
    captured: dict[str, object] = {}

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured.update(kwargs)
        return {
            "text": '```json\n[{"country":"DE","ad_name":"bad-ad","roas":0.4,'
                    '"judgment":"关停","reason":"ROAS 低于保本线且持续消耗"}]\n```'
        }

    monkeypatch.setattr(llm_client, "invoke_chat", fake_invoke_chat)

    evaluations = ad_alerts.evaluate_ads(10, "de", threshold=1.5, user_id=7)

    assert captured["use_case_code"] == "ad_alert.evaluate"
    assert captured["user_id"] == 7
    assert captured["project_id"] == "ad-alert:10:de"
    prompt_text = captured["messages"][1]["content"]
    assert "bad-ad" in prompt_text
    assert "good-ad" not in prompt_text
    assert captured["billing_extra"] == {"product_id": 10, "lang": "de", "ad_count": 1}
    assert evaluations == [
        ad_alerts.AdEvaluation(
            country="DE",
            ad_name="bad-ad",
            roas=0.4,
            judgment="关停",
            reason="ROAS 低于保本线且持续消耗",
        )
    ]
