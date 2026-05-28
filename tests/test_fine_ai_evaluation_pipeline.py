import pytest


@pytest.fixture(autouse=True)
def _stub_fine_ai_model_settings(monkeypatch):
    from appcore import fine_ai_evaluation_model_config as model_config

    monkeypatch.setattr(model_config.settings_store, "get_setting", lambda key: None)


def test_pipeline_extracts_product_facts_once_and_evaluates_five_countries_serially():
    from appcore.fine_ai_evaluation_country_config import DEFAULT_COUNTRY_CODES
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    calls = []
    repository = InMemoryEvaluationRepository()
    client = FakeGeminiClient(calls)
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=client,
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    run = service.create_run(123, countries=list(DEFAULT_COUNTRY_CODES))
    result = service.run_evaluation(run["evaluation_run_id"])

    assert result["status"] == "completed"
    assert [call[0] for call in calls] == [
        "product_facts",
        "country:DE",
        "country:FR",
        "country:IT",
        "country:ES",
        "country:JP",
    ]
    assert "product_catalog" not in repr(repository.rows)
    assert "```" not in repr(result)
    assert set(result["countries"]) == {"DE", "FR", "IT", "ES", "JP"}


def test_pipeline_persists_visual_progress_steps_and_execution_log():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    service = FineAiEvaluationService(
        repository=InMemoryEvaluationRepository(),
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    run = service.create_run(123)
    result = service.run_evaluation(run["evaluation_run_id"])
    progress = result["progress"]

    step_keys = [step["key"] for step in progress["steps"]]
    assert step_keys == [
        "data_preparation",
        "product_fact_extraction",
        "country_DE",
        "country_FR",
        "country_IT",
        "country_ES",
        "country_JP",
        "summary",
    ]
    assert progress["total_steps"] == 8
    assert progress["completed_steps"] == 8
    assert progress["current_step"] == "summary"
    assert progress["elapsed_seconds"] >= 0
    assert progress["countries"] == {
        "DE": "completed",
        "FR": "completed",
        "IT": "completed",
        "ES": "completed",
        "JP": "completed",
    }

    product_step = next(step for step in progress["steps"] if step["key"] == "product_fact_extraction")
    de_step = next(step for step in progress["steps"] if step["key"] == "country_DE")
    assert product_step["status"] == "completed"
    assert de_step["status"] == "completed"
    assert any(item["label"] == "Provider" for item in product_step["debug"])
    assert any(item["label"] == "Country" and item["value"] == "DE" for item in de_step["debug"])
    assert any(event["step_key"] == "country_DE" for event in progress["events"])
    assert any(log["level"] == "info" for log in de_step["logs"])


def test_pipeline_persists_llm_trace_on_llm_progress_steps_only():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    service = FineAiEvaluationService(
        repository=InMemoryEvaluationRepository(),
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    run = service.create_run(123, countries=["DE"])
    result = service.run_evaluation(run["evaluation_run_id"])
    steps = {step["key"]: step for step in result["progress"]["steps"]}

    product_step = steps["product_fact_extraction"]
    country_step = steps["country_DE"]
    assert product_step["provider"] == "fake-provider"
    assert product_step["model_id"] == "fake-model"
    assert product_step["llm_trace"]["request"]["prompt"] == "full prompt for product_facts"
    assert product_step["llm_trace"]["response"]["parsed_json"]["product_id"] == "123"
    assert country_step["provider"] == "fake-provider"
    assert country_step["model_id"] == "fake-model"
    assert country_step["llm_trace"]["request"]["prompt"] == "full prompt for country:DE"
    assert country_step["llm_trace"]["response"]["parsed_json"]["country_code"] == "DE"
    assert "llm_trace" not in steps["data_preparation"]
    assert "llm_trace" not in steps["summary"]


def test_status_refreshes_progress_elapsed_seconds():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    run = service.create_run(123)
    stored = repository.rows[run["evaluation_run_id"]]
    stored["progress"]["started_at"] = "2026-01-01T00:00:00Z"
    stored["progress"]["elapsed_seconds"] = 0

    status = service.get_status(123, run["evaluation_run_id"])

    assert status["progress"]["elapsed_seconds"] > 0


def test_status_includes_context_snapshot_for_detail_header():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=ExplodingProductSnapshotService(),
        asset_snapshot_service=ExplodingAssetSnapshotService(),
        external_card_video_snapshot_service=FakeExternalCardVideoSnapshotService(),
    )

    run = service.create_external_link_run(
        product_link="https://example.test/products/new-idea",
        product_name="New Idea",
        product_code="new-idea",
        card_video_object_key="mk/videos/selected-card.mp4",
        card_video_path="uploads2/selected-card.mp4",
        card_video_url="/xuanpin/api/mk-video?path=uploads2%2Fselected-card.mp4",
        card_video_name="selected-card.mp4",
        card_video_duration_seconds=18.5,
    )

    status = service.get_status(0, run["evaluation_run_id"])

    assert status["product_snapshot"]["product_code"] == "new-idea"
    assert status["product_snapshot"]["product_url"] == "https://example.test/products/new-idea"
    assert status["metadata"]["external_card_video"]["name"] == "selected-card.mp4"


def test_latest_external_link_result_uses_product_link_and_current_card_video_archive():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=ExplodingProductSnapshotService(),
        asset_snapshot_service=ExplodingAssetSnapshotService(),
        external_card_video_snapshot_service=FakeExternalCardVideoSnapshotService(),
    )

    other = service.create_external_link_run(
        product_link="https://example.test/products/new-idea",
        product_name="New Idea",
        card_video_object_key="mk/videos/other-card.mp4",
        card_video_path="uploads2/other-card.mp4",
        card_video_name="other-card.mp4",
    )
    wanted = service.create_external_link_run(
        product_link="https://example.test/products/new-idea",
        product_name="New Idea",
        card_video_object_key="mk/videos/selected-card.mp4",
        card_video_path="uploads2/selected-card.mp4",
        card_video_name="selected-card.mp4",
    )
    repository.rows[other["evaluation_run_id"]]["status"] = "completed"
    repository.rows[wanted["evaluation_run_id"]]["status"] = "completed"

    latest = service.get_latest_external_link_result(
        "https://example.test/products/new-idea",
        card_video_path="uploads2/selected-card.mp4",
    )

    assert latest["evaluation_run_id"] == wanted["evaluation_run_id"]
    assert latest["metadata"]["external_card_video"]["path"] == "uploads2/selected-card.mp4"


