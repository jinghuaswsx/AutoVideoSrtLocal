"""Service workflow for single-product five-country fine AI evaluation."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from appcore import runner_lifecycle
from appcore.fine_ai_evaluation_aggregator import build_summary
from appcore.fine_ai_evaluation_country_config import (
    DEFAULT_COUNTRY_CODES,
    country_configs,
    get_country_config,
    normalize_country_codes,
)
from appcore.fine_ai_evaluation_frontend_mapper import build_frontend
from appcore.fine_ai_evaluation_repository import FineAiEvaluationRepository
from appcore.fine_ai_evaluation_schemas import (
    COUNTRY_EVALUATION_SCHEMA,
    PRODUCT_FACTS_SCHEMA,
    validate_json_schema,
)
from appcore.fine_ai_evaluation_snapshots import (
    AssetSnapshotService,
    ProductNotFoundError,
    ProductSnapshotService,
)
from appcore.fine_ai_gemini_client import MODEL, PROVIDER, FineAiGeminiClient

log = logging.getLogger(__name__)

RUN_STATUSES = {"queued", "running", "completed", "partially_completed", "failed", "cancelled"}
COUNTRY_PENDING_STATUSES = {"pending", "running", "completed", "failed", "skipped"}


class FineAiEvaluationError(RuntimeError):
    code = "FINE_AI_EVALUATION_ERROR"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class FineAiEvaluationNotFound(FineAiEvaluationError):
    code = "EVALUATION_RUN_NOT_FOUND"


class FineAiEvaluationService:
    def __init__(
        self,
        *,
        repository=None,
        gemini_client=None,
        product_snapshot_service=None,
        asset_snapshot_service=None,
    ):
        self.repository = repository or FineAiEvaluationRepository()
        self.gemini_client = gemini_client or FineAiGeminiClient()
        self.product_snapshot_service = product_snapshot_service or ProductSnapshotService()
        self.asset_snapshot_service = asset_snapshot_service or AssetSnapshotService()

    def create_run(
        self,
        product_id: int | str,
        *,
        force_refresh: bool = False,
        countries: list[str] | None = None,
        include_assets: bool = True,
        include_videos: bool = True,
        locale: str = "zh-CN",
        product_url_override: str | None = None,
    ) -> dict[str, Any]:
        country_codes = normalize_country_codes(countries)
        product_snapshot = self.product_snapshot_service.build_snapshot(
            product_id,
            include_assets=include_assets,
            include_videos=include_videos,
            product_url_override=product_url_override,
        )
        asset_snapshot = self.asset_snapshot_service.build_snapshot(
            product_id,
            include_assets=include_assets,
            include_videos=include_videos,
        )
        now = _now_iso()
        evaluation_run_id = f"eval_{uuid.uuid4().hex}"
        progress = _initial_progress(country_codes)
        run = {
            "evaluation_run_id": evaluation_run_id,
            "product_id": str(product_id),
            "status": "queued",
            "countries": country_codes,
            "product_snapshot": _with_asset_counts(product_snapshot, asset_snapshot),
            "product_facts": {},
            "summary": {},
            "frontend": {},
            "metadata": {
                "schema_version": "1.0",
                "model": MODEL,
                "provider": PROVIDER,
                "force_refresh": bool(force_refresh),
                "include_assets": bool(include_assets),
                "include_videos": bool(include_videos),
                "locale": locale,
                "countries_requested": country_codes,
                "countries_completed": [],
                "countries_failed": [],
                "run_errors": [],
                "token_usage": {},
                "asset_snapshot": asset_snapshot,
                "data_quality": _data_quality(product_snapshot, asset_snapshot),
            },
            "progress": progress,
            "created_at": now,
            "updated_at": now,
        }
        self.repository.create_run(run)
        return {
            "evaluation_run_id": evaluation_run_id,
            "product_id": str(product_id),
            "status": "queued",
            "countries": country_codes,
            "created_at": now,
        }

    def start_run_async(self, evaluation_run_id: str) -> bool:
        return runner_lifecycle.start_tracked_thread(
            project_type="fine_ai_evaluation",
            task_id=str(evaluation_run_id),
            target=self.run_evaluation,
            args=(evaluation_run_id,),
            daemon=True,
            stage="run",
            interrupt_policy="cautious",
        )

    def run_evaluation(self, evaluation_run_id: str) -> dict[str, Any]:
        run = self._require_run(evaluation_run_id)
        country_codes = normalize_country_codes(run.get("countries") or DEFAULT_COUNTRY_CODES)
        metadata = dict(run.get("metadata") or {})
        asset_snapshot = metadata.get("asset_snapshot") or {}
        product_snapshot = dict(run.get("product_snapshot") or {})
        product_id = str(run.get("product_id") or product_snapshot.get("product_id") or "")
        countries: dict[str, dict[str, Any]] = {}
        failed_codes: list[str] = []
        completed_codes: list[str] = []
        run_errors: list[dict[str, str]] = list(metadata.get("run_errors") or [])

        try:
            self.repository.update_run(
                evaluation_run_id,
                status="running",
                started_at=_now_iso(),
                progress=_progress(country_codes, "product_fact_extraction", completed_steps=0),
            )
            product_facts = self.gemini_client.generate_product_facts(
                product_snapshot=product_snapshot,
                countries=country_configs(country_codes),
            )
            validate_json_schema(product_facts, PRODUCT_FACTS_SCHEMA)
            metadata = self._merge_call_metadata(metadata, "product_facts")
            self.repository.update_run(
                evaluation_run_id,
                product_facts=product_facts,
                metadata=metadata,
                progress=_progress(country_codes, "product_fact_extraction", completed_steps=1),
            )
        except Exception as exc:
            log.exception("fine AI product fact extraction failed: run=%s", evaluation_run_id)
            run_errors.append({"stage": "product_fact_extraction", "message": str(exc)[:500]})
            metadata["run_errors"] = run_errors
            self.repository.update_run(
                evaluation_run_id,
                status="failed",
                metadata=metadata,
                failed_at=_now_iso(),
                error_message=str(exc)[:500],
                progress=_progress(country_codes, "failed", completed_steps=0),
            )
            return self.get_result(product_id, evaluation_run_id)

        product_facts = self._require_run(evaluation_run_id).get("product_facts") or {}
        for index, code in enumerate(country_codes, start=1):
            country = get_country_config(code)
            self.repository.update_run(
                evaluation_run_id,
                status="running",
                progress=_progress(
                    country_codes,
                    f"country_evaluation_{code}",
                    running_country=code,
                    completed_steps=index,
                    completed_countries=completed_codes,
                    failed_countries=failed_codes,
                ),
            )
            try:
                result = self.gemini_client.generate_country_evaluation(
                    product_snapshot=product_snapshot,
                    product_facts=product_facts,
                    country=country,
                    asset_snapshot=asset_snapshot,
                    asset_paths=list(asset_snapshot.get("asset_paths") or []),
                )
                result = _normalize_country_result(result, country, asset_snapshot)
                validate_json_schema(result, COUNTRY_EVALUATION_SCHEMA)
                completed_codes.append(code)
                countries[code] = result
                self.repository.upsert_country(
                    evaluation_run_id,
                    code,
                    {
                        "product_id": product_id,
                        "status": "completed",
                        "full_result": result,
                        "metadata": self._call_metadata(),
                        "raw_response": {},
                    },
                )
            except Exception as exc:
                log.exception("fine AI country evaluation failed: run=%s country=%s", evaluation_run_id, code)
                failed_codes.append(code)
                failed = _failed_country_result(country, str(exc))
                countries[code] = failed
                self.repository.upsert_country(
                    evaluation_run_id,
                    code,
                    {
                        "product_id": product_id,
                        "status": "failed",
                        "full_result": failed,
                        "metadata": self._call_metadata(),
                        "raw_response": {},
                        "error_message": str(exc)[:500],
                    },
                )

        countries = _unwrap_country_results(self.repository.list_countries(evaluation_run_id)) or countries
        summary = build_summary(countries)
        frontend = build_frontend(summary, countries)
        metadata.update({
            "model": MODEL,
            "provider": PROVIDER,
            "countries_requested": country_codes,
            "countries_completed": completed_codes,
            "countries_failed": failed_codes,
            "run_errors": run_errors,
            "data_quality": metadata.get("data_quality") or _data_quality(product_snapshot, asset_snapshot),
        })
        final_status = "completed" if not failed_codes else "partially_completed"
        if not completed_codes and failed_codes:
            final_status = "failed"
        self.repository.update_run(
            evaluation_run_id,
            status=final_status,
            summary=summary,
            frontend=frontend,
            metadata=metadata,
            completed_at=_now_iso() if final_status in {"completed", "partially_completed"} else None,
            failed_at=_now_iso() if final_status == "failed" else None,
            progress=_progress(
                country_codes,
                "summary_completed",
                completed_steps=7,
                completed_countries=completed_codes,
                failed_countries=failed_codes,
            ),
        )
        return self.get_result(product_id, evaluation_run_id)

    def get_status(self, product_id: int | str, evaluation_run_id: str) -> dict[str, Any]:
        run = self._require_run(evaluation_run_id)
        self._assert_product(run, product_id)
        return {
            "evaluation_run_id": run["evaluation_run_id"],
            "product_id": str(run.get("product_id") or ""),
            "status": run.get("status") or "queued",
            "progress": run.get("progress") or _initial_progress(run.get("countries") or DEFAULT_COUNTRY_CODES),
            "started_at": run.get("started_at"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "completed_at": run.get("completed_at"),
            "failed_at": run.get("failed_at"),
        }

    def get_result(self, product_id: int | str, evaluation_run_id: str) -> dict[str, Any]:
        run = self._require_run(evaluation_run_id)
        self._assert_product(run, product_id)
        countries = _unwrap_country_results(self.repository.list_countries(evaluation_run_id))
        return _build_result_payload(run, countries)

    def get_latest_result(self, product_id: int | str) -> dict[str, Any]:
        run = self.repository.get_latest_run(product_id)
        if not run:
            raise FineAiEvaluationNotFound("Evaluation run not found")
        countries = _unwrap_country_results(self.repository.list_countries(run["evaluation_run_id"]))
        return _build_result_payload(run, countries)

    def rerun_country(
        self,
        product_id: int | str,
        evaluation_run_id: str,
        country_code: str,
        *,
        force_refresh: bool = True,
        include_assets: bool = True,
        include_videos: bool = True,
    ) -> dict[str, Any]:
        run = self._require_run(evaluation_run_id)
        self._assert_product(run, product_id)
        code = normalize_country_codes([country_code])[0]
        self.repository.update_run(
            evaluation_run_id,
            status="running",
            progress=_progress(run.get("countries") or DEFAULT_COUNTRY_CODES, f"country_evaluation_{code}", running_country=code),
        )
        runner_lifecycle.start_tracked_thread(
            project_type="fine_ai_evaluation",
            task_id=f"{evaluation_run_id}:{code}",
            target=self._rerun_country_sync,
            args=(product_id, evaluation_run_id, code),
            kwargs={"include_assets": include_assets, "include_videos": include_videos},
            daemon=True,
            stage="country_rerun",
            details={"force_refresh": bool(force_refresh)},
            interrupt_policy="cautious",
        )
        return {
            "evaluation_run_id": evaluation_run_id,
            "product_id": str(product_id),
            "country_code": code,
            "status": "running",
        }

    def _rerun_country_sync(
        self,
        product_id: int | str,
        evaluation_run_id: str,
        country_code: str,
        *,
        include_assets: bool = True,
        include_videos: bool = True,
    ) -> None:
        run = self._require_run(evaluation_run_id)
        product_snapshot = run.get("product_snapshot") or self.product_snapshot_service.build_snapshot(
            product_id,
            include_assets=include_assets,
            include_videos=include_videos,
        )
        metadata = dict(run.get("metadata") or {})
        asset_snapshot = self.asset_snapshot_service.build_snapshot(
            product_id,
            include_assets=include_assets,
            include_videos=include_videos,
        )
        metadata["asset_snapshot"] = asset_snapshot
        product_facts = run.get("product_facts") or self.gemini_client.generate_product_facts(
            product_snapshot=product_snapshot,
            countries=country_configs(run.get("countries") or DEFAULT_COUNTRY_CODES),
        )
        country = get_country_config(country_code)
        try:
            result = self.gemini_client.generate_country_evaluation(
                product_snapshot=product_snapshot,
                product_facts=product_facts,
                country=country,
                asset_snapshot=asset_snapshot,
                asset_paths=list(asset_snapshot.get("asset_paths") or []),
            )
            result = _normalize_country_result(result, country, asset_snapshot)
            self.repository.upsert_country(
                evaluation_run_id,
                country_code,
                {
                    "product_id": str(product_id),
                    "status": "completed",
                    "full_result": result,
                    "metadata": self._call_metadata(),
                    "raw_response": {},
                },
            )
        except Exception as exc:
            failed = _failed_country_result(country, str(exc))
            self.repository.upsert_country(
                evaluation_run_id,
                country_code,
                {
                    "product_id": str(product_id),
                    "status": "failed",
                    "full_result": failed,
                    "metadata": self._call_metadata(),
                    "raw_response": {},
                    "error_message": str(exc)[:500],
                },
            )
        countries = _unwrap_country_results(self.repository.list_countries(evaluation_run_id))
        summary = build_summary(countries)
        frontend = build_frontend(summary, countries)
        failed_codes = [code for code, item in countries.items() if item.get("status") == "failed"]
        completed_codes = [code for code, item in countries.items() if item.get("status") == "completed"]
        metadata.update({
            "countries_completed": completed_codes,
            "countries_failed": failed_codes,
        })
        self.repository.update_run(
            evaluation_run_id,
            status="completed" if not failed_codes else "partially_completed",
            summary=summary,
            frontend=frontend,
            metadata=metadata,
            progress=_progress(
                run.get("countries") or DEFAULT_COUNTRY_CODES,
                "summary_completed",
                completed_steps=7,
                completed_countries=completed_codes,
                failed_countries=failed_codes,
            ),
        )

    def _require_run(self, evaluation_run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(evaluation_run_id)
        if not run:
            raise FineAiEvaluationNotFound("Evaluation run not found")
        return run

    @staticmethod
    def _assert_product(run: dict[str, Any], product_id: int | str) -> None:
        if str(run.get("product_id") or "") != str(product_id):
            raise FineAiEvaluationNotFound("Evaluation run not found")

    def _call_metadata(self) -> dict[str, Any]:
        return dict(getattr(self.gemini_client, "last_call_metadata", {}) or {})

    def _merge_call_metadata(self, metadata: dict[str, Any], stage: str) -> dict[str, Any]:
        merged = dict(metadata or {})
        calls = list(merged.get("llm_call_metadata") or [])
        calls.append({"stage": stage, **self._call_metadata()})
        merged["llm_call_metadata"] = calls
        usage = dict(merged.get("token_usage") or {})
        usage[stage] = self._call_metadata().get("usage") or {}
        merged["token_usage"] = usage
        return merged


_SERVICE: FineAiEvaluationService | None = None


def get_service() -> FineAiEvaluationService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = FineAiEvaluationService()
    return _SERVICE


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _initial_progress(country_codes: list[str] | tuple[str, ...]) -> dict[str, Any]:
    return _progress(country_codes, "queued")


def _progress(
    country_codes: list[str] | tuple[str, ...],
    current_step: str,
    *,
    running_country: str | None = None,
    completed_steps: int = 0,
    completed_countries: list[str] | None = None,
    failed_countries: list[str] | None = None,
) -> dict[str, Any]:
    completed = set(completed_countries or [])
    failed = set(failed_countries or [])
    statuses = {}
    for code in country_codes:
        if code in completed:
            statuses[code] = "completed"
        elif code in failed:
            statuses[code] = "failed"
        elif code == running_country:
            statuses[code] = "running"
        else:
            statuses[code] = "pending"
    return {
        "total_steps": 7,
        "completed_steps": int(completed_steps),
        "current_step": current_step,
        "current_country": running_country or "",
        "countries": statuses,
    }


def _with_asset_counts(product_snapshot: dict[str, Any], asset_snapshot: dict[str, Any]) -> dict[str, Any]:
    out = dict(product_snapshot or {})
    out["asset_count"] = {
        "images": len(asset_snapshot.get("product_images") or []),
        "videos": len(asset_snapshot.get("videos") or []),
    }
    return out


def _data_quality(product_snapshot: dict[str, Any], asset_snapshot: dict[str, Any]) -> dict[str, bool]:
    return {
        "has_product_url": bool(product_snapshot.get("product_url")),
        "has_landing_page_url": bool(product_snapshot.get("landing_page_url")),
        "has_images": bool(asset_snapshot.get("product_images") or asset_snapshot.get("cover_images")),
        "has_videos": bool(asset_snapshot.get("videos")),
        "has_cost": product_snapshot.get("cost") is not None,
        "has_shipping_data": bool(product_snapshot.get("shipping_cost_by_country")),
    }


def _normalize_country_result(
    result: dict[str, Any],
    country: dict[str, Any],
    asset_snapshot: dict[str, Any],
) -> dict[str, Any]:
    out = dict(result or {})
    out.setdefault("country_code", country["country_code"])
    out.setdefault("country_name", country["country_name"])
    out.setdefault("country_name_zh", country["country_name_zh"])
    out.setdefault("language", country["language"])
    out.setdefault("currency", country["currency"])
    out.setdefault("status", "completed")
    out["scores"] = _normalize_scores(out.get("scores") or {})
    out.setdefault("decision", {})
    out["decision"].setdefault("final_decision", _decision_from_score(out["scores"]["overall_score"], out["decision"].get("blocking_issues") or []))
    out["decision"].setdefault("confidence", "medium")
    out["decision"].setdefault("one_sentence_reason", "")
    out["decision"].setdefault("why", [])
    out["decision"].setdefault("blocking_issues", [])
    out.setdefault("missing_data", [])
    out.setdefault("warnings", [])
    if not asset_snapshot.get("product_images") and not asset_snapshot.get("videos"):
        out["missing_data"] = _dedupe([*out["missing_data"], "product_images", "videos"])
        out.setdefault("creative_fit", {})
        out["creative_fit"]["creative_missing"] = True
        out["creative_fit"].setdefault("final_creative_decision", "NO_CREATIVE_PROVIDED")
    return _fill_country_defaults(out, country)


def _normalize_scores(scores: dict[str, Any]) -> dict[str, int]:
    from appcore.fine_ai_evaluation_schemas import SCORE_KEYS

    out = {}
    for key in SCORE_KEYS:
        try:
            value = int(round(float(scores.get(key, 0))))
        except (TypeError, ValueError):
            value = 0
        out[key] = max(0, min(100, value))
    return out


def _decision_from_score(score: int, blocking_issues: list[Any]) -> str:
    if blocking_issues:
        return "HOLD"
    if score >= 75:
        return "GO"
    if score >= 60:
        return "TEST"
    return "HOLD"


def _fill_country_defaults(out: dict[str, Any], country: dict[str, Any]) -> dict[str, Any]:
    out.setdefault("market_fit", {
        "local_positioning": "",
        "target_segments": [],
        "use_cases": [],
        "demand_analysis": {"summary": "", "facts": [], "inferences": [], "evidence_gaps": []},
        "seasonality": [],
        "market_entry_notes": [],
    })
    out.setdefault("competitor_analysis", {
        "summary": "",
        "competitors": [],
        "competitive_advantages": [],
        "competitive_disadvantages": [],
        "evidence_gaps": [],
    })
    out.setdefault("pricing_analysis", {
        "current_price": None,
        "current_currency": "",
        "recommended_price_range": {"min": None, "max": None, "currency": country["currency"]},
        "pricing_commentary": "",
        "margin_inputs_missing": [],
        "cannot_calculate_reasons": [],
    })
    out.setdefault("creative_fit", {})
    creative = out["creative_fit"]
    creative.setdefault("creative_missing", False)
    creative.setdefault("assets_reviewed", {"cover_images": [], "product_images": [], "videos": []})
    creative.setdefault("cover_image_audit", {"score": 0, "issues": [], "localization_needed": [], "claim_risks": [], "recommended_cover_directions": []})
    creative.setdefault("product_image_audit", {"score": 0, "issues": [], "recommended_image_directions": []})
    creative.setdefault("video_audit", {"score": 0, "timestamp_findings": [], "hook_analysis": "", "proof_gaps": [], "scenes_to_keep": [], "scenes_to_replace_or_reshoot": []})
    creative.setdefault("localized_copy_directions", {"cover_text_direction": [], "hook_direction": [], "cta_direction": [], "language_notes": []})
    creative.setdefault("final_creative_decision", "NO_CREATIVE_PROVIDED" if creative.get("creative_missing") else "LOCALIZE_BEFORE_TEST")
    out.setdefault("landing_page_localization", {
        "localization_difficulty": 0,
        "hero_section": {"title_direction": "", "subtitle_direction": "", "cta_direction": "", "image_direction": ""},
        "sections_needed": [],
        "trust_elements_needed": [],
        "claims_to_avoid_or_rewrite": [],
        "unit_and_currency_notes": [],
        "faq_directions": [],
    })
    out.setdefault("risks", {"claim_risks": [], "compliance_risks": [], "operational_risks": [], "trust_risks": [], "localization_risks": []})
    out.setdefault("recommendations", {
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
    })
    out.setdefault("sources", [])
    out.setdefault("missing_data", [])
    out.setdefault("warnings", [])
    return out


def _failed_country_result(country: dict[str, Any], message: str) -> dict[str, Any]:
    result = _fill_country_defaults({
        "country_code": country["country_code"],
        "country_name": country["country_name"],
        "country_name_zh": country["country_name_zh"],
        "language": country["language"],
        "currency": country["currency"],
        "status": "failed",
        "scores": _normalize_scores({}),
        "decision": {
            "final_decision": "HOLD",
            "confidence": "low",
            "one_sentence_reason": "该国家评估失败，需重试。",
            "why": [],
            "blocking_issues": ["country_evaluation_failed"],
        },
        "error": {"code": "COUNTRY_EVALUATION_FAILED", "message": message[:500]},
        "sources": [],
        "missing_data": [],
        "warnings": [message[:500]],
    }, country)
    result["creative_fit"]["creative_missing"] = True
    result["creative_fit"]["final_creative_decision"] = "NO_CREATIVE_PROVIDED"
    return result


def _build_result_payload(run: dict[str, Any], countries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "evaluation_run_id": run["evaluation_run_id"],
        "product_id": str(run.get("product_id") or ""),
        "status": run.get("status") or "queued",
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "completed_at": run.get("completed_at"),
        "product_snapshot": run.get("product_snapshot") or {},
        "product_facts": run.get("product_facts") or {},
        "summary": run.get("summary") or {},
        "countries": countries or {},
        "frontend": run.get("frontend") or {},
        "metadata": run.get("metadata") or {},
    }


def _unwrap_country_results(countries: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for code, item in (countries or {}).items():
        if isinstance(item, dict) and isinstance(item.get("full_result"), dict):
            out[code] = item["full_result"]
        elif isinstance(item, dict):
            out[code] = item
    return out


def _dedupe(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out
