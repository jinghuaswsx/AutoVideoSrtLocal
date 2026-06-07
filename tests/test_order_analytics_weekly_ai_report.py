from __future__ import annotations

from datetime import date, datetime

from appcore.order_analytics import weekly_ai_report as war


def _fake_overview(date_text, **kwargs):
    business_day = date.fromisoformat(date_text[:10])
    store = (kwargs.get("site_codes") or ["all"])[0]
    day_index = (business_day - date(2026, 5, 31)).days + 1
    store_factor = {"all": 1.0, "newjoy": 0.7, "omurio": 0.3}.get(store, 1.0)
    revenue = 1000 * day_index * store_factor
    spend = (500 if day_index <= 4 else 900) * store_factor
    profit = (180 if day_index <= 4 else -120) * store_factor
    overview = {
        "summary": {
            "order_count": int(10 * day_index * store_factor),
            "line_count": int(12 * day_index * store_factor),
            "units": int(15 * day_index * store_factor),
            "order_revenue": revenue - 100,
            "shipping_revenue": 100,
            "revenue_with_shipping": revenue,
            "ad_spend": spend,
            "meta_purchase_value": revenue * 0.9,
            "meta_purchases": int(8 * day_index * store_factor),
            "true_roas": revenue / spend,
            "meta_roas": revenue * 0.9 / spend,
        },
        "order_profit_summary": {
            "shopify_fee_total_usd": revenue * 0.05,
            "purchase_cost_with_estimate_usd": revenue * 0.2,
            "logistics_cost_with_estimate_usd": revenue * 0.12,
            "return_reserve_usd": revenue * 0.03,
            "profit_with_estimate_usd": profit,
            "profit_with_estimate_margin_pct": profit / revenue * 100,
            "global_break_even_roas": 1.25,
            "unallocated_ad_spend_usd": 0,
        },
        "scope": {"ad_source": "meta_ad_daily_campaign_metrics", "ad_granularity": "daily"},
        "freshness": {},
        "campaigns": [],
        "product_sales_stats": [],
    }
    if store == "all":
        overview["product_sales_stats"] = [
            {
                "product_id": 101,
                "product_code": "P101",
                "product_name": "Scale Product",
                "order_count": 4 if day_index <= 4 else 1,
                "units": 5 if day_index <= 4 else 1,
                "total_sales": 400 if day_index <= 4 else 90,
            },
            {
                "product_id": 202,
                "product_code": "P202",
                "product_name": "Low Order Product",
                "order_count": 1 if day_index == 6 else 0,
                "units": 1 if day_index == 6 else 0,
                "total_sales": 80 if day_index == 6 else 0,
            },
        ]
        overview["campaigns"] = [
            {
                "ad_account_id": "act_1",
                "ad_account_name": "Newjoy",
                "campaign_name": "P101 scale",
                "normalized_campaign_code": "P101",
                "matched_product_id": 101,
                "matched_product_code": "P101",
                "matched_product_name": "Scale Product",
                "spend_usd": 120 if day_index <= 4 else 20,
                "purchase_value_usd": 260 if day_index <= 4 else 30,
                "result_count": 2 if day_index <= 4 else 0,
            },
            {
                "ad_account_id": "act_1",
                "ad_account_name": "Newjoy",
                "campaign_name": "P202 waste",
                "normalized_campaign_code": "P202",
                "matched_product_id": 202,
                "matched_product_code": "P202",
                "matched_product_name": "Low Order Product",
                "spend_usd": 90 if day_index >= 5 else 10,
                "purchase_value_usd": 0 if day_index >= 5 else 20,
                "result_count": 0,
            },
        ]
    return overview


def _fake_product_profit(*, date_from, date_to):
    return {
        "summary": {
            "product_count": 2,
            "total_orders": 20,
            "total_revenue_usd": 2100,
            "total_profit_usd": 130,
            "total_ad_spend_usd": 900,
            "overall_roas": 2.33,
        },
        "rows": [
            {
                "product_id": 101,
                "product_code": "P101",
                "name": "Scale Product",
                "order_count": 19,
                "revenue_usd": 2000,
                "ad_cost_usd": 500,
                "roas": 4.0,
                "profit_usd": 420,
                "purchase_usd": 300,
                "shipping_cost_usd": 120,
                "cost_completeness": "ok",
            },
            {
                "product_id": 202,
                "product_code": "P202",
                "name": "Low Order Product",
                "order_count": 1,
                "revenue_usd": 100,
                "ad_cost_usd": 400,
                "roas": 0.25,
                "profit_usd": -290,
                "purchase_usd": 20,
                "shipping_cost_usd": 10,
                "cost_completeness": "ok",
            },
        ],
    }


def test_previous_complete_business_week_uses_sunday_to_saturday():
    week_start, week_end = war.previous_complete_business_week(datetime(2026, 6, 7, 12, 0, 0))

    assert week_start == date(2026, 5, 31)
    assert week_end == date(2026, 6, 6)


def test_normalize_week_start_snaps_to_sunday():
    assert war.normalize_week_start(date(2026, 6, 3)) == date(2026, 5, 31)
    assert war.normalize_week_start(date(2026, 5, 31)) == date(2026, 5, 31)