def test_initial_progress_points_to_next_pending_step_after_data_preparation():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    run = service.create_run(123)
    stored = repository.get_run(run["evaluation_run_id"])
    progress = stored["progress"]
    data_step = next(step for step in progress["steps"] if step["key"] == "data_preparation")

    assert data_step["status"] == "completed"
    assert progress["completed_steps"] == 1
    assert progress["current_step"] == "product_fact_extraction"


def test_repository_normalizes_iso_z_timestamps_for_mysql_datetime_columns(monkeypatch):
    from appcore import fine_ai_evaluation_repository as repo_mod

    captured = {}

    def fake_execute(sql, args=()):
        captured["args"] = args
        return 1

    monkeypatch.setattr(repo_mod, "execute", fake_execute)
    monkeypatch.setattr(repo_mod, "query_one", lambda sql, args=(): None)

    repo_mod.FineAiEvaluationRepository().update_run(
        "eval_test",
        started_at="2026-05-22T07:46:12Z",
        failed_at="2026-05-22T07:46:13Z",
        completed_at="2026-05-22T07:46:14Z",
    )

    timestamp_args = captured["args"][:3]
    assert [str(value) for value in timestamp_args] == [
        "2026-05-22 07:46:12",
        "2026-05-22 07:46:13",
        "2026-05-22 07:46:14",
    ]


def test_repository_latest_external_link_falls_back_to_card_video_when_link_drifted(monkeypatch):
    from appcore import fine_ai_evaluation_repository as repo_mod

    calls = []

    def fake_query_one(sql, args=()):
        calls.append((sql, args))
        if len(calls) == 1:
            return None
        return {
            "evaluation_run_id": "eval_same_video",
            "product_id": 0,
            "status": "completed",
            "countries_json": "[]",
            "product_snapshot_json": "{}",
            "product_facts_json": "{}",
            "summary_json": "{}",
            "frontend_json": "{}",
            "metadata_json": '{"source_type":"external_product_link","external_product_link":"https://shop.example/products/archived","external_card_video":{"path":"uploads2/video-1.mp4"}}',
            "progress_json": "{}",
        }

    monkeypatch.setattr(repo_mod, "query_one", fake_query_one)

    result = repo_mod.FineAiEvaluationRepository().get_latest_external_link_run(
        "https://shop.example/products/current",
        card_video_path="uploads2/video-1.mp4",
        card_video_url="/xuanpin/api/mk-video?path=uploads2%2Fvideo-1.mp4",
        card_video_name="current-title.mp4",
    )

    assert result["evaluation_run_id"] == "eval_same_video"
    assert len(calls) == 2
    assert "$.external_product_link" in calls[0][0]
    assert "$.external_product_link" not in calls[1][0]
    assert "$.external_card_video.path" in calls[1][0]
    assert "$.card_video_path" in calls[1][0]
    assert "$.video_path" in calls[1][0]
    assert calls[1][1] == ("uploads2/video-1.mp4", "uploads2/video-1.mp4", "uploads2/video-1.mp4")


def test_repository_lists_inflight_runs_without_sorting_large_table(monkeypatch):
    from appcore import fine_ai_evaluation_repository as repo_mod

    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(repo_mod, "query", fake_query)

    rows = repo_mod.FineAiEvaluationRepository().list_inflight_runs()

    assert rows == []
    assert "status IN ('queued', 'running')" in captured["sql"]
    assert "ORDER BY" not in captured["sql"].upper()
    assert captured["args"] == ()


