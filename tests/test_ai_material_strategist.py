import json
from pathlib import Path

from appcore import ai_material_strategist as svc


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
    assert svc._normalize_country_code("en") == "EN"
    assert svc._lang_for_country_code("EN") == "en"


def test_country_summaries_include_english_before_small_languages(monkeypatch):
    def fake_query(sql, params=()):
        return [
            {
                "product_id": 10,
                "lang": "en",
                "item_count": 2,
                "pushed_video_count": 1,
                "ad_spend_usd": 500.0,
                "purchase_value_usd": 1500.0,
                "ad_roas": 3.0,
                "active_7d_ad_spend_usd": 100.0,
            }
        ]

    monkeypatch.setattr(svc.db, "query", fake_query)

    summaries = svc._load_country_summaries([10])

    assert [item["country_code"] for item in summaries[10]][:3] == ["EN", "DE", "FR"]
    assert summaries[10][0]["lang"] == "en"
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
        "runner_state": "running",
        "runner_heartbeat_at": "2026-06-10 19:00:00",
        "recovery": {"reason": "service_restart", "status": "running"},
        "steps": [{"key": "snapshot", "status": "done", "message": "done"}],
        "product_progress": {"current_index": 7, "total": 30},
        "logs": [{"time": "now", "level": "info", "message": "existing"}],
    }

    normalized = svc._normalize_progress(progress, message="resume")

    assert normalized["percent"] == 63
    assert normalized["current_step"] == "product_analysis"
    assert normalized["steps"][0]["status"] == "done"
    assert normalized["product_progress"]["current_index"] == 7
    assert normalized["logs"][-1]["message"] == "existing"
    assert normalized["runner_state"] == "running"
    assert normalized["runner_heartbeat_at"] == "2026-06-10 19:00:00"
    assert normalized["recovery"]["reason"] == "service_restart"


def test_mark_startup_interrupted_project_for_recovery_writes_recovery_checkpoint(monkeypatch):
    progress = svc._progress_update(
        svc._initial_progress(message="running"),
        step_key="product_analysis",
        step_status="running",
        percent=63,
        message="分析第 7/30 个产品",
        product_progress={"current_index": 7, "total": 30},
    )
    row = {
        "id": 9,
        "project_name": "AI素材军师 demo",
        "status": "running",
        "user_id": 1,
        "provider_code": "openrouter",
        "model_id": "google/gemini-3.5-flash",
        "summary_json": "{}",
        "progress_json": json.dumps(progress, ensure_ascii=False),
        "share_token": None,
        "share_enabled_at": None,
        "error_message": "",
        "started_at": "2026-06-10 19:00:00",
        "finished_at": None,
        "created_at": "2026-06-10 19:00:00",
        "updated_at": "2026-06-10 19:04:00",
    }
    writes = []

    fake_lock = object()
    monkeypatch.setattr(svc, "_with_project_lock", lambda timeout_seconds=0: fake_lock)
    monkeypatch.setattr(svc, "_release_project_lock", lambda conn: None)
    monkeypatch.setattr(svc.db, "query_one", lambda *args, **kwargs: row)
    monkeypatch.setattr(svc.db, "query", lambda *args, **kwargs: [])
    def fake_execute(sql, params=None):
        writes.append((sql, params))
        return 1

    monkeypatch.setattr(svc.db, "execute", fake_execute)
    project = svc.mark_startup_interrupted_project_for_recovery()

    assert project["id"] == 9
    assert project["status"] == "running"
    assert project["user_id"] == 1
    assert writes
    saved = json.loads(writes[0][1][0])
    assert saved["runner_state"] == "resume_scheduled"
    assert saved["recovery"]["reason"] == "service_restart"
    assert saved["recovery"]["status"] == "scheduled"
    assert saved["recovery"]["project_id"] == 9
    assert saved["product_progress"]["current_index"] == 7
    assert "服务重启导致运行线程中断" in saved["logs"][-1]["message"]


