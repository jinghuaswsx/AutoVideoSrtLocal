from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest


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


def test_high_loss_share_token_binds_expiry_and_options():
    from appcore import ad_alerts

    now = datetime(2026, 6, 12, 4, 0, 0, tzinfo=timezone.utc)
    payload = ad_alerts.build_high_loss_share_payload(
        search="Glow",
        limit=99,
        expires_in_hours=24,
        now=now,
    )
    token = ad_alerts.sign_share_token(payload, "test-secret")

    verified = ad_alerts.verify_high_loss_share_token(
        token,
        payload["expires_at"],
        "test-secret",
        now=now + timedelta(hours=1),
    )

    assert payload["scope"] == ad_alerts.HIGH_LOSS_SHARE_SCOPE
    assert payload["limit"] == 30
    assert payload["expires_at"] == "2026-06-13T04:00:00Z"
    assert verified["q"] == "Glow"
    assert verified["limit"] == 30
    assert verified["expires_at"] == payload["expires_at"]

    with pytest.raises(ValueError):
        ad_alerts.verify_high_loss_share_token(
            token,
            "2026-06-13T05:00:00Z",
            "test-secret",
            now=now,
        )

    with pytest.raises(ValueError):
        ad_alerts.verify_high_loss_share_token(
            token,
            payload["expires_at"],
            "test-secret",
            now=now + timedelta(hours=25),
        )


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
    assert "p.store_code" not in captured["sql"]
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
        if "FROM media_items" in sql:
            return [{"filename": "DemoMaterial", "display_name": "DemoMaterial"}]
        # 对应主查询的 Assert
        assert "FROM meta_ad_daily_ad_metrics m" in sql or "FROM meta_ad_daily_ad_metrics" in sql
        assert "SELECT" in sql
        assert "spend_usd" in sql
        return [
            {"ad_date": date(2026, 6, 3), "spend_usd": "10.00", "purchase_value_usd": "15.00", "country": "de"},
            {"ad_date": date(2026, 6, 4), "spend_usd": "20.00", "purchase_value_usd": "10.00", "country": "de"},
        ]

    monkeypatch.setattr(ad_alerts, "query_one", fake_query_one)
    monkeypatch.setattr(ad_alerts, "query", fake_query)

    detail = ad_alerts.get_alert_detail(10, "de", threshold=1.5)
    cache_sql = next(sql for sql, _params in queries if "FROM media_product_lang_ad_summary_cache c" in sql)

    assert detail is not None
    assert "p.store_code" not in cache_sql
    assert "c.ad_roas < %(threshold)s" in cache_sql
    assert "c.active_7d_ad_spend_usd > 0" in cache_sql
    assert "c.ad_spend_usd > 0" in cache_sql
    assert next(params for sql, params in queries if sql == cache_sql) == {
        "product_id": 10,
        "lang": "de",
        "threshold": 1.5,
    }
    assert detail.lang_label == "德语"
    assert detail.active_days == 2
    assert [point.date for point in detail.trend] == ["2026-06-04", "2026-06-03"]
    assert detail.trend[1].roas == 1.5
    assert detail.estimated_loss == -60.0


def test_get_ad_list_aggregates_language_matched_ads(monkeypatch):
    from appcore import ad_alerts

    captured: dict[str, object] = {}

    def fake_query(sql, params=None):
        if "FROM media_items" in sql:
            return [{"filename": "ABC123_DE_01", "display_name": ""}]
        if "SELECT DISTINCT COALESCE" in sql:
            return [{"code": "abc123_de_01"}]
        if "meta_ad_realtime_daily_ad_metrics" in sql:
            return [{"code": "abc123_de_01"}]
        
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
    assert captured["params"]["product_id"] == 10
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