def test_external_link_zero_product_id_status_is_not_treated_as_missing(monkeypatch):
    from appcore import active_tasks
    from appcore import fine_ai_evaluation_repository as repo_mod
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    active_tasks.clear_active_tasks_for_tests()
    monkeypatch.setattr(
        repo_mod,
        "query_one",
        lambda sql, args=(): {
            "evaluation_run_id": "eval_external",
            "product_id": 0,
            "status": "running",
            "countries_json": '["DE"]',
            "progress_json": '{"current_step": "product_fact_extraction"}',
            "metadata_json": '{"source_type": "external_product_link"}',
        },
    )

    service = FineAiEvaluationService(
        repository=repo_mod.FineAiEvaluationRepository(),
        gemini_client=FakeGeminiClient([]),
    )

    active_tasks.register("fine_ai_evaluation", "eval_external")
    try:
        status = service.get_status(0, "eval_external")
    finally:
        active_tasks.unregister("fine_ai_evaluation", "eval_external")

    assert status["product_id"] == "0"
    assert status["status"] == "running"


def test_recover_interrupted_run_marks_orphan_running_country_failed_and_terminal():
    from appcore import active_tasks
    from appcore.fine_ai_evaluation_service import (
        FineAiEvaluationService,
        _country_step_key,
        _mark_progress_step,
        _progress,
    )

    active_tasks.clear_active_tasks_for_tests()
    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    run = service.create_run(123, countries=["DE", "FR"])
    evaluation_run_id = run["evaluation_run_id"]
    repository.upsert_country(
        evaluation_run_id,
        "DE",
        {
            "product_id": "123",
            "status": "completed",
            "full_result": make_country_result("DE"),
        },
    )
    progress = repository.rows[evaluation_run_id]["progress"]
    progress = _mark_progress_step(progress, "product_fact_extraction", "completed", "facts done")
    progress = _mark_progress_step(progress, _country_step_key("DE"), "completed", "DE done")
    progress = _mark_progress_step(progress, _country_step_key("FR"), "running", "FR running")
    progress = _progress(
        ["DE", "FR"],
        "country_evaluation_FR",
        running_country="FR",
        completed_countries=["DE"],
        base_progress=progress,
    )
    repository.update_run(
        evaluation_run_id,
        status="running",
        product_facts={"product_id": "123"},
        metadata={"countries_completed": ["DE"], "countries_failed": []},
        progress=progress,
        started_at="2026-05-23T08:00:00Z",
    )

    recovered = service.recover_run_if_interrupted(evaluation_run_id)

    assert recovered["status"] == "interrupted"
    assert recovered["metadata"]["countries_completed"] == ["DE"]
    assert recovered["metadata"]["countries_failed"] == ["FR"]
    assert recovered["progress"]["countries"]["FR"] == "failed"
    result = service.get_result(123, evaluation_run_id)
    assert result["status"] == "interrupted"
    assert result["countries"]["DE"]["status"] == "completed"
    assert result["countries"]["FR"]["status"] == "failed"
    fr_step = next(step for step in result["progress"]["steps"] if step["key"] == "country_FR")
    assert fr_step["status"] == "failed"
    assert "interrupted" in fr_step["message"].lower()


def test_recover_interrupted_run_leaves_active_run_running():
    from appcore import active_tasks
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    active_tasks.clear_active_tasks_for_tests()
    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    run = service.create_run(123, countries=["DE"])
    evaluation_run_id = run["evaluation_run_id"]
    repository.update_run(evaluation_run_id, status="running")
    active_tasks.register("fine_ai_evaluation", evaluation_run_id)
    try:
        recovered = service.recover_run_if_interrupted(evaluation_run_id)
    finally:
        active_tasks.unregister("fine_ai_evaluation", evaluation_run_id)

    assert recovered["status"] == "running"