def test_build_weekly_data_package_aggregates_sources(monkeypatch):
    monkeypatch.setattr(war, "get_realtime_roas_overview", _fake_overview)
    monkeypatch.setattr(war, "generate_product_profit_list", _fake_product_profit)
    monkeypatch.setattr(
        war,
        "load_product_stability_summary",
        lambda limit=50: {
            "counts": {
                "total": 3,
                "stable_total": 1,
                "stable_7d": 1,
                "stable_30d": 0,
                "secondary_stable": 0,
                "potential": 0,
                "test": 1,
                "stopped": 0,
                "never": 0,
                "insufficient_history": 1,
            },
            "buckets": {
                "stable": [{
                    "product_id": 101,
                    "product_code": "P101",
                    "product_name": "Scale Product",
                    "status": "stable",
                    "stable_7d": True,
                    "stable_marks": ["7天稳定"],
                    "last_7d_orders": 140,
                    "details": {"delivery_start_date": "2026-05-20"},
                }],
                "test": [{
                    "product_id": 202,
                    "product_code": "P202",
                    "product_name": "Low Order Product",
                    "status": "test",
                    "last_7d_orders": 1,
                    "details": {"delivery_start_date": "2026-05-20"},
                }],
                "insufficient_history": [{
                    "product_id": 303,
                    "product_code": "P303",
                    "product_name": "New Product",
                    "status": "insufficient_history",
                    "last_7d_orders": 20,
                    "details": {"delivery_start_date": "2026-06-03"},
                }],
            },
            "warnings": [],
            "computed_at": "2026-06-07T12:00:00",
        },
    )
    monkeypatch.setattr(
        war,
        "load_product_lang_ad_summary_cache",
        lambda pids: {
            101: {
                "de": {
                    "lang": "de",
                    "active_7d_ad_spend_usd": 20,
                    "ad_spend_usd": 120,
                    "ad_roas": 1.8,
                    "pushed_video_count": 2,
                    "item_count": 3,
                    "delivery_status": "active",
                }
            }
        },
    )
    monkeypatch.setattr(
        war,
        "_load_quality_materials",
        lambda product_code, limit=5: [{
            "material_key": "mk-1",
            "material_name": "Winning English Video",
            "video_path": "/videos/winning.mp4",
            "spend_90_usd": 180.0,
            "ads_count": 6,
        }],
    )

    package = war.build_weekly_data_package(
        date(2026, 5, 31),
        date(2026, 6, 6),
        now=datetime(2026, 6, 7, 12, 0, 0),
    )

    assert package["period"]["week_definition"] == "sunday_to_saturday"
    assert package["period"]["week_start"] == date(2026, 5, 31)
    assert package["period"]["week_end"] == date(2026, 6, 6)
    assert len(package["daily_global"]) == 7
    assert set(package["daily_by_store"]) == {"all", "newjoy", "omurio"}
    assert package["segments"]["thursday_to_saturday"]["profit_usd"] < 0
    assert package["product_rows"][0]["product_code"] == "P101"
    assert any(row["product_code"] == "P202" for row in package["low_order_products"]["one_to_two"])
    assert any(row["normalized_campaign_code"] == "P202" for row in package["campaign_rows"])
    assert package["product_stability"]["counts"]["stable_total"] == 1
    assert package["product_stability"]["counts"]["insufficient_history"] == 1
    assert package["product_stability"]["buckets"]["stable"][0]["product_code"] == "P101"
    assert package["product_scope"]["evaluated_product_count"] == 2
    assert package["product_scope"]["excluded_under_7d_count"] == 1
    assert package["product_supplement_recommendations"]["country_expansion"][0]["product_code"] == "P101"
    assert package["product_supplement_recommendations"]["material_fill"][0]["material_key"] == "mk-1"
    assert package["rule_findings"]["ads_pause"]


def test_generate_ai_report_success_upserts(monkeypatch):
    package = {
        "period": {"week_start": date(2026, 5, 31), "week_end": date(2026, 6, 6)},
        "data_quality": {"status": "ok"},
        "summary": {"profit_usd": 120, "true_roas": 1.6},
    }
    writes = []
    monkeypatch.setattr(war, "query_one", lambda *a, **k: None)
    monkeypatch.setattr(war, "query", lambda *a, **k: [])
    monkeypatch.setattr(war, "execute", lambda *a, **k: writes.append((a, k)) or 1)
    monkeypatch.setattr(war, "build_weekly_data_package", lambda *a, **k: package)
    monkeypatch.setattr(
        war.llm_client,
        "invoke_chat",
        lambda *a, **k: {
            "json": {
                "business_health": {"status": "ok", "summary": "正常", "evidence": []},
                "product_direction": {"scale": [], "watch": [], "cut": []},
                "ad_actions": {"increase": [], "reduce": [], "pause": []},
                "risk_flags": [],
                "executive_summary": ["利润为正"],
            },
            "text": "{}",
            "usage_log_id": 9,
        },
    )

    report = war.generate_ai_report(date(2026, 6, 3), user_id=7, force=True)

    assert report["status"] == "success"
    assert writes
    params = writes[0][0][1]
    assert params[0] == date(2026, 5, 31)
    assert params[4] == "success"
    assert params[9] == 9


def test_generate_ai_report_parse_failure_stores_failed(monkeypatch):
    package = {
        "period": {"week_start": date(2026, 5, 31), "week_end": date(2026, 6, 6)},
        "data_quality": {"status": "ok"},
        "summary": {},
    }
    writes = []
    monkeypatch.setattr(war, "query_one", lambda *a, **k: None)
    monkeypatch.setattr(war, "query", lambda *a, **k: [])
    monkeypatch.setattr(war, "execute", lambda *a, **k: writes.append((a, k)) or 1)
    monkeypatch.setattr(war, "build_weekly_data_package", lambda *a, **k: package)
    monkeypatch.setattr(war.llm_client, "invoke_chat", lambda *a, **k: {"text": "not json"})

    report = war.generate_ai_report(date(2026, 5, 31), user_id=7, force=True)

    assert report["status"] == "failed"
    assert report["raw_text"] == "not json"
    assert writes
    assert writes[0][0][1][4] == "failed"
    assert writes[0][0][1][7] == "not json"