def test_get_problem_ads_uses_realtime_today_and_aggregates_windows(monkeypatch):
    from appcore import ad_alerts

    captured: dict[str, object] = {}

    def fake_query(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "code": "glow-ad-1",
                "name": "Glow Ad 1",
                "ad_account_id": "1234",
                "ad_account_name": "newjoyloo",
                "first_active_date": date(2026, 5, 1),
                "last_active_date": date(2026, 6, 12),
                "today_spend_usd": "12.00",
                "today_purchase_value_usd": "0.00",
                "today_result_count": "0",
                "yesterday_spend_usd": "8.00",
                "yesterday_purchase_value_usd": "16.00",
                "yesterday_result_count": "1",
                "last_7d_spend_usd": "70.00",
                "last_7d_purchase_value_usd": "35.00",
                "last_7d_result_count": "2",
                "last_30d_spend_usd": "300.00",
                "last_30d_purchase_value_usd": "450.00",
                "last_30d_result_count": "8",
                "overall_spend_usd": "500.00",
                "overall_purchase_value_usd": "1000.00",
                "overall_result_count": "20",
            },
        ]

    monkeypatch.setattr(ad_alerts, "query", fake_query)
    monkeypatch.setattr(ad_alerts, "current_meta_business_date", lambda: date(2026, 6, 12))

    business_date, items = ad_alerts.get_problem_ads("ad", search="Glow", limit=20)

    assert business_date.isoformat() == "2026-06-12"
    assert len(items) == 1
    assert items[0].metrics["today"].spend_usd == 12.0
    assert items[0].metrics["today"].result_count == 0
    assert items[0].metrics["today"].roas == 0.0
    assert items[0].metrics["last_7d"].roas == 0.5
    assert items[0].metrics["overall"].roas == 2.0
    assert "start_date=2026-05-01" in items[0].detail_url
    assert "end_date=2026-06-12" in items[0].detail_url
    assert "ads_level=ad" in items[0].detail_url

    sql = str(captured["sql"])
    assert "meta_ad_realtime_daily_ad_metrics" in sql
    assert "meta_ad_daily_ad_metrics" in sql
    assert "GROUP BY business_date, ad_account_id" in sql
    assert "problem_today.ad_account_id <=> s.ad_account_id" in sql
    assert "HAVING SUM(COALESCE(t.spend_usd, 0)) > 0" in sql
    assert "AND SUM(COALESCE(t.result_count, 0)) = 0" in sql
    assert "LOWER(s.name) LIKE LOWER(%(search)s)" in sql
    assert captured["params"] == {
        "today": date(2026, 6, 12),
        "yesterday": date(2026, 6, 11),
        "last_7d_start": date(2026, 6, 6),
        "last_30d_start": date(2026, 5, 14),
        "limit": 20,
        "search": "%Glow%",
    }


def test_get_high_loss_ads_prioritizes_recent_spend_and_counts_consecutive_loss(monkeypatch):
    from appcore import ad_alerts

    captured: dict[str, object] = {}

    def fake_query(sql, params=None):
        sql_text = str(sql)
        if "GROUP BY s.code, s.ad_account_id, s.metric_date" in sql_text:
            return [
                {
                    "code": "glow-ad-1",
                    "ad_account_id": "1234",
                    "metric_date": date(2026, 6, 12),
                    "spend_usd": "12.00",
                    "purchase_value_usd": "0.00",
                    "result_count": "0",
                },
                {
                    "code": "glow-ad-1",
                    "ad_account_id": "1234",
                    "metric_date": date(2026, 6, 11),
                    "spend_usd": "20.00",
                    "purchase_value_usd": "10.00",
                    "result_count": "1",
                },
                {
                    "code": "glow-ad-1",
                    "ad_account_id": "1234",
                    "metric_date": date(2026, 6, 10),
                    "spend_usd": "20.00",
                    "purchase_value_usd": "40.00",
                    "result_count": "1",
                },
            ]
        if "media_product_covers" in sql_text or "WHERE LOWER(product_code) IN" in sql_text:
            return []

        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "code": "glow-ad-1",
                "name": "Glow Ad 1",
                "ad_account_id": "1234",
                "ad_account_name": "newjoyloo",
                "country": "de",
                "product_id": 10,
                "product_code": "glow-rjc",
                "product_name": "Glow Product",
                "product_main_image": "covers/glow.jpg",
                "first_active_date": date(2026, 5, 1),
                "last_active_date": date(2026, 6, 12),
                "active_days": "12",
                "today_spend_usd": "12.00",
                "today_purchase_value_usd": "0.00",
                "today_result_count": "0",
                "last_7d_spend_usd": "120.00",
                "last_7d_purchase_value_usd": "40.00",
                "last_7d_result_count": "1",
                "last_30d_spend_usd": "300.00",
                "last_30d_purchase_value_usd": "100.00",
                "last_30d_result_count": "3",
                "overall_spend_usd": "500.00",
                "overall_purchase_value_usd": "180.00",
                "overall_result_count": "6",
            }
        ]

    monkeypatch.setattr(ad_alerts, "query", fake_query)
    monkeypatch.setattr(ad_alerts, "current_meta_business_date", lambda: date(2026, 6, 12))

    business_date, items = ad_alerts.get_high_loss_ads(search="Glow", limit=99)

    assert business_date.isoformat() == "2026-06-12"
    assert len(items) == 1
    item = items[0]
    assert item.code == "glow-ad-1"
    assert item.product_code == "glow-rjc"
    assert item.product_main_image == "/medias/obj/covers/glow.jpg"
    assert item.metrics["last_7d"].spend_usd == 120.0
    assert item.metrics["last_7d"].roas == 0.3333
    assert item.metrics["last_7d"].estimated_loss == -80.0
    assert item.consecutive_loss_days == 2
    assert "ads_level=ad" in item.detail_url
    assert "start_date=2026-05-01" in item.detail_url

    sql = str(captured["sql"])
    assert "meta_ad_realtime_daily_ad_metrics" in sql
    assert "meta_ad_daily_ad_metrics" in sql
    assert "GROUP BY business_date, ad_account_id" in sql
    assert "HAVING last_7d_spend_usd > 0" in sql
    assert "last_7d_result_count = 0" in sql
    assert "last_7d_purchase_value_usd / NULLIF(last_7d_spend_usd, 0)" in sql
    assert "ORDER BY last_7d_spend_usd DESC, today_spend_usd DESC, last_30d_spend_usd DESC" in sql
    assert captured["params"]["limit"] == 30
    assert captured["params"]["search"] == "%Glow%"


