import json
from datetime import date

from appcore import ad_material_ai_analysis as svc


def _row(**overrides):
    base = {
        "product_id": 1,
        "product_code": "demo-rjc",
        "product_name": "Demo",
        "spend_30d": 0,
        "orders_30d": 0,
        "results_30d": 0,
        "ad_count_30d": 0,
        "spend_7d": 0,
        "spend_yesterday": 0,
        "spend_today": 0,
        "orders_7d": 0,
        "revenue_30d": 0,
        "profit_30d": 0,
        "purchase_value_30d": 0,
        "local_material_count": 0,
    }
    base.update(overrides)
    base["true_roas_30d"] = (
        round(base["revenue_30d"] / base["spend_30d"], 4)
        if base["spend_30d"]
        else None
    )
    base["meta_roas_30d"] = (
        round(base["purchase_value_30d"] / base["spend_30d"], 4)
        if base["spend_30d"]
        else None
    )
    return base


def test_strip_rjc_for_mingkong_search_code():
    assert svc.strip_rjc("emergency-choking-relief-kit-rjc") == "emergency-choking-relief-kit"
    assert svc.strip_rjc("demo_RJC") == "demo"
    assert svc.strip_rjc("plain-code") == "plain-code"


def test_score_product_rows_filters_high_roas_low_volume_and_prefers_volume():
    tiny_high_roas = _row(
        product_id=1,
        product_code="tiny-rjc",
        spend_30d=3,
        orders_30d=1,
        revenue_30d=90,
        purchase_value_30d=90,
    )
    strong_volume = _row(
        product_id=2,
        product_code="strong-rjc",
        spend_30d=800,
        spend_7d=180,
        spend_yesterday=45,
        orders_30d=80,
        orders_7d=18,
        revenue_30d=1800,
        purchase_value_30d=1500,
        profit_30d=360,
        results_30d=120,
        ad_count_30d=18,
    )
    moderate = _row(
        product_id=3,
        product_code="moderate-rjc",
        spend_30d=90,
        orders_30d=9,
        revenue_30d=360,
        purchase_value_30d=280,
        profit_30d=80,
        results_30d=15,
        ad_count_30d=4,
    )

    ranked = svc.score_product_rows([tiny_high_roas, moderate, strong_volume], limit=10)

    assert [row["product_id"] for row in ranked] == [2, 3]
    assert "30天消耗有量" in ranked[0]["selection_reasons"]
    assert "真实ROAS较好" in ranked[0]["selection_reasons"]


def test_mk_search_codes_include_stripped_code_first():
    mapping = svc._mk_search_codes(["demo-product-rjc"])

    assert mapping["demo-product-rjc"] == ["demo-product", "demo-product-rjc"]


def test_target_countries_include_english_source_language():
    assert svc.TARGET_COUNTRIES[0]["country_code"] == "EN"
    assert svc.TARGET_COUNTRIES[0]["lang"] == "en"
    assert svc.TARGET_COUNTRIES[0]["tier"] == "source"
    assert svc._normalize_country_code("en") == "EN"
    assert svc._lang_for_country_code("EN") == "en"


def test_country_summaries_include_english_before_small_languages(monkeypatch):
    def fake_query(sql, params):
        assert "media_product_lang_ad_summary_cache" in sql
        assert params == (10,)
        return [{
            "product_id": 10,
            "lang": "en",
            "item_count": 2,
            "pushed_video_count": 1,
            "ad_spend_usd": 120,
            "purchase_value_usd": 360,
            "ad_roas": 3,
            "active_7d_ad_spend_usd": 40,
        }]

    monkeypatch.setattr(svc.db, "query", fake_query)

    summaries = svc._load_country_summaries([10])

    assert [item["country_code"] for item in summaries[10]] == [
        "EN", "DE", "FR", "IT", "ES", "JP", "SE", "NL", "PT",
    ]
    assert summaries[10][0]["lang_name"] == "英语"
    assert summaries[10][0]["delivery_status"] == "active"


def test_progress_payload_marks_current_step_and_keeps_logs():
    progress = svc._initial_progress(message="queued")

    updated = svc._progress_update(
        progress,
        step_key="candidate_score",
        step_status="running",
        percent=22,
        message="正在预筛",
    )

    assert updated["percent"] == 22
    assert updated["current_step"] == "candidate_score"
    assert updated["current_step_label"] == "规则预筛打分"
    assert updated["steps"][0]["status"] == "done"
    assert updated["steps"][1]["status"] == "running"
    assert updated["logs"][-1]["message"] == "正在预筛"