def test_mark_startup_interrupted_project_for_recovery_reschedules_already_scheduled(monkeypatch):
    progress = svc._initial_progress(message="queued")
    progress["recovery"] = {
        "reason": "service_restart",
        "status": "scheduled",
        "project_id": 9,
    }
    row = {
        "id": 9,
        "project_name": "AI素材军师 demo",
        "status": "running",
        "user_id": 1,
        "provider_code": "openrouter",
        "model_id": "google/gemini-3.5-flash",
        "summary_json": "{}",
        "progress_json": json.dumps(progress, ensure_ascii=False),
        "share_token": None,
        "share_enabled_at": None,
        "error_message": "",
        "started_at": "2026-06-10 19:00:00",
        "finished_at": None,
        "created_at": "2026-06-10 19:00:00",
        "updated_at": "2026-06-10 19:04:00",
    }

    writes = []
    fake_lock = object()
    monkeypatch.setattr(svc, "_with_project_lock", lambda timeout_seconds=0: fake_lock)
    monkeypatch.setattr(svc, "_release_project_lock", lambda conn: None)
    monkeypatch.setattr(svc.db, "query_one", lambda *args, **kwargs: row)
    monkeypatch.setattr(svc.db, "query", lambda *args, **kwargs: [])

    def fake_execute(sql, params=None):
        writes.append((sql, params))
        return 1

    monkeypatch.setattr(svc.db, "execute", fake_execute)

    project = svc.mark_startup_interrupted_project_for_recovery()

    assert project["id"] == 9
    assert writes
    saved = json.loads(writes[0][1][0])
    assert saved["runner_state"] == "resume_scheduled"
    assert saved["recovery"]["status"] == "scheduled"


def test_mark_startup_interrupted_project_for_recovery_reschedules_previous_running_recovery(monkeypatch):
    progress = svc._initial_progress(message="running")
    progress["recovery"] = {
        "reason": "service_restart",
        "status": "running",
        "project_id": 9,
    }
    row = {
        "id": 9,
        "project_name": "AI素材军师 demo",
        "status": "running",
        "user_id": 1,
        "provider_code": "openrouter",
        "model_id": "google/gemini-3.5-flash",
        "summary_json": "{}",
        "progress_json": json.dumps(progress, ensure_ascii=False),
        "share_token": None,
        "share_enabled_at": None,
        "error_message": "",
        "started_at": "2026-06-10 19:00:00",
        "finished_at": None,
        "created_at": "2026-06-10 19:00:00",
        "updated_at": "2026-06-10 19:04:00",
    }
    writes = []

    fake_lock = object()
    monkeypatch.setattr(svc, "_with_project_lock", lambda timeout_seconds=0: fake_lock)
    monkeypatch.setattr(svc, "_release_project_lock", lambda conn: None)
    monkeypatch.setattr(svc.db, "query_one", lambda *args, **kwargs: row)
    monkeypatch.setattr(svc.db, "query", lambda *args, **kwargs: [])

    def fake_execute(sql, params=None):
        writes.append((sql, params))
        return 1

    monkeypatch.setattr(svc.db, "execute", fake_execute)

    project = svc.mark_startup_interrupted_project_for_recovery()

    assert project["id"] == 9
    assert writes
    saved = json.loads(writes[0][1][0])
    assert saved["recovery"]["status"] == "scheduled"


