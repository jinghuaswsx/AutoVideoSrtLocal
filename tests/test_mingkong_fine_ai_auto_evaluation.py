from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def mock_cooldown_inactive(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod
    monkeypatch.setattr(mod, "is_cooldown_active", lambda: False)


def _candidate(index: int, *, material_key: str | None = None, is_new_top100_entry: bool = False) -> dict:
    key = material_key or f"{index:064x}"
    return {
        "material_key": key,
        "product_code": f"product-{index}",
        "product_url": f"https://shop.example/products/product-{index}",
        "mk_product_id": 1000 + index,
        "mk_product_link": f"https://shop.example/products/product-{index}",
        "product_name": f"Product {index}",
        "video_name": f"video-{index}.mp4",
        "video_path": f"uploads2/video-{index}.mp4",
        "video_image_path": f"uploads2/video-{index}.jpg",
        "video_duration_seconds": 12.5,
        "cumulative_90_spend": 1000 - index,
        "yesterday_spend_delta": 100 + index,
        "display_position": index,
        "rank_position": index,
        "is_new_top100_entry": is_new_top100_entry,
    }


def _patch_run_logging(monkeypatch, mod, *, latest_running=None, start_id=101):
    finish_calls = []
    monkeypatch.setattr(mod.scheduled_tasks, "latest_running_run", lambda task_code: latest_running)
    monkeypatch.setattr(mod.scheduled_tasks, "start_run", lambda task_code: start_id)
    monkeypatch.setattr(
        mod.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: finish_calls.append({"run_id": run_id, **kwargs}),
    )
    from appcore import fine_ai_evaluation_model_config as config_mod
    monkeypatch.setattr(config_mod, "get_parallel_mode", lambda: "parallel")
    monkeypatch.setattr(config_mod, "get_profile_config", lambda profile: {"provider": "openrouter"})
    return finish_calls


def test_tick_prioritizes_top500_before_yesterday_top100(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod

    finish_calls = _patch_run_logging(monkeypatch, mod)
    processed = []
    monkeypatch.setattr(mod, "_fetch_top500_candidates", lambda limit: [_candidate(1)])

    def fail_top100(limit):
        raise AssertionError("top100 should not be queried while top500 has runnable candidates")

    monkeypatch.setattr(mod, "_fetch_yesterday_top100_candidates", fail_top100)
    monkeypatch.setattr(
        mod,
        "_run_candidate",
        lambda row, **kwargs: processed.append({"row": row, **kwargs}) or {"status": "completed"},
    )

    summary = mod.tick_once(limit=10)

    assert summary["processed"] == 1
    assert processed[0]["source_bucket"] == "top500_90d_spend"
    assert processed[0]["source_rank"] == 1
    assert finish_calls[-1]["status"] == "success"


def test_tick_uses_yesterday_top100_after_top500_exhausted(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod

    _patch_run_logging(monkeypatch, mod)
    processed = []
    monkeypatch.setattr(mod, "_fetch_top500_candidates", lambda limit: [])
    monkeypatch.setattr(
        mod,
        "_fetch_yesterday_top100_candidates",
        lambda limit: [
            _candidate(1, is_new_top100_entry=False),
            _candidate(2, is_new_top100_entry=True),
        ],
    )
    monkeypatch.setattr(
        mod,
        "_run_candidate",
        lambda row, **kwargs: processed.append({"row": row, **kwargs}) or {"status": "completed"},
    )

    summary = mod.tick_once(limit=10)

    assert summary["processed"] == 1
    assert [item["source_bucket"] for item in processed] == ["yesterday_top100"]
    assert [item["row"]["is_new_top100_entry"] for item in processed] == [False]


def test_tick_limits_each_round_to_one(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod

    _patch_run_logging(monkeypatch, mod)
    processed = []
    monkeypatch.setattr(mod, "_fetch_top500_candidates", lambda limit: [_candidate(i) for i in range(1, 13)])
    monkeypatch.setattr(mod, "_fetch_yesterday_top100_candidates", lambda limit: [])
    monkeypatch.setattr(
        mod,
        "_run_candidate",
        lambda row, **kwargs: processed.append(row["material_key"]) or {"status": "completed"},
    )

    summary = mod.tick_once(limit=10)

    assert summary["limit"] == 1
    assert summary["processed"] == 1
    assert len(processed) == 1


def test_worker_pool_refills_finished_slot_while_other_task_is_running(monkeypatch):
    import threading
    import time

    from appcore import mingkong_fine_ai_auto_evaluation as mod

    rows = iter([_candidate(1), _candidate(2), _candidate(3)])

    def fake_claim_batch(limit):
        claimed = []
        for _ in range(limit):
            try:
                row = next(rows)
            except StopIteration:
                break
            claimed.append({
                "row": row,
                "material_key": row["material_key"],
                "source_bucket": mod.SOURCE_TOP500,
                "source_rank": row["rank_position"],
            })
        return {
            "source_bucket": mod.SOURCE_TOP500 if claimed else "",
            "scanned": len(claimed),
            "claimed": claimed,
            "skipped": [],
        }

    lock = threading.Lock()
    active = 0
    max_active = 0
    events = []

    def fake_run_candidate(row, **kwargs):
        nonlocal active, max_active
        code = row["product_code"]
        with lock:
            active += 1
            max_active = max(max_active, active)
            events.append(("start", code))
        try:
            time.sleep(0.02 if code == "product-1" else 0.08)
            return {"status": "completed"}
        finally:
            with lock:
                events.append(("finish", code))
                active -= 1

    monkeypatch.setattr(mod, "_claim_candidate_batch", fake_claim_batch)
    monkeypatch.setattr(mod, "_run_candidate", fake_run_candidate)

    summary = mod.run_worker_pool(
        max_workers=2,
        idle_sleep_seconds=0.005,
        max_processed=3,
        sleeper=lambda seconds: None,
    )

    assert summary["processed"] == 3
    assert summary["completed"] == 3
    assert max_active == 2
    assert events.index(("start", "product-3")) < events.index(("finish", "product-2"))


def test_default_worker_concurrency_is_one():
    from appcore import mingkong_fine_ai_auto_evaluation as mod

    assert mod.DEFAULT_WORKER_CONCURRENCY == 1


def test_run_candidate_reuses_manual_link_check_contract(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod

    row = _candidate(1)
    writes = []
    created = {}
    link_check = {
        "ok": True,
        "status": "replaced",
        "original_link": row["mk_product_link"],
        "selected_link": "https://shop.example/products/replacement",
        "message": "replacement selected",
        "candidates": [],
    }

    class FakeService:
        def create_external_link_run(self, **kwargs):
            created.update(kwargs)
            return {"evaluation_run_id": "eval_auto", "status": "queued"}

        def run_evaluation(self, evaluation_run_id):
            return {"status": "completed", "evaluation_run_id": evaluation_run_id}

    monkeypatch.setattr(mod, "execute", lambda sql, args=(): writes.append((sql, args)) or 1)
    monkeypatch.setattr(mod, "resolve_billing_user_id", lambda explicit_user_id=None: 1)
    monkeypatch.setattr(mod, "_cache_card_video", lambda video_path: "mk/videos/video-1.mp4")
    monkeypatch.setattr(mod, "_resolve_product_link", lambda product_link, **kwargs: link_check)

    result = mod._run_candidate(
        row,
        scheduled_run_id=101,
        source_bucket=mod.SOURCE_TOP500,
        source_rank=1,
        service=FakeService(),
    )

    assert result["status"] == "completed"
    assert created["product_link"] == "https://shop.example/products/replacement"
    assert created["link_check_result"] == link_check
    assert created["model_profile"] == "scheduled"
    assert created["card_video_path"] == row["video_path"]
    assert len(writes) == 2


def test_run_candidate_fails_before_llm_when_product_link_unavailable(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod

    row = _candidate(1)
    writes = []

    class ExplodingService:
        def create_external_link_run(self, **kwargs):
            raise AssertionError("LLM run should not be created when product link is unavailable")

    monkeypatch.setattr(mod, "execute", lambda sql, args=(): writes.append((sql, args)) or 1)
    monkeypatch.setattr(mod, "resolve_billing_user_id", lambda explicit_user_id=None: 1)
    monkeypatch.setattr(
        mod,
        "_resolve_product_link",
        lambda product_link, **kwargs: {
            "ok": False,
            "status": "failed",
            "original_link": product_link,
            "selected_link": "",
            "message": "商品链接和明空候选链接均不可访问",
        },
    )
    monkeypatch.setattr(
        mod,
        "_cache_card_video",
        lambda video_path: (_ for _ in ()).throw(AssertionError("video should not be cached")),
    )

    result = mod._run_candidate(
        row,
        scheduled_run_id=101,
        source_bucket=mod.SOURCE_TOP500,
        source_rank=1,
        service=ExplodingService(),
    )

    assert result["status"] == "failed"
    assert result["reason"] == "product_link_unavailable"
    assert len(writes) == 2


def test_run_candidate_skips_when_material_already_claimed(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod

    row = _candidate(1)
    monkeypatch.setattr(mod, "_claim_running_record", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        mod,
        "_resolve_product_link",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("claimed cards must not create Fine AI runs")),
    )

    result = mod._run_candidate(
        row,
        scheduled_run_id=101,
        source_bucket=mod.SOURCE_TOP500,
        source_rank=1,
        service=object(),
    )

    assert result == {"status": "skipped", "reason": "already_claimed"}


def test_cache_card_video_forces_local_mingkong_download(monkeypatch):
    from appcore import local_media_storage
    from appcore import mingkong_fine_ai_auto_evaluation as mod
    from web.routes import medias as media_routes
    from web.routes.medias import mk_selection

    class MissingLocalPath:
        def is_file(self):
            return False

    captured = {}
    monkeypatch.setattr(media_routes, "_normalize_mk_media_path", lambda value: "uploads2/video-1.mp4")
    monkeypatch.setattr(
        media_routes,
        "_cache_mk_video",
        lambda media_path: (_ for _ in ()).throw(
            AssertionError("auto task must bypass remote-only cache shortcut")
        ),
    )
    monkeypatch.setattr(local_media_storage, "safe_local_path_for", lambda object_key: MissingLocalPath())
    monkeypatch.setattr(mk_selection, "_mk_video_cache_object_key", lambda media_path: "mk/videos/video-1.mp4")
    monkeypatch.setattr(mk_selection, "_build_mk_request_headers", lambda: {"Authorization": "Bearer token"})
    monkeypatch.setattr(mk_selection, "_get_mk_api_base_url", lambda: "https://mk.example")
    monkeypatch.setattr(mk_selection, "_mk_http_get", object())

    def fake_cache_impl(media_path, **kwargs):
        object_key = kwargs["cache_object_key_fn"](media_path)
        captured["media_path"] = media_path
        captured["object_key"] = object_key
        captured["storage_exists"] = kwargs["storage_exists_fn"](object_key)
        captured["safe_local_path_for_fn"] = kwargs["safe_local_path_for_fn"]
        captured["http_get_fn"] = kwargs["http_get_fn"]
        return object_key

    monkeypatch.setattr(mk_selection, "_cache_mk_video_impl", fake_cache_impl)

    object_key = mod._cache_card_video("uploads2/video-1.mp4")

    assert object_key == "mk/videos/video-1.mp4"
    assert captured["media_path"] == "uploads2/video-1.mp4"
    assert captured["storage_exists"] is False
    assert captured["safe_local_path_for_fn"] is local_media_storage.safe_local_path_for
    assert captured["http_get_fn"] is mk_selection._mk_http_get


def test_tick_skips_when_existing_run_younger_than_30_minutes(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod

    now = datetime(2026, 5, 23, 9, 0, 0)
    running = {"id": 42, "started_at": now - timedelta(seconds=60)}
    monkeypatch.setattr(mod, "_now", lambda: now)
    monkeypatch.setattr(mod.scheduled_tasks, "latest_running_run", lambda task_code: running)
    monkeypatch.setattr(
        mod.scheduled_tasks,
        "start_run",
        lambda task_code: (_ for _ in ()).throw(AssertionError("new run should not start")),
    )

    summary = mod.tick_once(limit=10)

    assert summary["skipped"] is True
    assert summary["reason"] == "previous_run_still_running"
    assert summary["running_run_id"] == 42
    assert summary["running_age_seconds"] == 60


def test_tick_replaces_running_run_older_than_30_minutes(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod

    now = datetime(2026, 5, 23, 9, 0, 0)
    running = {"id": 42, "started_at": now - timedelta(seconds=1900)}
    monkeypatch.setattr(mod, "_now", lambda: now)
    finish_calls = _patch_run_logging(monkeypatch, mod, latest_running=running, start_id=101)
    write_calls = []
    monkeypatch.setattr(mod, "execute", lambda sql, args=(): write_calls.append((sql, args)) or 1)
    monkeypatch.setattr(mod, "_fetch_top500_candidates", lambda limit: [])
    monkeypatch.setattr(mod, "_fetch_yesterday_top100_candidates", lambda limit: [])

    summary = mod.tick_once(limit=10)

    assert summary["stale_run_replaced"] == 42
    assert summary["running_age_seconds"] == 1900
    assert finish_calls[0]["run_id"] == 42
    assert finish_calls[0]["status"] == "failed"
    assert "exceeded" in finish_calls[0]["error_message"]
    assert "UPDATE mingkong_fine_ai_auto_evaluations" in write_calls[0][0]
    assert write_calls[0][1][1] == 42


def test_fetch_candidates_exclude_any_existing_auto_record(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod

    captured = []
    monkeypatch.setattr(mod, "query", lambda sql, args=(): captured.append(sql) or [])

    assert mod._fetch_top500_candidates(10) == []
    assert mod._fetch_yesterday_top100_candidates(10) == []

    joined_sql = "\n".join(captured)
    assert "LEFT JOIN mingkong_fine_ai_auto_evaluations a" in joined_sql
    assert "a.status IN" not in joined_sql
    assert "LIMIT 500" in joined_sql
    assert "LIMIT 100" in joined_sql


def test_enrich_cards_reads_external_fine_ai_result_for_unimported_material(monkeypatch):
    from appcore import mingkong_materials as mod

    item = _candidate(1)
    item["mk_product_link"] = "https://shop.example/products/product-1"
    item["product_url"] = "https://fallback.example/products/product-1"
    monkeypatch.setattr(mod, "_status_cache_by_hash", lambda scope, lookup_hashes: {})
    monkeypatch.setattr(mod, "_legacy_material_rows_by_product", lambda product_ids: {})

    def fake_query(sql, args=()):
        if "FROM ai_evaluation_runs" in sql:
            return [
                {
                    "id": 9,
                    "evaluation_run_id": "eval_auto",
                    "product_id": 0,
                    "status": "completed",
                    "summary_json": "{}",
                    "frontend_json": "{}",
                    "progress_json": "{}",
                    "metadata_json": json.dumps(
                        {
                            "source_type": "external_product_link",
                            "external_product_link": "https://shop.example/products/product-1",
                            "external_card_video": {"path": "uploads2/video-1.mp4", "name": "video-1.mp4"},
                        }
                    ),
                    "created_at": "2026-05-23 09:00:00",
                    "updated_at": "2026-05-23 09:01:00",
                    "completed_at": "2026-05-23 09:01:00",
                    "failed_at": None,
                }
            ]
        if "FROM ai_country_evaluations" in sql:
            return [
                {
                    "evaluation_run_id": "eval_auto",
                    "country_code": "DE",
                    "country_name": "Germany",
                    "status": "completed",
                    "scores_json": '{"overall_score": 81}',
                    "decision_json": '{"final_decision": "GO"}',
                    "full_result_json": json.dumps(
                        {
                            "country_code": "DE",
                            "country_name": "Germany",
                            "country_name_zh": "德国",
                            "status": "completed",
                            "scores": {"overall_score": 81},
                            "decision": {"final_decision": "GO"},
                        },
                        ensure_ascii=False,
                    ),
                    "error_message": "",
                }
            ]
        return []

    monkeypatch.setattr(mod, "query", fake_query)

    enriched = mod._enrich_cached_ad_statuses([item])

    fine_ai = enriched[0]["product_ad_status"]["fine_ai_evaluation"]
    assert fine_ai["evaluation_run_id"] == "eval_auto"
    assert fine_ai["has_result"] is True
    assert fine_ai["countries"]["DE"]["decision"]["final_decision"] == "GO"


def test_enrich_cards_matches_external_fine_ai_result_by_video_card_when_link_drifted(monkeypatch):
    from appcore import mingkong_materials as mod

    item = _candidate(1)
    item["mk_product_link"] = "https://shop.example/products/current-link"
    item["product_url"] = "https://fallback.example/products/current-link"
    monkeypatch.setattr(mod, "_status_cache_by_hash", lambda scope, lookup_hashes: {})
    monkeypatch.setattr(mod, "_legacy_material_rows_by_product", lambda product_ids: {})

    def fake_query(sql, args=()):
        if "FROM ai_evaluation_runs" in sql:
            return [
                {
                    "id": 9,
                    "evaluation_run_id": "eval_same_video",
                    "product_id": 0,
                    "status": "completed",
                    "summary_json": "{}",
                    "frontend_json": "{}",
                    "progress_json": "{}",
                    "metadata_json": json.dumps(
                        {
                            "source_type": "external_product_link",
                            "external_product_link": "https://shop.example/products/archived-link",
                            "external_card_video": {"path": "uploads2/video-1.mp4", "name": "video-1.mp4"},
                        }
                    ),
                    "created_at": "2026-05-23 09:00:00",
                    "updated_at": "2026-05-23 09:01:00",
                    "completed_at": "2026-05-23 09:01:00",
                    "failed_at": None,
                }
            ]
        if "FROM ai_country_evaluations" in sql:
            return [
                {
                    "evaluation_run_id": "eval_same_video",
                    "country_code": "DE",
                    "country_name": "Germany",
                    "status": "completed",
                    "scores_json": '{"overall_score": 81}',
                    "decision_json": '{"final_decision": "GO"}',
                    "full_result_json": json.dumps(
                        {
                            "country_code": "DE",
                            "country_name": "Germany",
                            "country_name_zh": "德国",
                            "status": "completed",
                            "scores": {"overall_score": 81},
                            "decision": {"final_decision": "GO"},
                        },
                        ensure_ascii=False,
                    ),
                    "error_message": "",
                }
            ]
        return []

    monkeypatch.setattr(mod, "query", fake_query)

    enriched = mod._enrich_cached_ad_statuses([item])

    fine_ai = enriched[0]["product_ad_status"]["fine_ai_evaluation"]
    assert fine_ai["evaluation_run_id"] == "eval_same_video"
    assert fine_ai["has_result"] is True


def test_enrich_cards_prefers_auto_material_key_result(monkeypatch):
    from appcore import mingkong_materials as mod

    item = _candidate(1)
    monkeypatch.setattr(mod, "_status_cache_by_hash", lambda scope, lookup_hashes: {})
    monkeypatch.setattr(mod, "_legacy_material_rows_by_product", lambda product_ids: {})

    def fake_query(sql, args=()):
        if "FROM mingkong_fine_ai_auto_evaluations" in sql:
            return [{"material_key": item["material_key"], "evaluation_run_id": "eval_auto"}]
        if "FROM ai_evaluation_runs" in sql:
            return [
                {
                    "id": 9,
                    "evaluation_run_id": "eval_auto",
                    "product_id": 0,
                    "status": "completed",
                    "summary_json": "{}",
                    "frontend_json": "{}",
                    "progress_json": "{}",
                    "metadata_json": json.dumps(
                        {
                            "source_type": "external_product_link",
                            "external_product_link": "https://other.example/products/other",
                            "external_card_video": {"path": "uploads2/other.mp4"},
                        }
                    ),
                    "created_at": "2026-05-23 09:00:00",
                    "updated_at": "2026-05-23 09:01:00",
                    "completed_at": "2026-05-23 09:01:00",
                    "failed_at": None,
                }
            ]
        if "FROM ai_country_evaluations" in sql:
            return [
                {
                    "evaluation_run_id": "eval_auto",
                    "country_code": "FR",
                    "country_name": "France",
                    "status": "completed",
                    "scores_json": '{"overall_score": 76}',
                    "decision_json": '{"final_decision": "WATCH"}',
                    "full_result_json": json.dumps(
                        {
                            "country_code": "FR",
                            "country_name": "France",
                            "country_name_zh": "法国",
                            "status": "completed",
                            "scores": {"overall_score": 76},
                            "decision": {"final_decision": "WATCH"},
                        },
                        ensure_ascii=False,
                    ),
                    "error_message": "",
                }
            ]
        return []

    monkeypatch.setattr(mod, "query", fake_query)

    enriched = mod._enrich_cached_ad_statuses([item])

    fine_ai = enriched[0]["product_ad_status"]["fine_ai_evaluation"]
    assert fine_ai["evaluation_run_id"] == "eval_auto"
    assert fine_ai["countries"]["FR"]["decision"]["final_decision"] == "WATCH"


def test_tick_once_safe_limit_fallback_serial(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod
    _patch_run_logging(monkeypatch, mod)

    # Force serial mode
    from appcore import fine_ai_evaluation_model_config as config_mod
    monkeypatch.setattr(config_mod, "get_parallel_mode", lambda: "serial")

    monkeypatch.setattr(mod, "_fetch_top500_candidates", lambda limit: [_candidate(1), _candidate(2)])
    monkeypatch.setattr(mod, "_fetch_yesterday_top100_candidates", lambda limit: [])
    processed = []
    monkeypatch.setattr(
        mod,
        "_run_candidate",
        lambda row, **kwargs: processed.append(row["material_key"]) or {"status": "completed"},
    )

    summary = mod.tick_once(limit=10)
    assert summary["limit"] == 1
    assert summary["processed"] == 1
    assert len(processed) == 1


def test_tick_once_skips_when_cooldown_active(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod
    _patch_run_logging(monkeypatch, mod)
    monkeypatch.setattr(mod, "is_cooldown_active", lambda: True)

    summary = mod.tick_once(limit=10)
    assert summary["skipped"] is True
    assert summary["reason"] == "cooldown_active"


def test_run_worker_pool_pauses_when_cooldown_active(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod
    monkeypatch.setattr(mod, "is_cooldown_active", lambda: True)

    sleeps = []
    def fake_sleep(stop_event, seconds, sleeper):
        sleeps.append(seconds)
        stop_event.set()

    monkeypatch.setattr(mod, "_sleep_or_stop", fake_sleep)

    import threading
    stop_event = threading.Event()

    summary = mod.run_worker_pool(
        max_workers=2,
        stop_event=stop_event,
        sleeper=lambda s: None
    )

    assert len(sleeps) == 1
    assert sleeps[0] == 30.0
    assert summary["processed"] == 0


def test_run_candidate_fails_when_cooldown_active(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation as mod
    monkeypatch.setattr(mod, "is_cooldown_active", lambda: True)

    finished = []
    monkeypatch.setattr(mod, "_finish_record", lambda key, status, error: finished.append((key, status, error)))

    row = _candidate(1)
    result = mod._run_candidate(
        row,
        scheduled_run_id=None,
        source_bucket="top1000",
        source_rank=1,
        already_claimed=True
    )

    assert result == {"status": "failed", "reason": "cooldown_active"}
    assert len(finished) == 1
    assert finished[0] == (row["material_key"], "failed", "cooldown_active")