def test_normalize_progress_preserves_existing_checkpoint():
    progress = {
        "percent": 63,
        "current_step": "product_analysis",
        "steps": [{"key": "snapshot", "status": "done", "message": "done"}],
        "product_progress": {"current_index": 7, "total": 20},
        "logs": [{"time": "now", "level": "info", "message": "existing"}],
    }

    normalized = svc._normalize_progress(progress, message="resume")

    assert normalized["percent"] == 63
    assert normalized["current_step"] == "product_analysis"
    assert normalized["steps"][0]["status"] == "done"
    assert normalized["product_progress"]["current_index"] == 7
    assert normalized["logs"][-1]["message"] == "existing"


def test_selected_product_ids_from_stored_ranking_wrapper_and_legacy_payload():
    wrapped = {
        "selected_product_ids": [3, "2", 0],
        "ranking_result": {"mode": "ai"},
    }
    legacy = {
        "mode": "ai",
        "final_output": {
            "ranked_products": [
                {"product_id": 9, "rank": 2},
                {"product_id": 8, "rank": 1},
            ],
        },
    }

    assert svc._selected_product_ids_from_ranking(wrapped) == [3, 2]
    assert svc._selected_product_ids_from_ranking(legacy) == [8, 9]


def test_select_products_fills_from_rule_candidates_when_ai_returns_short_list():
    candidates = [
        _row(product_id=1, product_code="one-rjc", spend_30d=300, orders_30d=30),
        _row(product_id=2, product_code="two-rjc", spend_30d=200, orders_30d=20),
        _row(product_id=3, product_code="three-rjc", spend_30d=100, orders_30d=10),
    ]

    selected = svc._select_products(candidates, {"selected_product_ids": [2]})

    assert [item["product_id"] for item in selected] == [2, 1, 3]


def test_runtime_result_from_stored_project_product_result():
    stored = {
        "rank_no": 4,
        "metrics": {"product_id": 22, "product_code": "demo-rjc"},
        "country_summary": [{"country_code": "DE"}],
        "local_materials": [{"id": 1}],
        "mingkong_materials": [{"material_key": "mk"}],
        "ai_result": {"priority": "P1"},
        "action_items": [{"type": "view_task"}],
    }

    runtime = svc._runtime_result_from_stored(stored)

    assert runtime["rank_no"] == 4
    assert runtime["product"]["product_id"] == 22
    assert runtime["ai_result"]["priority"] == "P1"


def test_task_status_group_matches_strategist_dedup_policy():
    assert svc._task_status_group({"status": "blocked"}) == "pending"
    assert svc._task_status_group({"status": "assigned"}) == "in_progress"
    assert svc._task_status_group({"status": "done", "parent_status": "cancelled"}) == "completed"
    assert svc._task_status_group({"status": "assigned", "parent_status": "cancelled"}) == "cancelled"
    assert svc._task_status_group({"status": "cancelled"}) == "cancelled"


def test_existing_active_task_suppresses_duplicate_translation_action():
    product = _row(product_id=10, product_code="demo-rjc")
    blocking_task = {
        "task_id": 44,
        "country_code": "DE",
        "lang": "de",
        "status_group": "in_progress",
        "status_label": "进行中",
        "task_url": "/tasks/detail/44",
    }
    countries = [{
        "country_code": "DE",
        "lang": "de",
        "blocking_task": blocking_task,
        "cancelled_task": None,
        "tasks": [blocking_task],
    }]
    ai_result = {
        "primary_action": "expand_country",
        "country_actions": [{
            "country_code": "DE",
            "lang": "de",
            "action": "expand_country",
            "reason": "DE适合扩量",
        }],
    }

    decorated = svc._decorate_ai_result_with_tasks(ai_result, countries, [blocking_task])
    actions = svc._build_action_items(product, decorated, [], countries)

    assert decorated["primary_action"] == "hold"
    assert decorated["country_actions"][0]["duplicate_suppressed"] is True
    assert decorated["country_actions"][0]["existing_task"]["task_id"] == 44
    assert not [
        item for item in actions
        if item["type"] == "create_translation_task" and item.get("country_code") == "DE"
    ]
    task_actions = [item for item in actions if item["type"] == "view_task"]
    assert task_actions[0]["task_id"] == 44
    assert task_actions[0]["url"] == "/tasks/detail/44"


