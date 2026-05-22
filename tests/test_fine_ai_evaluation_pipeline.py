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


def test_external_link_zero_product_id_status_is_not_treated_as_missing(monkeypatch):
    from appcore import fine_ai_evaluation_repository as repo_mod
    from appcore.fine_ai_evaluation_service import FineAiEvaluationService

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

    status = service.get_status(0, "eval_external")

    assert status["product_id"] == "0"
    assert status["status"] == "running"


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
            "final_creative_decision": "NO_CREATIVE_PROVIDED" if creative_missing else "LOCALIZE_BEFORE_TEST",
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