def test_pipeline_continues_when_one_country_fails():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    calls = []
    service = FineAiEvaluationService(
        repository=InMemoryEvaluationRepository(),
        gemini_client=FakeGeminiClient(calls, fail_country="FR"),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    run = service.create_run(123)
    result = service.run_evaluation(run["evaluation_run_id"])

    assert result["status"] == "partially_completed"
    assert result["countries"]["FR"]["status"] == "failed"
    assert result["countries"]["DE"]["status"] == "completed"
    assert result["countries"]["IT"]["status"] == "completed"
    assert result["metadata"]["countries_failed"] == ["FR"]
    progress = result["progress"]
    fr_step = next(step for step in progress["steps"] if step["key"] == "country_FR")
    assert fr_step["status"] == "failed"
    assert any(log["level"] == "error" for log in fr_step["logs"])
    assert progress["countries"]["JP"] == "completed"


def test_country_failure_retries_once_then_continues():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    class FlakyCountryGeminiClient(FakeGeminiClient):
        def __init__(self, calls, fail_once_country):
            super().__init__(calls)
            self.fail_once_country = fail_once_country
            self.failed_once = False

        def generate_country_evaluation(self, *, product_snapshot, product_facts, country, asset_snapshot, asset_paths):
            code = country["country_code"]
            if code == self.fail_once_country and not self.failed_once:
                self.failed_once = True
                self.calls.append((f"country:{code}", len(asset_paths)))
                self.last_call_metadata = {
                    "provider": "fake-provider",
                    "model": "fake-model",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
                self.last_call_trace = _fake_trace(
                    f"country:{code}",
                    {"error": "first attempt failed"},
                    product_id=product_snapshot["product_id"],
                )
                raise RuntimeError("first attempt failed")
            return super().generate_country_evaluation(
                product_snapshot=product_snapshot,
                product_facts=product_facts,
                country=country,
                asset_snapshot=asset_snapshot,
                asset_paths=asset_paths,
            )

    calls = []
    service = FineAiEvaluationService(
        repository=InMemoryEvaluationRepository(),
        gemini_client=FlakyCountryGeminiClient(calls, fail_once_country="FR"),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
        country_retry_attempts=2,
    )

    run = service.create_run(123, countries=["DE", "FR", "IT"])
    result = service.run_evaluation(run["evaluation_run_id"])

    assert result["status"] == "completed"
    assert [call[0] for call in calls] == [
        "product_facts",
        "country:DE",
        "country:FR",
        "country:FR",
        "country:IT",
    ]
    assert result["countries"]["FR"]["status"] == "completed"
    fr_step = next(step for step in result["progress"]["steps"] if step["key"] == "country_FR")
    assert any("第 1 次失败" in log["message"] for log in fr_step["logs"])


def test_production_fine_ai_country_request_interval_defaults_to_zero():
    from appcore import fine_ai_evaluation_service as mod

    assert mod.PRODUCTION_COUNTRY_REQUEST_INTERVAL_SECONDS == 0


def test_parallel_country_mode_honors_adc_and_configured_country_concurrency(monkeypatch):
    import threading
    import time

    from appcore import fine_ai_evaluation_model_config as model_config
    from appcore import fine_ai_evaluation_service as service_mod
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    store = {
        model_config.PARALLEL_MODE_KEY: "parallel",
        model_config.COUNTRY_CONCURRENCY_KEY: "2",
        model_config.SETTING_KEYS[model_config.SCHEDULED_PROFILE]: "gemini_vertex_adc",
    }
    monkeypatch.setattr(model_config.settings_store, "get_setting", lambda key: store.get(key))

    active = 0
    max_active = 0
    lock = threading.Lock()
    country_calls = []

    class TrackingCountryGeminiClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.last_call_metadata = {}
            self.last_call_trace = {}

        def generate_country_evaluation(
            self,
            *,
            product_snapshot,
            product_facts,
            country,
            asset_snapshot,
            asset_paths,
        ):
            nonlocal active, max_active
            code = country["country_code"]
            with lock:
                active += 1
                max_active = max(max_active, active)
                country_calls.append(code)
            try:
                time.sleep(0.03)
                result = make_country_result(code)
                self.last_call_metadata = {
                    "provider": "gemini_vertex_adc",
                    "model": "gemini-3.5-flash",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
                self.last_call_trace = _fake_trace(
                    f"country:{code}",
                    result,
                    product_id=product_snapshot["product_id"],
                )
                return result
            finally:
                with lock:
                    active -= 1

    monkeypatch.setattr(service_mod, "FineAiGeminiClient", TrackingCountryGeminiClient)

    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    run = service.create_run(
        123,
        countries=["DE", "FR", "IT"],
        model_profile=model_config.SCHEDULED_PROFILE,
    )
    result = service.run_evaluation(run["evaluation_run_id"])

    assert result["status"] == "completed"
    assert result["metadata"]["provider"] == "gemini_vertex"
    assert result["metadata"]["country_parallel_mode"] == "parallel"
    assert result["metadata"]["country_execution_mode"] == "parallel"
    assert result["metadata"]["country_concurrency"] == 2
    assert max_active == 2
    assert set(country_calls) == {"DE", "FR", "IT"}


def test_country_request_waits_between_countries_and_marks_progress():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    calls = []
    sleep_calls = []
    repository = InMemoryEvaluationRepository()

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        stored = repository.rows[run["evaluation_run_id"]]
        progress = stored["progress"]
        fr_step = next(step for step in progress["steps"] if step["key"] == "country_FR")
        assert progress["current_step"] == "country_wait_FR"
        assert progress["countries"]["DE"] == "completed"
        assert progress["countries"]["FR"] == "waiting"
        assert fr_step["status"] == "waiting"
        assert "等待 30 秒" in fr_step["message"]

    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient(calls),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
        country_request_interval_seconds=30,
        country_request_sleeper=fake_sleep,
    )

    run = service.create_run(123, countries=["DE", "FR"])
    result = service.run_evaluation(run["evaluation_run_id"])

    assert result["status"] == "completed"
    assert sleep_calls == [30]
    assert [call[0] for call in calls] == ["product_facts", "country:DE", "country:FR"]
    assert any("等待 30 秒" in event["message"] for event in result["progress"]["events"])


def test_country_failure_persists_raw_response_debug():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    class RawFailureGeminiClient(FakeGeminiClient):
        def generate_country_evaluation(self, *, product_snapshot, product_facts, country, asset_snapshot, asset_paths):
            self.last_call_metadata = {
                "provider": "fake-provider",
                "model": "fake-model",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "raw_response": {
                    "text_preview": '{"country_code": "DE", bad',
                    "json_parse_error": "Expecting property name",
                },
            }
            raise RuntimeError("JSON parse failed after repair")

    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=RawFailureGeminiClient([]),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    run = service.create_run(123, countries=["DE"])
    result = service.run_evaluation(run["evaluation_run_id"])

    stored_country = repository.country_rows[(run["evaluation_run_id"], "DE")]
    assert result["status"] == "failed"
    assert stored_country["raw_response"]["json_parse_error"] == "Expecting property name"
    assert stored_country["raw_response"]["text_preview"] == '{"country_code": "DE", bad'


def test_pipeline_handles_empty_assets_without_crashing():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    service = FineAiEvaluationService(
        repository=InMemoryEvaluationRepository(),
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(empty=True),
    )

    run = service.create_run(123)
    result = service.run_evaluation(run["evaluation_run_id"])

    assert result["status"] == "completed"
    assert result["countries"]["DE"]["creative_fit"]["creative_missing"] is True
    assert "product_images" in result["countries"]["DE"]["missing_data"]


def test_external_link_run_uses_product_link_without_local_product_lookup():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=ExplodingProductSnapshotService(),
        asset_snapshot_service=ExplodingAssetSnapshotService(),
    )

    run = service.create_external_link_run(
        product_link="https://example.test/products/new-idea",
        product_name="New Idea",
        product_code="new-idea",
        countries=["DE"],
    )

    stored = repository.get_run(run["evaluation_run_id"])
    assert run["product_id"] == "0"
    assert stored["product_snapshot"]["product_url"] == "https://example.test/products/new-idea"
    assert stored["product_snapshot"]["product_name"] == "New Idea"
    assert stored["metadata"]["source_type"] == "external_product_link"
    assert stored["metadata"]["data_quality"]["has_product_url"] is True
    assert stored["metadata"]["asset_snapshot"] == {
        "cover_images": [],
        "product_images": [],
        "videos": [],
        "asset_paths": [],
        "warnings": [],
    }


def test_external_link_run_records_manual_fine_ai_model_profile(monkeypatch):
    from appcore import fine_ai_evaluation_model_config as model_config
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    monkeypatch.setattr(
        model_config,
        "get_profile_config",
        lambda profile: {
            "profile": profile,
            "provider": "openrouter",
            "model": "google/gemini-3.5-flash",
            "label": "OPENROUTER",
        },
    )
    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=ExplodingProductSnapshotService(),
        asset_snapshot_service=ExplodingAssetSnapshotService(),
    )

    run = service.create_external_link_run(
        product_link="https://example.test/products/new-idea",
        product_name="New Idea",
        countries=["DE"],
    )

    stored = repository.get_run(run["evaluation_run_id"])
    assert stored["metadata"]["model_profile"] == "manual"
    assert stored["metadata"]["provider"] == "openrouter"
    assert stored["metadata"]["model"] == "google/gemini-3.5-flash"