def test_cancelled_task_keeps_link_and_allows_new_translation_action():
    product = _row(product_id=10, product_code="demo-rjc")
    cancelled_task = {
        "task_id": 45,
        "country_code": "JP",
        "lang": "ja",
        "status_group": "cancelled",
        "status_label": "已取消",
        "task_url": "/tasks/detail/45",
    }
    countries = [{
        "country_code": "JP",
        "lang": "ja",
        "blocking_task": None,
        "cancelled_task": cancelled_task,
        "tasks": [cancelled_task],
    }]
    ai_result = {
        "primary_action": "expand_country",
        "country_actions": [{
            "country_code": "JP",
            "lang": "ja",
            "action": "expand_country",
            "reason": "JP可重新测试",
        }],
    }

    decorated = svc._decorate_ai_result_with_tasks(ai_result, countries, [cancelled_task])
    actions = svc._build_action_items(product, decorated, [], countries)

    assert decorated["country_actions"][0]["cancelled_task"]["task_id"] == 45
    assert any(item["type"] == "view_task" and item["task_id"] == 45 for item in actions)
    create_actions = [
        item for item in actions
        if item["type"] == "create_translation_task" and item.get("country_code") == "JP"
    ]
    assert create_actions
    assert create_actions[0]["target_lang"] == "ja"


def test_import_mk_video_action_payload_contains_required_download_fields():
    product = _row(product_id=10, product_code="demo-rjc", user_id=7)
    material = {
        "material_key": "mk1",
        "product_code": "demo",
        "product_name": "Demo",
        "product_url": "https://cozyhoome.com/products/demo-rjc",
        "mk_product_id": 456,
        "mk_product_name": "MK Demo",
        "video_name": "demo.mp4",
        "video_path": "folder/demo.mp4",
        "video_image_path": "folder/demo.jpg",
        "cumulative_90_spend": 123,
        "video_ads_count": 4,
        "video_duration_seconds": 15,
    }

    actions = svc._build_action_items(product, {}, [material], [])
    import_action = next(item for item in actions if item["type"] == "import_mk_video")
    meta = import_action["payload"]["mk_video_metadata"]

    assert import_action["payload"]["product_owner_id"] == 7
    assert meta["filename"] == "demo.mp4"
    assert meta["mp4_url"] == "/medias/api/mk-video?path=folder%2Fdemo.mp4"
    assert meta["cover_url"] == "/medias/api/mk-media?path=folder%2Fdemo.jpg"
    assert meta["duration_seconds"] == 15
    assert meta["mk_product_id"] == 456


def test_serialize_product_result_upgrades_legacy_import_payload():
    material = {
        "material_key": "mk1",
        "product_code": "demo",
        "product_name": "Demo",
        "product_url": "https://cozyhoome.com/products/demo-rjc",
        "video_name": "legacy.mp4",
        "video_path": "legacy/legacy.mp4",
        "video_image_path": "legacy/legacy.jpg",
        "mk_video_metadata": {"product_code": "demo-rjc"},
    }
    row = {
        "id": 1,
        "project_id": 2,
        "rank_no": 3,
        "product_id": 10,
        "product_code": "demo-rjc",
        "product_name": "Demo",
        "score": 88,
        "metrics_json": "{}",
        "country_summary_json": "[]",
        "local_materials_json": "[]",
        "mingkong_materials_json": json.dumps([material], ensure_ascii=False),
        "ai_result_json": "{}",
        "action_items_json": json.dumps([
            {
                "type": "import_mk_video",
                "material_key": "mk1",
                "payload": {
                    "mk_video_metadata": {"product_code": "demo-rjc"},
                    "product_owner_id": 7,
                },
            }
        ], ensure_ascii=False),
        "created_at": None,
        "updated_at": None,
    }

    result = svc._serialize_product_result(row)
    meta = result["action_items"][0]["payload"]["mk_video_metadata"]

    assert meta["product_code"] == "demo-rjc"
    assert meta["filename"] == "legacy.mp4"
    assert meta["mp4_url"] == "/medias/api/mk-video?path=legacy%2Flegacy.mp4"
    assert meta["cover_url"] == "/medias/api/mk-media?path=legacy%2Flegacy.jpg"