def test_get_ad_detail_and_trend_returns_result_counts(monkeypatch):
    from appcore import ad_alerts

    captured: dict[str, object] = {}

    def fake_query_one(sql, params=None):
        captured["detail_sql"] = sql
        captured["detail_params"] = params
        return {
            "first_active_date": date(2026, 5, 1),
            "last_active_date": date(2026, 6, 12),
            "ad_name": "Glow Ad 1",
            "ad_account_name": "newjoyloo",
            "today_spend_usd": "12.00",
            "today_purchase_value_usd": "0.00",
            "today_result_count": "0",
            "yesterday_spend_usd": "8.00",
            "yesterday_purchase_value_usd": "16.00",
            "yesterday_result_count": "1",
            "last_7d_spend_usd": "70.00",
            "last_7d_purchase_value_usd": "35.00",
            "last_7d_result_count": "2",
            "last_30d_spend_usd": "300.00",
            "last_30d_purchase_value_usd": "450.00",
            "last_30d_result_count": "8",
            "overall_spend_usd": "500.00",
            "overall_purchase_value_usd": "1000.00",
            "overall_result_count": "20",
        }

    def fake_query(sql, params=None):
        captured["trend_sql"] = sql
        captured["trend_params"] = params
        return []

    monkeypatch.setattr(ad_alerts, "query_one", fake_query_one)
    monkeypatch.setattr(ad_alerts, "query", fake_query)
    monkeypatch.setattr(ad_alerts, "current_meta_business_date", lambda: date(2026, 6, 12))

    detail = ad_alerts.get_ad_detail_and_trend(10, "glow-ad-1", "1234")

    assert detail is not None
    assert "today_result_count" in str(captured["detail_sql"])
    assert "overall_result_count" in str(captured["detail_sql"])
    assert detail["metrics"]["today"]["result_count"] == 0
    assert detail["metrics"]["yesterday"]["result_count"] == 1
    assert detail["metrics"]["last_7d"]["result_count"] == 2
    assert detail["metrics"]["last_30d"]["result_count"] == 8
    assert detail["metrics"]["overall"]["result_count"] == 20
    assert detail["metrics"]["overall"]["purchase_value_usd"] == 1000.0
    assert detail["metrics"]["overall"]["roas"] == 2.0


def test_get_alerts_dynamically_date_range(monkeypatch):
    from appcore import ad_alerts

    captured_sqls: list[str] = []
    captured_params: list[dict] = []

    def fake_query(sql, params=None):
        captured_sqls.append(sql)
        captured_params.append(params or {})
        return [
            {
                "product_id": 10,
                "lang": "de",
                "ad_spend_usd": 150.00,
                "purchase_value_usd": 50.00,
                "ad_roas": 0.3333,
                "active_7d_ad_spend_usd": 12.00,
                "computed_at": datetime(2026, 6, 11, 8, 0, 0),
                "product_code": "ABC123",
                "product_name": "Demo Product",
                "store_code": "DE01",
            }
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
        lambda product_id, lang, end_date=None: (None, None),
    )
    monkeypatch.setattr(ad_alerts, "get_ad_list", lambda product_id, lang: [])

    items = ad_alerts.get_alerts(
        threshold=1.5,
        lang="de",
        start_date="2026-06-01",
        end_date="2026-06-10",
    )

    assert len(captured_sqls) == 1
    assert "media_product_lang_ad_summary_cache" in captured_sqls[0]
    assert "meta_ad_daily_ad_metrics" in captured_sqls[0]
    assert captured_params[0]["start_date"] == "2026-06-01"
    assert captured_params[0]["end_date"] == "2026-06-10"
    assert len(items) == 1
    assert items[0].product_id == 10
    assert items[0].estimated_loss == -100.0