def test_external_link_run_records_product_link_check_progress():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=ExplodingProductSnapshotService(),
        asset_snapshot_service=ExplodingAssetSnapshotService(),
    )

    bad_link = "https://shop.example/products/bad"
    good_link = "https://shop.example/products/good"
    run = service.create_external_link_run(
        product_link=bad_link,
        product_name="New Idea",
        product_code="new-idea",
        countries=["DE"],
        link_check_result={
            "ok": True,
            "status": "replaced",
            "original_link": bad_link,
            "selected_link": good_link,
            "message": "replacement selected",
            "candidates": [
                {
                    "url": bad_link,
                    "ok": False,
                    "http_status": 404,
                    "error": "http 404",
                    "elapsed_ms": 4,
                    "used": False,
                    "source": "current",
                },
                {
                    "url": good_link,
                    "ok": True,
                    "http_status": 200,
                    "error": None,
                    "elapsed_ms": 5,
                    "used": True,
                    "source": "mingkong",
                },
            ],
        },
    )

    stored = repository.get_run(run["evaluation_run_id"])
    step_keys = [step["key"] for step in stored["progress"]["steps"]]
    link_step = stored["progress"]["steps"][0]

    assert run["link_check"]["selected_link"] == good_link
    assert stored["product_snapshot"]["product_url"] == good_link
    assert stored["metadata"]["external_product_link"] == good_link
    assert stored["metadata"]["link_check"]["selected_link"] == good_link
    assert step_keys[:2] == ["product_link_check", "data_preparation"]
    assert link_step["status"] == "completed"
    assert any(item["value"] == good_link for item in link_step["debug"])


