from __future__ import annotations

import json
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
            {
                "product_id": 505,
                "product_code": "P505",
                "product_name": "Potential Product",
                "order_count": 2,
                "units": 2,
                "total_sales": 180,
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
                "spend_usd": 90 if day_index == 6 else 0,
                "purchase_value_usd": 0,
                "result_count": 0,
            },
            {
                "ad_account_id": "act_1",
                "ad_account_name": "Newjoy",
                "campaign_name": "P505 potential",
                "normalized_campaign_code": "P505",
                "matched_product_id": 505,
                "matched_product_code": "P505",
                "matched_product_name": "Potential Product",
                "spend_usd": 5,
                "purchase_value_usd": 20,
                "result_count": 1,
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


def _fake_order_fallback_overview(date_text, **kwargs):
    overview = _fake_overview(date_text, **kwargs)
    store = (kwargs.get("site_codes") or ["all"])[0]
    if store == "all":
        business_day = date.fromisoformat(date_text[:10])
        day_index = (business_day - date(2026, 5, 31)).days + 1
        overview["product_sales_stats"] = [
            {
                "product_id": 101,
                "product_code": "P101",
                "product_name": "Order Stable Product",
                "order_count": 30,
                "units": 30,
                "total_sales": 3000,
            },
            {
                "product_id": 505,
                "product_code": "P505",
                "product_name": "Order Secondary Product",
                "order_count": 11,
                "units": 11,
                "total_sales": 1100,
            },
            {
                "product_id": 202,
                "product_code": "P202",
                "product_name": "Long Tail Product",
                "order_count": 3 if day_index == 1 else 0,
                "units": 3 if day_index == 1 else 0,
                "total_sales": 240 if day_index == 1 else 0,
            },
        ]
        overview["campaigns"] = []
    return overview


def test_previous_complete_business_week_uses_sunday_to_saturday():
    week_start, week_end = war.previous_complete_business_week(datetime(2026, 6, 7, 12, 0, 0))

    assert week_start == date(2026, 5, 31)
    assert week_end == date(2026, 6, 6)


def test_weekly_ai_report_registers_sunday_20_beijing(monkeypatch):
    calls = []

    def fake_add_controlled_job(scheduler, task_code, func, trigger, **kwargs):
        calls.append((scheduler, task_code, func, trigger, kwargs))

    monkeypatch.setattr(war.scheduled_tasks, "add_controlled_job", fake_add_controlled_job)
    scheduler = object()

    war.register(scheduler)

    assert len(calls) == 1
    assert calls[0][0] is scheduler
    assert calls[0][1] == war.TASK_CODE
    assert calls[0][2] is war.run_scheduled_report
    assert calls[0][3] == "cron"
    assert calls[0][4]["day_of_week"] == "sun"
    assert calls[0][4]["hour"] == 20
    assert calls[0][4]["minute"] == 0


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
                "secondary_stable": 1,
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
                "secondary_stable": [{
                    "product_id": 505,
                    "product_code": "P505",
                    "product_name": "Potential Product",
                    "status": "secondary_stable",
                    "last_7d_orders": 14,
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
    monkeypatch.setattr(
        war,
        "_load_weekly_created_products",
        lambda week_start, week_end, notes: [
            {
                "product_id": 101,
                "product_code": "P101",
                "product_name": "Scale Product",
                "name": "Scale Product",
                "main_image": "",
                "created_at": "2026-06-01 10:00:00",
            },
            {
                "product_id": 202,
                "product_code": "P202",
                "product_name": "Low Order Product",
                "name": "Low Order Product",
                "main_image": "",
                "created_at": "2026-06-02 10:00:00",
            },
            {
                "product_id": 303,
                "product_code": "P303",
                "product_name": "New Product",
                "name": "New Product",
                "main_image": "",
                "created_at": "2026-06-03 10:00:00",
            },
            {
                "product_id": 505,
                "product_code": "P505",
                "product_name": "Potential Product",
                "name": "Potential Product",
                "main_image": "",
                "created_at": "2026-06-04 10:00:00",
            },
        ],
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
    assert not any(row["product_code"] == "P202" for row in package["low_order_products"]["one_to_two"])
    assert any(row["normalized_campaign_code"] == "P202" for row in package["campaign_rows"])
    assert package["product_stability"]["counts"]["stable_total"] == 1
    assert package["product_stability"]["counts"]["secondary_stable"] == 1
    assert package["product_stability"]["counts"]["test"] == 2
    assert package["product_stability"]["counts"]["insufficient_history"] == 0
    assert package["product_stability"]["buckets"]["stable"][0]["product_code"] == "P101"
    assert package["product_scope"]["evaluated_product_count"] == 2
    assert package["product_scope"]["excluded_without_continuous_7d_active_count"] == 2
    assert package["product_stability"]["buckets"]["test"][0]["display_label"] == "测试中"
    assert package["product_stability"]["buckets"]["test"][0]["weekly_active_day_count"] in {0, 1}
    share = package["product_tier_order_share"]
    assert share["weekly"]["total_orders"] == 34
    assert share["weekly"]["stable"]["order_count"] == 19
    assert share["weekly"]["stable"]["order_share_pct"] == 55.8824
    assert share["weekly"]["potential"]["order_count"] == 14
    assert share["weekly"]["potential"]["order_share_pct"] == 41.1765
    assert share["weekly"]["other"]["order_count"] == 1
    assert share["weekly"]["other"]["order_share_pct"] == 2.9412
    assert share["daily"][0]["date"] == "2026-05-31"
    assert share["daily"][0]["stable"]["order_count"] == 4
    assert share["daily"][0]["potential"]["order_count"] == 2
    assert share["daily"][0]["other"]["order_count"] == 0
    assert share["daily"][5]["stable"]["order_share_pct"] == 25.0
    assert share["daily"][5]["potential"]["order_share_pct"] == 50.0
    assert share["daily"][5]["other"]["order_share_pct"] == 25.0
    assert package["product_supplement_recommendations"]["country_expansion"][0]["product_code"] == "P101"
    assert package["product_supplement_recommendations"]["material_fill"][0]["material_key"] == "mk-1"
    assert not any(row.get("matched_product_code") == "P202" for row in package["rule_findings"]["ads_pause"])
    potential_new = package["potential_new_products"]
    assert potential_new["summary"]["weekly_created_product_count"] == 4
    assert potential_new["summary"]["testing_candidate_count"] == 2
    assert potential_new["rows"][0]["product_code"] == "P202"
    assert potential_new["rows"][0]["label"] == "潜力新品"
    assert potential_new["rows"][0]["product_grade"] == "测试中"
    assert potential_new["rows"][0]["avg_daily_orders"] == 0.14
    assert not any(row["product_code"] in {"P101", "P505"} for row in potential_new["rows"])


def test_build_weekly_data_package_fallback_classifies_orders_when_stability_cache_empty(monkeypatch):
    monkeypatch.setattr(war, "get_realtime_roas_overview", _fake_order_fallback_overview)
    monkeypatch.setattr(war, "generate_product_profit_list", lambda **kwargs: {"summary": {}, "rows": []})
    monkeypatch.setattr(
        war,
        "load_product_stability_summary",
        lambda limit=50: {
            "counts": {"total": 0},
            "buckets": {
                "stable": [],
                "secondary_stable": [],
                "potential": [],
                "test": [],
                "stopped": [],
                "never": [],
                "insufficient_history": [],
            },
            "warnings": [],
            "computed_at": None,
        },
    )
    monkeypatch.setattr(war, "load_product_lang_ad_summary_cache", lambda pids: {})

    package = war.build_weekly_data_package(
        date(2026, 5, 31),
        date(2026, 6, 6),
        now=datetime(2026, 6, 7, 12, 0, 0),
    )

    stability = package["product_stability"]
    assert stability["source"] == "product_sales_stats_order_fallback"
    assert stability["counts"]["stable_total"] == 1
    assert stability["counts"]["secondary_stable"] == 1
    assert stability["counts"]["test"] == 1
    assert stability["buckets"]["stable"][0]["product_code"] == "P101"
    assert stability["buckets"]["secondary_stable"][0]["product_code"] == "P505"
    assert stability["warnings"][0]["code"] == "product_stability_order_fallback"
    assert package["product_scope"]["fallback_applied"] is True

    share = package["product_tier_order_share"]["weekly"]
    assert share["total_orders"] == 290
    assert share["stable"]["order_count"] == 210
    assert share["stable"]["order_share_pct"] == 72.4138
    assert share["potential"]["order_count"] == 77
    assert share["potential"]["order_share_pct"] == 26.5517
    assert share["other"]["order_count"] == 3
    assert share["other"]["order_share_pct"] == 1.0345


def test_existing_report_backfills_missing_product_tier_order_share(monkeypatch):
    calls = []
    backfilled_share = {
        "weekly": {
            "label": "整周",
            "total_orders": 20,
            "stable": {"key": "stable", "label": "稳定品", "order_count": 12, "order_share_pct": 60.0},
            "potential": {"key": "potential", "label": "潜力品", "order_count": 5, "order_share_pct": 25.0},
            "other": {"key": "other", "label": "其他品", "order_count": 3, "order_share_pct": 15.0},
        },
        "daily": [],
        "source": "product_sales_stats",
    }

    monkeypatch.setattr(
        war,
        "query_one",
        lambda *a, **k: {
            "week_start_date": date(2026, 5, 31),
            "week_end_date": date(2026, 6, 6),
            "generated_at": datetime(2026, 6, 7, 20, 5, 0),
            "generated_by": "manual",
            "status": "success",
            "data_snapshot_json": json.dumps({
                "summary": {"order_count": 20},
                "data_quality": {"status": "ok"},
                "product_stability": {"counts": {"stable_total": 1}},
            }),
            "ai_report_json": json.dumps({"executive_summary": ["旧报告结论"]}),
            "raw_text": "{}",
            "data_quality_json": json.dumps({"status": "ok"}),
            "usage_log_id": 9,
            "error_message": None,
        },
    )
    monkeypatch.setattr(war, "query", lambda *a, **k: [])

    def fake_build_weekly_data_package(week_start, week_end):
        calls.append((week_start, week_end))
        return {
            "data_quality": {"status": "ok"},
            "product_tier_order_share": backfilled_share,
            "product_stability": {"counts": {"stable_total": 1}},
        }

    monkeypatch.setattr(war, "build_weekly_data_package", fake_build_weekly_data_package)

    report = war.get_or_build_report_payload(date(2026, 6, 3))

    assert calls == [(date(2026, 5, 31), date(2026, 6, 6))]
    assert report["status"] == "success"
    assert report["report"]["executive_summary"] == ["旧报告结论"]
    share = report["data_package"]["product_tier_order_share"]
    assert share["weekly"]["total_orders"] == 20
    assert share["weekly"]["stable"]["order_count"] == 12
    assert share["weekly"]["potential"]["order_count"] == 5
    assert share["weekly"]["other"]["order_count"] == 3


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
    saved_report = json.loads(params[6])
    assert saved_report["product_action_evaluations"] == []
    assert saved_report["product_action_evaluation_summary"]["total"] == 0
    assert params[9] == 9


def _minimal_product_candidate(product_id=101, product_code="P101"):
    return {
        "identity": {
            "product_id": product_id,
            "product_code": product_code,
            "product_name": "示例产品",
            "product_main_image_url": "/medias/cover/101?lang=en",
            "product_cover_url": "/medias/cover/101?lang=en",
            "media_search_url": f"/medias/?q={product_code}",
        },
        "eligibility": {"status": "stable", "label": "稳定品"},
        "stability": {"last_7d_orders": 210, "overall_roas": 2.1},
        "weekly_product": {"order_count": 28, "profit_usd": 120},
        "campaigns": [],
        "order_country_distribution": [{"country_code": "DE", "order_count": 12}],
        "ad_country_distribution": [{"market_country": "DE", "spend_usd": 80}],
        "material_summary_by_lang": {"de": {"pushed_video_count": 1}},
        "local_material_candidates": [{"source": "local", "material_id": "10", "filename": "demo.mp4"}],
        "mingkong_summary": {"video_count": 4, "total_90_spend": 1200},
        "mingkong_material_candidates": [{"source": "mingkong", "material_key": "mk1", "video_path": "mk/demo.mp4"}],
        "target_country_tiers": war._target_country_tiers(),
        "data_quality_notes": [],
    }


def test_product_ai_candidates_only_stable_secondary_stable_and_potential(monkeypatch):
    monkeypatch.setattr(war, "_load_product_ad_summary", lambda product_ids, notes: {})
    monkeypatch.setattr(war, "_load_material_summary_by_lang", lambda product_ids, notes: {})
    monkeypatch.setattr(war, "_load_order_country_distribution", lambda product_ids, week_start, week_end, notes: {})
    monkeypatch.setattr(war, "_load_ad_country_distribution", lambda product_ids, week_start, week_end, notes: {})
    monkeypatch.setattr(war, "_load_local_material_candidates", lambda product_ids, notes: {})
    monkeypatch.setattr(war, "_load_mingkong_product_summary", lambda product_codes, notes: {})
    monkeypatch.setattr(war, "_load_mingkong_material_candidates", lambda product_codes, notes: {})

    product_stability = {
        "buckets": {
            "stable": [{"product_id": 101, "product_code": "P101", "product_name": "稳定品"}],
            "secondary_stable": [{"product_id": 151, "product_code": "P151", "product_name": "二级稳定品"}],
            "potential": [{"product_id": 202, "product_code": "P202", "product_name": "潜力品"}],
            "test": [{"product_id": 303, "product_code": "P303", "product_name": "测试品"}],
            "stopped": [{"product_id": 404, "product_code": "P404", "product_name": "已停投"}],
        }
    }

    candidates = war._build_product_ai_evaluation_candidates(
        product_stability=product_stability,
        product_rows=[],
        campaign_rows=[],
        week_start=date(2026, 5, 31),
        week_end=date(2026, 6, 6),
        identity_by_id={
            101: {"id": 101, "product_code": "P101", "name": "稳定品", "main_image": "https://cdn.example/p101.jpg"},
            151: {"id": 151, "product_code": "P151", "name": "二级稳定品", "main_image": ""},
            202: {"id": 202, "product_code": "P202", "name": "潜力品", "main_image": ""},
        },
        identity_by_code={},
        global_notes=[],
    )

    assert [c["identity"]["product_code"] for c in candidates] == ["P101", "P151", "P202"]
    assert candidates[0]["eligibility"]["status"] == "stable"
    assert candidates[1]["eligibility"]["status"] == "secondary_stable"
    assert candidates[2]["eligibility"]["status"] == "potential"
    assert candidates[0]["identity"]["product_main_image_url"] == "https://cdn.example/p101.jpg"
    assert candidates[2]["identity"]["product_main_image_url"] == "/medias/cover/202?lang=en"
    assert [tier["country_codes"] for tier in candidates[0]["target_country_tiers"]] == [
        ["DE", "FR"],
        ["ES", "IT", "JP"],
        ["SE", "NL", "PT"],
    ]


def test_product_action_prompt_contains_country_tiers_and_material_sources():
    prompt = war.build_product_action_evaluation_prompt(_minimal_product_candidate())

    assert "第一阶梯 DE/FR" in prompt
    assert "order_country_distribution" in prompt
    assert "ad_country_distribution" in prompt
    assert "local_material_candidates" in prompt
    assert "mingkong_material_candidates" in prompt
    assert "mk/demo.mp4" in prompt


def test_invoke_product_action_evaluation_uses_openrouter_gemini_schema(monkeypatch):
    captured = {}

    def fake_invoke_generate(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {
            "json": {
                "product_id": 999,
                "product_code": "WRONG",
                "product_name": "WRONG",
                "status": "success",
                "primary_action": "supplement_material",
                "action_label": "补素材",
                "confidence": 88,
                "stage": {"current_tier": "tier1", "next_tier": "tier1", "reason": "德法优先"},
                "country_plan": [],
                "material_plan": {
                    "needs_material": True,
                    "priority_country_codes": ["DE"],
                    "recommended_source": "mingkong",
                    "recommended_material": {"material_id": "mk1"},
                    "localization_steps": ["翻译德语"],
                },
                "budget_plan": {"summary": "小预算测试", "increase": [], "reduce": [], "pause": []},
                "evidence": ["有明空素材"],
                "risk_flags": [],
                "next_steps": ["先补 DE 素材"],
            }
        }

    monkeypatch.setattr(war.llm_client, "invoke_generate", fake_invoke_generate)

    result = war.invoke_product_action_evaluation(
        _minimal_product_candidate(),
        user_id=7,
        week_start=date(2026, 5, 31),
        week_end=date(2026, 6, 6),
    )

    assert captured["use_case_code"] == war.PRODUCT_EVALUATION_USE_CASE_CODE
    assert captured["kwargs"]["provider_override"] == "openrouter"
    assert captured["kwargs"]["model_override"] == "google/gemini-3.5-flash"
    assert captured["kwargs"]["response_schema"] is war.PRODUCT_ACTION_RESPONSE_SCHEMA
    assert "DE/FR" in captured["kwargs"]["prompt"]
    assert result["product_id"] == 101
    assert result["product_code"] == "P101"
    assert result["status"] == "success"


def test_product_action_evaluation_failure_is_per_product(monkeypatch):
    candidates = [
        _minimal_product_candidate(101, "P101"),
        _minimal_product_candidate(202, "P202"),
    ]

    def fake_invoke(candidate, **kwargs):
        if candidate["identity"]["product_code"] == "P202":
            raise RuntimeError("boom")
        return {
            "product_id": 101,
            "product_code": "P101",
            "product_name": "示例产品",
            "status": "success",
            "primary_action": "hold",
            "action_label": "观察",
            "confidence": 70,
            "stage": {"current_tier": "tier1", "next_tier": "tier1", "reason": ""},
            "country_plan": [],
            "material_plan": {},
            "budget_plan": {},
            "evidence": [],
            "risk_flags": [],
            "next_steps": [],
        }

    monkeypatch.setattr(war, "invoke_product_action_evaluation", fake_invoke)

    evaluations = war._generate_product_action_evaluations(
        {"product_ai_evaluation_candidates": candidates},
        user_id=7,
        week_start=date(2026, 5, 31),
        week_end=date(2026, 6, 6),
    )

    assert [item["status"] for item in evaluations] == ["success", "failed"]
    assert evaluations[1]["product_code"] == "P202"
    assert evaluations[1]["primary_action"] == "investigate"


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