def test_english_action_does_not_create_small_language_translation_task():
    product = _row(product_id=10, product_code="demo-rjc")
    countries = [{
        "country_code": "EN",
        "lang": "en",
        "blocking_task": None,
        "cancelled_task": None,
        "tasks": [],
    }]
    ai_result = {
        "primary_action": "same_country_new_material",
        "country_actions": [{
            "country_code": "EN",
            "lang": "en",
            "action": "same_country_new_material",
            "reason": "英语素材仍在跑，建议补英语新素材。",
        }],
    }

    actions = svc._build_action_items(product, ai_result, [], countries)

    assert not [item for item in actions if item["type"] == "create_translation_task"]
    assert any(item["type"] == "supplement_workbench" for item in actions)


def test_material_review_prompt_tells_model_to_exclude_missing_modules():
    payload = {
        "current_date": "2026-06-10",
        "product_brief": {"code": 0, "data": {"matrix": {"product_name": "Demo"}}, "message": ""},
        "creator_brief": {},
        "candidate_video": {},
        "stage1_visual_brief": {},
        "_adapter_notes": {
            "missing_modules": [
                "creator_brief.commerce_metrics.gpm_ratio",
                "candidate_video",
                "stage1_visual_brief",
                "future_45d_trend",
            ]
        },
    }

    prompt = svc._material_review_prompt(payload)

    assert "不要补全不存在的数据" in prompt
    assert "score 必须为 null" in prompt
    assert "included=false" in prompt
    assert "不要把缺失解释成表现差" in prompt


def test_run_product_analysis_uses_googlewj_material_review(monkeypatch):
    product = _row(product_id=10, product_code="demo-rjc", spend_30d=500, orders_30d=30)
    review_input = {
        "current_date": "2026-06-10",
        "product_brief": {"code": 0, "data": {"matrix": {"product_name": "Demo"}}, "message": ""},
        "creator_brief": {},
        "candidate_video": {},
        "stage1_visual_brief": {},
        "_adapter_notes": {"missing_modules": ["future_45d_trend"]},
    }
    captured = {}

    monkeypatch.setattr(svc, "_build_material_review_input", lambda product, local, mk: review_input)

    def fake_invoke(use_case, **kwargs):
        captured["use_case"] = use_case
        captured["kwargs"] = kwargs
        return {
            "json": {
                "final_decision": "条件通过",
                "quality_score": 72,
                "score_breakdown": {},
                "analysis_reason": {"final_judgment_reason": "商品历史有基础，因此判断为条件通过。"},
                "material_plan": {
                    "risk_alerts": [],
                    "editing_plan": [],
                    "hook_suggestions": [],
                    "highlight_segments_to_move_forward": [],
                    "copy_extraction": {
                        "original_language": "unknown",
                        "original_copy": "未识别到原始文案",
                        "english_translation": "No original copy detected.",
                        "copy_source": "unknown",
                    },
                },
            }
        }

    monkeypatch.setattr(svc.llm_client, "invoke_generate", fake_invoke)

    result = svc._run_product_analysis(
        product,
        countries=[],
        local_materials=[],
        mk_materials=[],
        project_id=99,
        user_id=1,
        run_ai=True,
    )

    assert captured["use_case"] == "medias.ad_material_ai_analysis_product_analysis"
    assert captured["kwargs"]["provider_override"] == "google_wj"
    assert captured["kwargs"]["model_override"] == "gemini-3.5-flash"
    assert captured["kwargs"]["response_schema"] == svc.MATERIAL_REVIEW_RESPONSE_SCHEMA
    assert result["material_review_input"] == review_input
    assert result["material_review_result"]["final_decision"] == "条件通过"
    assert result["priority"] == "P1"
    assert result["mode"] == "ai"


def test_rank_input_includes_breakeven_roas_context():
    row = _row(
        product_id=7,
        spend_30d=400,
        revenue_30d=800,
        purchase_value_30d=700,
        orders_30d=40,
    )
    row["effective_breakeven_roas"] = 1.6

    payload = svc._rank_input(row)

    assert payload["effective_breakeven_roas"] == 1.6
    # true_roas_30d = 2.0 → 2.0 / 1.6 = 1.25
    assert payload["roas_vs_breakeven"] == 1.25
    assert "roas_vs_breakeven" in svc._ranking_prompt({"products": [payload]})