def test_external_link_run_includes_current_card_video_asset_without_local_product_lookup():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    repository = InMemoryEvaluationRepository()
    card_video_assets = FakeExternalCardVideoSnapshotService()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=ExplodingProductSnapshotService(),
        asset_snapshot_service=ExplodingAssetSnapshotService(),
        external_card_video_snapshot_service=card_video_assets,
    )

    run = service.create_external_link_run(
        product_link="https://example.test/products/new-idea",
        product_name="New Idea",
        product_code="new-idea",
        countries=["DE"],
        card_video_object_key="mk/videos/selected-card.mp4",
        card_video_path="uploads2/selected-card.mp4",
        card_video_url="/xuanpin/api/mk-video?path=uploads2%2Fselected-card.mp4",
        card_video_name="selected-card.mp4",
        card_video_duration_seconds=18.5,
    )

    stored = repository.get_run(run["evaluation_run_id"])
    assert card_video_assets.calls == [
        {
            "card_video_object_key": "mk/videos/selected-card.mp4",
            "card_video_path": "uploads2/selected-card.mp4",
            "card_video_url": "/xuanpin/api/mk-video?path=uploads2%2Fselected-card.mp4",
            "card_video_name": "selected-card.mp4",
            "card_video_duration_seconds": 18.5,
        }
    ]
    assert stored["metadata"]["include_assets"] is True
    assert stored["metadata"]["include_videos"] is True
    assert stored["metadata"]["asset_snapshot"]["videos"][0]["object_key"] == "mk/videos/selected-card.mp4"
    assert stored["metadata"]["asset_snapshot"]["asset_paths"] == ["G:/tmp/selected-card_15s_llm.mp4"]
    assert stored["metadata"]["external_card_video"]["object_key"] == "mk/videos/selected-card.mp4"
    assert stored["product_snapshot"]["videos"][0]["object_key"] == "mk/videos/selected-card.mp4"
    assert stored["product_snapshot"]["asset_count"]["videos"] == 1


def test_external_country_rerun_reuses_current_card_video_asset_snapshot():
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

    calls = []
    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient(calls),
        product_snapshot_service=ExplodingProductSnapshotService(),
        asset_snapshot_service=ExplodingAssetSnapshotService(),
        external_card_video_snapshot_service=FakeExternalCardVideoSnapshotService(),
    )
    run = service.create_external_link_run(
        product_link="https://example.test/products/new-idea",
        product_name="New Idea",
        product_code="new-idea",
        countries=["DE"],
        card_video_object_key="mk/videos/selected-card.mp4",
        card_video_path="uploads2/selected-card.mp4",
        card_video_name="selected-card.mp4",
    )

    service._rerun_country_sync(0, run["evaluation_run_id"], "DE", include_assets=False, include_videos=False)

    assert ("country:DE", 1) in calls
    stored = repository.get_run(run["evaluation_run_id"])
    assert stored["metadata"]["asset_snapshot"]["asset_paths"] == ["G:/tmp/selected-card_15s_llm.mp4"]


def test_external_card_video_snapshot_uses_15s_llm_clip_optimizer(tmp_path, monkeypatch):
    from appcore import material_evaluation
    from appcore.fine_ai_evaluation_snapshots import ExternalCardVideoSnapshotService

    optimized = tmp_path / "selected-card_15s_llm.mp4"
    optimized.write_bytes(b"video")
    calls = []

    def fake_make_eval_clip_15s(product_id, item):
        calls.append((product_id, dict(item)))
        return optimized

    monkeypatch.setattr(material_evaluation, "_make_eval_clip_15s", fake_make_eval_clip_15s)

    snapshot = ExternalCardVideoSnapshotService().build_snapshot(
        card_video_object_key="mk/videos/selected-card.mp4",
        card_video_path="uploads2/selected-card.mp4",
        card_video_name="selected-card.mp4",
        card_video_duration_seconds="18.5",
    )

    assert calls[0][0] == 0
    assert calls[0][1]["object_key"] == "mk/videos/selected-card.mp4"
    assert snapshot["asset_paths"] == [str(optimized)]
    assert snapshot["videos"][0]["duration_seconds"] == 18.5


