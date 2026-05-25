import json
import pytest
from appcore.fine_ai_evaluation_service import FineAiEvaluationService
from test_fine_ai_evaluation_pipeline import (
    InMemoryEvaluationRepository,
    FakeProductSnapshotService,
    FakeAssetSnapshotService,
    FakeGeminiClient,
    make_country_result,
)

def test_external_link_run_reuses_completed_countries(monkeypatch):
    # 0. Check imported module path
    import inspect
    print("FineAiEvaluationService file:", inspect.getfile(FineAiEvaluationService))

    repository = InMemoryEvaluationRepository()
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=FakeGeminiClient([]),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    # 1. Mock DB queries to simulate finding a previous run
    prev_run_id = "eval_prev123"
    prev_run = {
        "evaluation_run_id": prev_run_id,
        "product_snapshot_json": json.dumps({"product_url": "https://example.test/products/sample"}),
        "metadata_json": json.dumps({"external_card_video": {"path": "uploads2/selected-card.mp4"}}),
        "product_facts_json": json.dumps({"category_detected": "Electronics", "sku_facts": []}),
    }

    country_row = {
        "country_code": "DE",
        "product_id": "0",
        "country_name": "Germany",
        "status": "completed",
        "scores_json": json.dumps({"overall_score": 75}),
        "decision_json": json.dumps({"final_decision": "TEST"}),
        "full_result_json": json.dumps(make_country_result("DE")),
        "sources_json": json.dumps([]),
        "raw_response_json": json.dumps({}),
        "metadata_json": json.dumps({}),
        "error_message": None,
    }

    query_calls = []
    execute_calls = []

    def mock_query(sql, args=()):
        query_calls.append((sql, args))
        if "ai_evaluation_runs" in sql:
            return [prev_run]
        if "ai_country_evaluations" in sql:
            return [country_row]
        return []

    def mock_execute(sql, args=()):
        execute_calls.append((sql, args))
        return 1

    monkeypatch.setattr("appcore.db.query", mock_query)
    monkeypatch.setattr("appcore.db.execute", mock_execute)
    
    # 2. Call create_external_link_run which should trigger the copy logic
    run_result = service.create_external_link_run(
        product_link="https://example.test/products/sample",
        product_name="Sample Product",
        countries=["DE", "FR"],
        card_video_path="uploads2/selected-card.mp4",
    )

    new_run_id = run_result["evaluation_run_id"]
    stored_run = repository.get_run(new_run_id)

    print("query_calls:", query_calls)
    print("execute_calls:", execute_calls)
    print("stored_run:", stored_run)

    # Assert that product_facts were inherited
    assert stored_run["product_facts"] == {"category_detected": "Electronics", "sku_facts": []}

    # Assert that DE was marked as completed in metadata
    assert "DE" in stored_run["metadata"]["countries_completed"]

    # Assert that insert statement was executed for DE in new run ID
    assert len(execute_calls) > 0
    insert_sql, insert_args = execute_calls[0]
    assert "INSERT INTO ai_country_evaluations" in insert_sql
    assert insert_args[0] == new_run_id
    assert insert_args[2] == "DE"

def test_run_evaluation_skips_completed_countries_parallel(monkeypatch):
    from appcore import fine_ai_evaluation_model_config as model_config
    from appcore import fine_ai_evaluation_service as service_mod
    monkeypatch.setattr(model_config, "get_parallel_mode", lambda: "parallel")
    monkeypatch.setattr(model_config, "get_country_concurrency", lambda: 2)

    repository = InMemoryEvaluationRepository()
    calls = []
    
    class DummyFineAiGeminiClient:
        def __init__(self, **kwargs):
            self.last_call_metadata = {}
            self.last_call_trace = {}
        def generate_country_evaluation(self, *, product_snapshot, product_facts, country, asset_snapshot, asset_paths):
            calls.append((f"country:{country['country_code']}", len(asset_paths)))
            self.last_call_metadata = {
                "provider": "fake-provider",
                "model": "fake-model",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            self.last_call_trace = {}
            return make_country_result(country["country_code"])

    monkeypatch.setattr(service_mod, "FineAiGeminiClient", DummyFineAiGeminiClient)

    fake_client = FakeGeminiClient(calls)
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=fake_client,
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    # 1. Create a run with existing completed countries (DE) and pending countries (FR)
    run = service.create_run(123, countries=["DE", "FR"])
    run_id = run["evaluation_run_id"]
    
    # Pre-populate product_facts
    repository.update_run(run_id, product_facts={"category_detected": "Electronics"})

    # Pre-populate country rows for DE as completed
    repository.upsert_country(run_id, "DE", {
        "status": "completed",
        "full_result": make_country_result("DE"),
    })

    # 2. Run evaluation
    result = service.run_evaluation(run_id)

    # FR should be evaluated (calling LLM), but DE must be SKIPPED!
    assert result["status"] == "completed"
    assert "country:FR" in [c[0] for c in calls]
    assert "country:DE" not in [c[0] for c in calls]
    assert "product_facts" not in [c[0] for c in calls]  # facts were reused!

def test_run_evaluation_skips_completed_countries_serial(monkeypatch):
    from appcore import fine_ai_evaluation_model_config as model_config
    monkeypatch.setattr(model_config, "get_parallel_mode", lambda: "serial")

    repository = InMemoryEvaluationRepository()
    calls = []
    fake_client = FakeGeminiClient(calls)
    service = FineAiEvaluationService(
        repository=repository,
        gemini_client=fake_client,
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
    )

    # 1. Create a run with existing completed countries (DE) and pending countries (FR)
    run = service.create_run(123, countries=["DE", "FR"])
    run_id = run["evaluation_run_id"]
    
    # Pre-populate product_facts
    repository.update_run(run_id, product_facts={"category_detected": "Electronics"})

    # Pre-populate country rows for DE as completed
    repository.upsert_country(run_id, "DE", {
        "status": "completed",
        "full_result": make_country_result("DE"),
    })

    # 2. Run evaluation
    result = service.run_evaluation(run_id)

    # FR should be evaluated, but DE must be SKIPPED!
    assert result["status"] == "completed"
    assert "country:FR" in [c[0] for c in calls]
    assert "country:DE" not in [c[0] for c in calls]
    assert "product_facts" not in [c[0] for c in calls]