def test_rank_input_breakeven_missing_yields_null_ratio():
    row = _row(product_id=8, spend_30d=100, revenue_30d=200, purchase_value_30d=150, orders_30d=10)
    row["effective_breakeven_roas"] = None

    payload = svc._rank_input(row)

    assert payload["effective_breakeven_roas"] is None
    assert payload["roas_vs_breakeven"] is None


def test_load_ad_rows_realtime_floor_excludes_daily_covered_days(monkeypatch):
    calls = []

    def fake_query(sql, args=None):
        calls.append((sql, args))
        return []

    monkeypatch.setattr(svc.db, "query", fake_query)
    monkeypatch.setattr(
        svc.db, "query_one",
        lambda sql, args=None: {"value": date(2026, 6, 11)},
    )

    svc._load_ad_rows(date(2026, 5, 14), date(2026, 6, 12))

    realtime_calls = [(sql, args) for sql, args in calls if "realtime" in sql]
    assert realtime_calls, "realtime 查询应存在"
    _, args = realtime_calls[0]
    # realtime 窗口下限 = daily 已覆盖最大业务日 + 1，避免同业务日双计
    assert date(2026, 6, 12) in tuple(args)
    assert date(2026, 5, 14) not in tuple(args)


def test_load_ad_rows_skips_realtime_when_daily_covers_current_day(monkeypatch):
    calls = []

    def fake_query(sql, args=None):
        calls.append((sql, args))
        return []

    monkeypatch.setattr(svc.db, "query", fake_query)
    monkeypatch.setattr(
        svc.db, "query_one",
        lambda sql, args=None: {"value": date(2026, 6, 12)},
    )

    svc._load_ad_rows(date(2026, 5, 14), date(2026, 6, 12))

    realtime_calls = [(sql, args) for sql, args in calls if "realtime" in sql]
    assert not realtime_calls, "daily 已覆盖开放业务日时不应再叠加 realtime"


def test_material_ad_rows_realtime_floor_uses_product_daily_max(monkeypatch):
    calls = []

    def fake_query(sql, args=None):
        calls.append((sql, args))
        return []

    monkeypatch.setattr(svc.db, "query", fake_query)
    monkeypatch.setattr(
        svc.db, "query_one",
        lambda sql, args=None: {"value": date(2026, 6, 10)},
    )

    svc._load_product_ad_rows_for_materials(5)

    realtime_calls = [(sql, args) for sql, args in calls if "realtime" in sql]
    assert realtime_calls
    sql, args = realtime_calls[0]
    assert "business_date > %s" in sql
    assert date(2026, 6, 10) in tuple(args)


def test_snake_batches_mix_strong_and_weak_candidates():
    items = [{"product_id": i, "score": 100 - i} for i in range(60)]
    batches = svc._snake_batches(items, size=20)
    assert [len(b) for b in batches] == [20, 20, 20]
    # 蛇形分配：全局前 3 名必须分散在 3 个不同批次，避免强者同批互斥
    for batch in batches:
        ids = {item["product_id"] for item in batch}
        assert ids & {0, 1, 2}
    # 不丢不重
    all_ids = sorted(item["product_id"] for batch in batches for item in batch)
    assert all_ids == list(range(60))


def test_snake_batches_small_input_returns_single_batch():
    items = [{"product_id": i} for i in range(8)]
    batches = svc._snake_batches(items, size=20)
    assert len(batches) == 1
    assert [item["product_id"] for item in batches[0]] == list(range(8))


def test_resolve_billing_user_id_handles_missing_and_fallback(monkeypatch):
    # 1. 显式传入 user_id 时，直接返回
    assert svc._resolve_billing_user_id(42) == 42
    assert svc._resolve_billing_user_id("100") == 100

    # 2. 传入 None，并且数据库正常返回用户
    monkeypatch.setattr(svc.db, "query_one", lambda sql, args=None: {"id": 99})
    assert svc._resolve_billing_user_id(None) == 99

    # 3. 传入 None，但数据库返回空
    monkeypatch.setattr(svc.db, "query_one", lambda sql, args=None: None)
    assert svc._resolve_billing_user_id(None) is None

    # 4. 传入 None，但数据库查询抛出异常
    def fake_query_error(sql, args=None):
        raise RuntimeError("DB error")
    monkeypatch.setattr(svc.db, "query_one", fake_query_error)
    assert svc._resolve_billing_user_id(None) is None