def test_mark_startup_interrupted_project_for_recovery_skips_when_lock_busy(monkeypatch):
    monkeypatch.setattr(svc, "_with_project_lock", lambda timeout_seconds=0: None)
    monkeypatch.setattr(svc.db, "query_one", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no query")))

    assert svc.mark_startup_interrupted_project_for_recovery() is None


def test_mark_startup_interrupted_project_for_recovery_marks_other_running_interrupted(monkeypatch):
    progress = svc._initial_progress(message="queued")
    latest = {
        "id": 9,
        "project_name": "AI素材军师 latest",
        "status": "running",
        "user_id": 1,
        "provider_code": "openrouter",
        "model_id": "google/gemini-3.5-flash",
        "summary_json": "{}",
        "progress_json": json.dumps(progress, ensure_ascii=False),
        "share_token": None,
        "share_enabled_at": None,
        "error_message": "",
        "started_at": "2026-06-10 19:00:00",
        "finished_at": None,
        "created_at": "2026-06-10 19:00:00",
        "updated_at": "2026-06-10 19:04:00",
    }
    writes = []

    monkeypatch.setattr(svc, "_with_project_lock", lambda timeout_seconds=0: object())
    monkeypatch.setattr(svc, "_release_project_lock", lambda conn: None)
    monkeypatch.setattr(svc.db, "query_one", lambda *args, **kwargs: latest)
    monkeypatch.setattr(
        svc.db,
        "query",
        lambda *args, **kwargs: [{"id": 8, "progress_json": json.dumps(progress, ensure_ascii=False)}],
    )
    monkeypatch.setattr(svc.db, "execute", lambda sql, params=None: writes.append((sql, params)) or 1)

    project = svc.mark_startup_interrupted_project_for_recovery()

    assert project["id"] == 9
    assert len(writes) == 2
    assert "status = 'interrupted'" in writes[1][0]
    interrupted_progress = json.loads(writes[1][1][1])
    assert interrupted_progress["recovery"]["reason"] == "replaced_by_startup_recovery"


def test_prepare_project_for_run_marks_scheduled_recovery_running(monkeypatch):
    progress = svc._initial_progress(message="queued")
    progress["recovery"] = {
        "reason": "service_restart",
        "status": "scheduled",
        "project_id": 9,
    }
    row = {
        "id": 9,
        "status": "running",
        "progress_json": json.dumps(progress, ensure_ascii=False),
    }
    writes = []

    monkeypatch.setattr(svc, "_load_project_row", lambda project_id: row)
    monkeypatch.setattr(svc.db, "execute", lambda sql, params=None: writes.append((sql, params)))
    monkeypatch.setattr(svc.db, "query", lambda *args, **kwargs: [])

    prepared = svc._prepare_project_for_run(9)

    assert prepared == row
    saved = json.loads(writes[0][1][0])
    assert saved["runner_state"] == "running"
    assert saved["recovery"]["status"] == "running"
    assert saved["recovery"]["reason"] == "service_restart"
    assert saved["recovery"]["resumed_at"]


def test_mark_project_interrupted_writes_terminal_interrupted_status(monkeypatch):
    progress = svc._progress_update(
        svc._initial_progress(message="running"),
        step_key="product_analysis",
        step_status="running",
        percent=63,
        message="分析第 7/30 个产品",
    )
    row = {
        "id": 9,
        "status": "running",
        "progress_json": json.dumps(progress, ensure_ascii=False),
    }
    writes = []

    monkeypatch.setattr(svc, "_load_project_row", lambda project_id: row)
    monkeypatch.setattr(svc.db, "execute", lambda sql, params=None: writes.append((sql, params)) or 1)
    monkeypatch.setattr(svc, "get_project", lambda project_id: {"id": project_id, "status": "interrupted"})

    project = svc.mark_project_interrupted(
        9,
        reason="startup_resume_schedule_failed",
        message="自动恢复未能排队，已中断。",
    )

    assert project == {"id": 9, "status": "interrupted"}
    assert writes
    saved = json.loads(writes[0][1][1])
    assert "status = 'interrupted'" in writes[0][0]
    assert saved["status"] == "interrupted"
    assert saved["runner_state"] == "interrupted"
    assert saved["recovery"]["status"] == "interrupted"
    assert saved["steps"][4]["status"] == "interrupted"


def test_resume_project_from_step_clears_downstream_checkpoints(monkeypatch):
    progress = svc._initial_progress(message="done")
    row = {
        "id": 9,
        "status": "interrupted",
        "progress_json": json.dumps(progress, ensure_ascii=False),
    }
    writes = []
    deletes = []
    fake_lock = object()

    monkeypatch.setattr(svc, "_with_project_lock", lambda timeout_seconds=0: fake_lock)
    monkeypatch.setattr(svc, "_release_project_lock", lambda conn: None)
    monkeypatch.setattr(svc, "_load_project_row", lambda project_id: row)
    monkeypatch.setattr(svc.db, "query", lambda *args, **kwargs: [])

    def fake_execute(sql, params=None):
        if sql.strip().startswith("DELETE FROM ai_material_strategist_product_results"):
            deletes.append((sql, params))
        else:
            writes.append((sql, params))
        return 1

    monkeypatch.setattr(svc.db, "execute", fake_execute)
    monkeypatch.setattr(svc, "get_project", lambda project_id: {"id": project_id, "status": "running"})

    project = svc.resume_project_from_step(9, "ai_ranking", user_id=1)

    assert project == {"id": 9, "status": "running"}
    assert writes
    sql, params = writes[0]
    assert "status = 'running'" in sql
    assert "ranking_prompt_json = NULL" in sql
    assert "ranking_result_json = NULL" in sql
    assert "data_snapshot_json = NULL" not in sql
    assert deletes
    saved = json.loads(params[0])
    assert saved["current_step"] == "ai_ranking"
    assert saved["recovery"]["reason"] == "manual_step_resume"
    assert saved["recovery"]["user_id"] == 1


def test_resume_project_checkpoint_preserves_product_results(monkeypatch):
    progress = svc._initial_progress(message="failed")
    progress["current_step"] = "product_analysis"
    row = {
        "id": 9,
        "status": "interrupted",
        "progress_json": json.dumps(progress, ensure_ascii=False),
    }
    writes = []
    deletes = []

    monkeypatch.setattr(svc, "_with_project_lock", lambda timeout_seconds=0: object())
    monkeypatch.setattr(svc, "_release_project_lock", lambda conn: None)
    monkeypatch.setattr(svc, "_load_project_row", lambda project_id: row)
    monkeypatch.setattr(svc.db, "query", lambda *args, **kwargs: [])

    def fake_execute(sql, params=None):
        if "DELETE FROM ai_material_strategist_product_results" in sql:
            deletes.append((sql, params))
        else:
            writes.append((sql, params))
        return 1

    monkeypatch.setattr(svc.db, "execute", fake_execute)
    monkeypatch.setattr(svc, "get_project", lambda project_id: {"id": project_id, "status": "running"})

    project = svc.resume_project_checkpoint(9, user_id=1)

    assert project == {"id": 9, "status": "running"}
    assert writes
    assert not deletes
    sql, params = writes[0]
    assert "summary_json = NULL" in sql
    assert "data_snapshot_json = NULL" not in sql
    assert "ranking_result_json = NULL" not in sql
    saved = json.loads(params[0])
    assert saved["runner_state"] == "checkpoint_resume_scheduled"
    assert saved["recovery"]["reason"] == "manual_checkpoint_resume"
    assert saved["recovery"]["user_id"] == 1


def test_checkpoint_resume_state_marks_stale_running_as_resumable():
    progress = svc._initial_progress(message="running")
    progress["runner_state"] = "running"
    progress["runner_heartbeat_at"] = "2026-06-10 19:00:00"

    can_resume, reason = svc._checkpoint_resume_state(
        {"status": "running", "updated_at": "2026-06-10 19:00:00"},
        progress,
    )

    assert can_resume is True
    assert reason == "stale_heartbeat"


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


def test_select_products_fills_to_30_from_rule_candidates_when_ai_returns_short_list():
    candidates = [
        _row(
            product_id=index,
            product_code=f"product-{index}-rjc",
            spend_30d=1000 - index,
            orders_30d=80 - index,
        )
        for index in range(1, 36)
    ]

    selected = svc._select_products(candidates, {"selected_product_ids": [2, 4]})

    assert len(selected) == 30
    assert [item["product_id"] for item in selected[:4]] == [2, 4, 1, 3]
    assert selected[-1]["product_id"] == 30


def test_fallback_ranking_outputs_30_products_when_candidates_available():
    candidates = [
        _row(
            product_id=index,
            product_code=f"product-{index}-rjc",
            spend_30d=1000 - index,
            orders_30d=80 - index,
            profit_30d=100,
        )
        for index in range(1, 36)
    ]

    ranking = svc._fallback_ranking(candidates)

    assert ranking["selected_product_ids"] == list(range(1, 31))
    assert len(ranking["ranking_result"]["ranked_products"]) == 30


def test_run_ai_ranking_final_prompt_requests_top_30(monkeypatch):
    candidates = [
        _row(
            product_id=index,
            product_code=f"product-{index}-rjc",
            spend_30d=1000 - index,
            orders_30d=80 - index,
            profit_30d=100,
        )
        for index in range(1, 61)
    ]
    calls = []

    def fake_invoke_generate(*args, **kwargs):
        prompt = kwargs["prompt"]
        calls.append({"prompt": prompt, "billing_extra": kwargs["billing_extra"]})
        if kwargs["billing_extra"]["stage"] == "batch_rank":
            batch_index = kwargs["billing_extra"]["batch_index"]
            start = (batch_index - 1) * 20 + 1
            product_ids = range(start, start + 10)
        else:
            product_ids = range(1, 31)
        return {
            "json": {
                "ranked_products": [
                    {
                        "product_id": product_id,
                        "rank": rank,
                        "score": 100 - rank,
                        "why_selected": "有量且效率达标",
                    }
                    for rank, product_id in enumerate(product_ids, start=1)
                ]
            },
            "text": "",
            "usage_log_id": f"log-{len(calls)}",
        }

    monkeypatch.setattr(svc.llm_client, "invoke_generate", fake_invoke_generate)

    ranking = svc._run_ai_ranking(candidates, project_id=9, user_id=1, run_ai=True)

    assert len(ranking["selected_product_ids"]) == 30
    assert "最终 Top 30" in calls[-1]["prompt"]
    assert ranking["ranking_result"]["final_input"]["rule"].startswith("从所有批次候选里输出最终 Top 30")


def test_product_analysis_fills_missing_structured_fields_from_fallback(monkeypatch):
    product = _row(
        product_id=7,
        product_code="structured-fill-rjc",
        product_name="Structured Fill",
        spend_30d=900,
        orders_30d=80,
        revenue_30d=1800,
        purchase_value_30d=1700,
        profit_30d=300,
    )
    countries = [
        {
            "country_code": "DE",
            "lang": "de",
            "ad_spend_usd": 500,
            "ad_roas": 1.8,
        }
    ]

    def fake_invoke_generate(*args, **kwargs):
        return {
            "json": {
                "product_id": 7,
                "product_code": "structured-fill-rjc",
                "overall_judgement": "AI 给出了可读补素材建议，但漏掉结构化动作字段。",
            },
            "text": "",
            "usage_log_id": 123,
        }

    monkeypatch.setattr(svc.llm_client, "invoke_generate", fake_invoke_generate)

    result = svc._run_product_analysis(
        product,
        countries,
        [],
        [],
        project_id=9,
        user_id=33,
        run_ai=True,
    )

    assert result["mode"] == "ai"
    assert result["overall_judgement"] == "AI 给出了可读补素材建议，但漏掉结构化动作字段。"
    assert result["priority"] in {"P0", "P1", "P2", "P3"}
    assert result["primary_action"] in {
        "expand_country",
        "same_country_new_material",
        "weak_country_retest",
        "hold",
        "investigate",
    }
    assert result["next_check"]
    assert {"priority", "primary_action", "next_check"} <= set(result["fallback_filled_fields"])


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


def test_english_action_does_not_create_small_language_translation_task():
    product = _row(product_id=10, product_code="demo-rjc", user_id=7)
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
            "reason": "EN源语言继续补素材",
        }],
        "material_actions": [],
    }

    decorated = svc._decorate_ai_result_with_tasks(ai_result, countries, [])
    actions = svc._build_action_items(product, decorated, [], countries)

    assert not [item for item in actions if item["type"] == "create_translation_task"]


def test_ai_material_strategist_frontend_restores_split_lost_controls():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "ai_material_strategist.js").read_text(encoding="utf-8")
    template = (root / "web" / "templates" / "medias_ai_material_strategist.html").read_text(encoding="utf-8")

    assert "DEFAULT_COUNTRY_CODES = ['EN', 'DE', 'FR', 'IT', 'ES', 'JP', 'SE', 'NL', 'PT']" in script
    assert "function getSpendGreenLevelClass" in script
    assert "green-level-6" in script
    assert "function renderTaskCountLink" in script
    assert "function showTasksModal" in script
    assert "function showLlmModal" in script
    assert "data-show-project-llm" in script
    assert "data-show-product-llm" in script
    assert "function countryCodesForMatrix" in script
    assert "function renderRecommendationBadge" in script
    assert "aimsTaskModal" in template
    assert "aimsLlmModal" in template
    assert ".aims-country-cell.green-level-6" in template
    assert ".aims-task-count-btn" in template
    assert ".aims-rec-badge.expand_country" in template
    assert "ai_material_strategist.js', v=" in template


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