class InMemoryEvaluationRepository:
    def __init__(self):
        self.rows = {}
        self.country_rows = {}

    def create_run(self, run):
        self.rows[run["evaluation_run_id"]] = dict(run)
        return dict(run)

    def get_run(self, evaluation_run_id):
        return dict(self.rows[evaluation_run_id])

    def get_latest_run(self, product_id):
        rows = [row for row in self.rows.values() if str(row["product_id"]) == str(product_id)]
        return dict(rows[-1]) if rows else None

    def list_inflight_runs(self):
        return [
            dict(row)
            for row in self.rows.values()
            if str(row.get("status") or "").lower() in {"queued", "running"}
        ]

    def get_latest_external_link_run(self, product_link, **kwargs):
        product_link = str(product_link or "").strip()
        rows = []
        for row in self.rows.values():
            metadata = row.get("metadata") or {}
            if str(row.get("product_id") or "") != "0":
                continue
            if metadata.get("source_type") != "external_product_link":
                continue
            link_check = metadata.get("link_check") or {}
            links = {
                str(metadata.get("external_product_link") or "").strip(),
                str(link_check.get("original_link") or "").strip(),
                str(link_check.get("selected_link") or "").strip(),
            }
            if product_link not in links:
                continue
            card_video = metadata.get("external_card_video") or {}
            if kwargs.get("card_video_object_key") and card_video.get("object_key") != kwargs["card_video_object_key"]:
                continue
            if kwargs.get("card_video_path") and card_video.get("path") != kwargs["card_video_path"]:
                continue
            if kwargs.get("card_video_url") and card_video.get("url") != kwargs["card_video_url"]:
                continue
            if kwargs.get("card_video_name") and card_video.get("name") != kwargs["card_video_name"]:
                continue
            rows.append(row)
        return dict(rows[-1]) if rows else None

    def update_run(self, evaluation_run_id, **fields):
        self.rows[evaluation_run_id].update(fields)
        return dict(self.rows[evaluation_run_id])

    def upsert_country(self, evaluation_run_id, country_code, data):
        self.country_rows[(evaluation_run_id, country_code)] = dict(data)

    def list_countries(self, evaluation_run_id):
        return {
            code: dict(data)
            for (run_id, code), data in self.country_rows.items()
            if run_id == evaluation_run_id
        }


class FakeProductSnapshotService:
    def build_snapshot(self, product_id, *, include_assets=True, include_videos=True, product_url_override=None):
        return {
            "product_id": str(product_id),
            "product_name": "Sample Product",
            "brand": "",
            "category": "",
            "product_url": product_url_override or "https://example.test/products/sample",
            "landing_page_url": product_url_override or "https://example.test/products/sample",
            "price": None,
            "currency": "",
            "sku_count": 0,
            "asset_count": {"images": 0, "videos": 0},
        }


class FakeAssetSnapshotService:
    def __init__(self, empty=False):
        self.empty = empty

    def build_snapshot(self, product_id, *, include_assets=True, include_videos=True):
        if self.empty:
            return {"cover_images": [], "product_images": [], "videos": [], "asset_paths": []}
        return {"cover_images": [], "product_images": ["image"], "videos": ["video"], "asset_paths": []}


class ExplodingProductSnapshotService:
    def build_snapshot(self, *args, **kwargs):
        raise AssertionError("external link evaluation must not load local media_products")


class ExplodingAssetSnapshotService:
    def build_snapshot(self, *args, **kwargs):
        raise AssertionError("external link evaluation must not load local media assets")


class FakeExternalCardVideoSnapshotService:
    def __init__(self):
        self.calls = []

    def build_snapshot(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "cover_images": [],
            "product_images": [],
            "videos": [
                {
                    "asset_id": "external-card-video",
                    "asset_type": "video",
                    "filename": kwargs.get("card_video_name") or "selected-card.mp4",
                    "object_key": kwargs["card_video_object_key"],
                    "duration_seconds": kwargs.get("card_video_duration_seconds"),
                    "source_path": kwargs.get("card_video_path") or "",
                    "source_url": kwargs.get("card_video_url") or "",
                    "mime_type": "video/mp4",
                }
            ],
            "asset_paths": ["G:/tmp/selected-card_15s_llm.mp4"],
            "warnings": [],
        }


class FakeGeminiClient:
    def __init__(self, calls, fail_country=None):
        self.calls = calls
        self.fail_country = fail_country
        self.last_call_metadata = {}
        self.last_call_trace = {}

    def generate_product_facts(self, *, product_snapshot, countries):
        self.calls.append(("product_facts", product_snapshot["product_id"]))
        self.last_call_metadata = {
            "provider": "fake-provider",
            "model": "fake-model",
            "usage": {"input_tokens": 12, "output_tokens": 34},
        }
        result = {
            "product_id": product_snapshot["product_id"],
            "product_name": product_snapshot["product_name"],
            "category_detected": None,
            "sku_facts": [],
            "price_facts": [],
            "dimension_facts": [],
            "material_facts": [],
            "feature_facts": [],
            "claim_inventory": [],
            "claim_consistency_risks": [],
            "missing_data": [],
            "assumptions": [],
            "generated_search_keywords": {
                "english_keywords": [],
                "country_keyword_hints": {"DE": [], "FR": [], "IT": [], "ES": [], "JP": []},
            },
        }
        self.last_call_trace = _fake_trace("product_facts", result, product_id=product_snapshot["product_id"])
        return result

    def generate_country_evaluation(self, *, product_snapshot, product_facts, country, asset_snapshot, asset_paths):
        code = country["country_code"]
        self.calls.append((f"country:{code}", len(asset_paths)))
        self.last_call_metadata = {
            "provider": "fake-provider",
            "model": "fake-model",
            "usage": {"input_tokens": 56, "output_tokens": 78},
        }
        if code == self.fail_country:
            self.last_call_trace = _fake_trace(f"country:{code}", {"error": "simulated country failure"}, product_id=product_snapshot["product_id"])
            raise RuntimeError("simulated country failure")
        result = make_country_result(code, creative_missing=not asset_snapshot["product_images"] and not asset_snapshot["videos"])
        self.last_call_trace = _fake_trace(f"country:{code}", result, product_id=product_snapshot["product_id"])
        return result