def test_get_alerts_filters_out_profit_unless_worsening_and_huge_spend(monkeypatch):
    from appcore import ad_alerts

    # 我们模拟 query 返回了三项：
    # 1. 亏损项：spend=100, purchase=30
    # 2. 盈利项但不是恶化大消耗：spend=100, purchase=150, trend=STABLE
    # 3. 盈利项且是恶化大消耗：spend=400, purchase=420, trend=WORSENING, active_7d_ad_spend_usd=120
    def fake_query(sql, params=None):
        return [
            {
                "product_id": 10,
                "lang": "de",
                "ad_spend_usd": 100.00,
                "purchase_value_usd": 30.00,
                "ad_roas": 0.3,
                "active_7d_ad_spend_usd": 10.00,
                "computed_at": datetime(2026, 6, 11, 8, 0, 0),
                "product_code": "ABC123",
                "product_name": "Demo Product",
                "store_code": "DE01",
            },
            {
                "product_id": 11,
                "lang": "fr",
                "ad_spend_usd": 100.00,
                "purchase_value_usd": 150.00,
                "ad_roas": 1.5,
                "active_7d_ad_spend_usd": 20.00,
                "computed_at": datetime(2026, 6, 11, 8, 0, 0),
                "product_code": "DEF456",
                "product_name": "Other Product",
                "store_code": "FR01",
            },
            {
                "product_id": 12,
                "lang": "es",
                "ad_spend_usd": 400.00,
                "purchase_value_usd": 420.00,
                "ad_roas": 1.05,
                "active_7d_ad_spend_usd": 120.00,
                "computed_at": datetime(2026, 6, 11, 8, 0, 0),
                "product_code": "XYZ789",
                "product_name": "Third Product",
                "store_code": "ES01",
            },
        ]

    monkeypatch.setattr(ad_alerts, "query", fake_query)
    monkeypatch.setattr(
        ad_alerts,
        "_get_active_window",
        lambda product_id, lang: ad_alerts.ActiveWindow(None, None, 15),
    )
    
    # 模拟不同的趋势。由于 10 没特别模拟，11 模拟 prior=1.0, recent=1.0 (持平)，
    # 12 模拟 prior=1.5, recent=0.8 (即恶化 trend=WORSENING)
    def fake_trend_inputs(product_id, lang, end_date=None):
        if product_id == 12:
            return (0.8, 1.5)  # recent, prior => ratio = 0.8/1.5 = 0.53 < 0.9 => worsening
        return (1.0, 1.0)      # stable

    monkeypatch.setattr(ad_alerts, "_alert_trend_inputs", fake_trend_inputs)

    items = ad_alerts.get_alerts(threshold=2.0)

    # 预期的 items：
    # 10 被保留（估计亏损 -70.0 < 0）
    # 11 被过滤（估计盈亏 +50.0 > 0 且趋势持平）
    # 12 被保留（估计盈亏 +20.0 > 0，但趋势恶化且消耗很大）
    pids = [it.product_id for it in items]
    assert 10 in pids
    assert 11 not in pids
    assert 12 in pids
    assert len(items) == 2


def test_batch_fetch_problem_ad_details_images(monkeypatch):
    from appcore import ad_alerts
    from appcore.ad_alerts import ProblemAdItem

    item1 = ProblemAdItem(
        level="ad",
        code="rjc-test-product-1",
        name="Test Ad 1",
        ad_account_id="123",
        ad_account_name="Account",
        first_active_date=None,
        last_active_date=None,
        detail_url="",
        metrics={},
        product_cn_name=None,
        product_theme=None,
        product_main_image=None,
    )

    item2 = ProblemAdItem(
        level="ad",
        code="rjc-test-product-2",
        name="Test Ad 2",
        ad_account_id="123",
        ad_account_name="Account",
        first_active_date=None,
        last_active_date=None,
        detail_url="",
        metrics={},
        product_cn_name=None,
        product_theme=None,
        product_main_image=None,
    )

    query_queries = []
    def fake_db_query(sql, params=None):
        query_queries.append((sql, params))
        if "media_product_covers" in sql:
            if params and any("rjc-test-product-2" in p for p in params):
                return [{"code": "rjc-test-product-2", "main_image": "79/medias/796/cover.png"}]
            return []
        elif "media_products" in sql:
            if "name" in sql:
                return [{"code": "rjc-test-product-1", "name": "Test Product 1 Name"}]
            if "main_image" in sql:
                if params and any("rjc-test-product-1" in p for p in params):
                    return [{"code": "rjc-test-product-1", "main_image": "https://img.example.com/mp.jpg"}]
            return []
        return []

    monkeypatch.setattr("appcore.db.query", fake_db_query)

    ad_alerts._batch_fetch_problem_ad_details([item1, item2], [
        {"matched_product_code": "rjc-test-product-1"},
        {"matched_product_code": "rjc-test-product-2"}
    ])

    assert item1.product_main_image == "https://img.example.com/mp.jpg"
    assert item2.product_main_image == "/medias/obj/79/medias/796/cover.png"
    assert len(query_queries) > 0