def _fake_trace(stage, parsed_json, *, product_id):
    return {
        "provider": "fake-provider",
        "model_id": "fake-model",
        "use_case_code": f"fine_ai_evaluation.{stage}",
        "project_id": f"fine-ai-product-{product_id}",
        "request": {
            "summary": {"media_count": 0},
            "system_prompt": "system prompt",
            "prompt": f"full prompt for {stage}",
            "payload": {"prompt": f"full prompt for {stage}", "provider_override": "fake-provider"},
        },
        "response": {
            "summary": {"input_tokens": 1, "output_tokens": 2},
            "parsed_json": parsed_json,
            "raw_payload": {"json": parsed_json, "usage": {"input_tokens": 1, "output_tokens": 2}},
        },
    }


def make_country_result(code, *, creative_missing=False):
    name_map = {
        "DE": ("Germany", "德国", "German", "EUR"),
        "FR": ("France", "法国", "French", "EUR"),
        "IT": ("Italy", "意大利", "Italian", "EUR"),
        "ES": ("Spain", "西班牙", "Spanish", "EUR"),
        "JP": ("Japan", "日本", "Japanese", "JPY"),
    }
    country_name, country_name_zh, language, currency = name_map[code]
    missing = ["product_images", "videos"] if creative_missing else []
    return {
        "country_code": code,
        "country_name": country_name,
        "country_name_zh": country_name_zh,
        "language": language,
        "currency": currency,
        "status": "completed",
        "scores": {
            "overall_score": 70,
            "product_market_fit_score": 70,
            "demand_score": 70,
            "competition_score": 70,
            "pricing_score": 70,
            "creative_fit_score": 0 if creative_missing else 70,
            "landing_page_fit_score": 70,
            "operational_fit_score": 70,
            "compliance_risk_score": 70,
        },
        "decision": {
            "final_decision": "TEST",
            "confidence": "medium",
            "one_sentence_reason": "Structured test result.",
            "why": [],
            "blocking_issues": [],
        },
        "market_fit": {
            "local_positioning": "",
            "target_segments": [],
            "use_cases": [],
            "demand_analysis": {"summary": "", "facts": [], "inferences": [], "evidence_gaps": []},
            "seasonality": [],
            "market_entry_notes": [],
        },
        "competitor_analysis": {
            "summary": "",
            "competitors": [],
            "competitive_advantages": [],
            "competitive_disadvantages": [],
            "evidence_gaps": [],
        },
        "pricing_analysis": {
            "current_price": None,
            "current_currency": "",
            "recommended_price_range": {"min": None, "max": None, "currency": currency},
            "pricing_commentary": "",
            "margin_inputs_missing": [],
            "cannot_calculate_reasons": [],
        },
        "creative_fit": {
            "creative_missing": creative_missing,
            "assets_reviewed": {"cover_images": [], "product_images": [], "videos": []},
            "cover_image_audit": {
                "score": 0,
                "issues": [],
                "localization_needed": [],
                "claim_risks": [],
                "recommended_cover_directions": [],
            },
            "product_image_audit": {"score": 0, "issues": [], "recommended_image_directions": []},
            "video_audit": {
                "score": 0,
                "timestamp_findings": [],
                "hook_analysis": "",
                "proof_gaps": [],
                "scenes_to_keep": [],
                "scenes_to_replace_or_reshoot": [],
            },
            "localized_copy_directions": {
                "cover_text_direction": [],
                "hook_direction": [],
                "cta_direction": [],
                "language_notes": [],
            },
            "final_creative_decision": "NO_CREATIVE_PROVIDED" if creative_missing else "USE_AS_IS",
        },
        "landing_page_localization": {
            "localization_difficulty": 50,
            "hero_section": {
                "title_direction": "",
                "subtitle_direction": "",
                "cta_direction": "",
                "image_direction": "",
            },
            "sections_needed": [],
            "trust_elements_needed": [],
            "claims_to_avoid_or_rewrite": [],
            "unit_and_currency_notes": [],
            "faq_directions": [],
        },
        "risks": {
            "claim_risks": [],
            "compliance_risks": [],
            "operational_risks": [],
            "trust_risks": [],
            "localization_risks": [],
        },
        "recommendations": {
            "recommended_positioning": "",
            "ad_test_angles": [],
            "audience_suggestions": [],
            "landing_page_actions": [],
            "creative_actions": [],
            "first_30_day_test_plan": {
                "test_priority": "medium",
                "creative_variants": [],
                "landing_page_variants": [],
                "success_metrics": [],
                "kill_criteria": [],
                "scale_criteria": [],
            },
        },
        "sources": [],
        "missing_data": missing,
        "warnings": [],
    }
