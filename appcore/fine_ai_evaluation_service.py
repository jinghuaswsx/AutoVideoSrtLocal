"""Service workflow for single-product five-country fine AI evaluation."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from appcore import runner_lifecycle
from appcore import fine_ai_evaluation_model_config as fine_ai_model_config
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
    ExternalCardVideoSnapshotService,
    ProductNotFoundError,
    ProductSnapshotService,
)
from appcore.fine_ai_gemini_client import MODEL, PROVIDER, FineAiGeminiClient

log = logging.getLogger(__name__)

RUN_STATUSES = {"queued", "running", "completed", "partially_completed", "failed", "cancelled"}
COUNTRY_PENDING_STATUSES = {"pending", "waiting", "running", "completed", "failed", "skipped"}
PRODUCTION_COUNTRY_REQUEST_INTERVAL_SECONDS = 0


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
        external_card_video_snapshot_service=None,
        country_request_interval_seconds: float = 0,
        country_request_sleeper=None,
        country_retry_attempts: int = 2,
    ):
        self.repository = repository or FineAiEvaluationRepository()
        self.gemini_client = gemini_client
        self._gemini_client_injected = gemini_client is not None
        self.product_snapshot_service = product_snapshot_service or ProductSnapshotService()
        self.asset_snapshot_service = asset_snapshot_service or AssetSnapshotService()
        self.external_card_video_snapshot_service = (
            external_card_video_snapshot_service or ExternalCardVideoSnapshotService()
        )
        self.country_request_interval_seconds = max(0.0, float(country_request_interval_seconds or 0))
        self.country_request_sleeper = country_request_sleeper or time.sleep
        self.country_retry_attempts = max(1, int(country_retry_attempts or 1))

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
        model_profile: str = fine_ai_model_config.MANUAL_PROFILE,
    ) -> dict[str, Any]:
        country_codes = normalize_country_codes(countries)
        model_config = fine_ai_model_config.get_profile_config(model_profile)
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
        progress = _initial_progress(country_codes, product_snapshot=product_snapshot, asset_snapshot=asset_snapshot)
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
                "model_profile": model_config["profile"],
                "model": model_config["model"],
                "provider": model_config["provider"],
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

    def create_external_link_run(
        self,
        *,
        product_link: str,
        product_name: str = "",
        product_code: str = "",
        link_check_result: dict[str, Any] | None = None,
        force_refresh: bool = True,
        countries: list[str] | None = None,
        locale: str = "zh-CN",
        card_video_object_key: str = "",
        card_video_path: str = "",
        card_video_url: str = "",
        card_video_name: str = "",
        card_video_duration_seconds: Any = None,
        model_profile: str = fine_ai_model_config.MANUAL_PROFILE,
    ) -> dict[str, Any]:
        product_url = str(product_link or "").strip()
        if not product_url:
            raise ValueError("product_link is required")
        model_config = fine_ai_model_config.get_profile_config(model_profile)
        link_check = _sanitize_link_check_result(link_check_result)
        if link_check.get("ok") and link_check.get("selected_link"):
            product_url = str(link_check["selected_link"]).strip()
        country_codes = normalize_country_codes(countries)
        card_video = _external_card_video_metadata(
            object_key=card_video_object_key,
            path=card_video_path,
            url=card_video_url,
            name=card_video_name,
            duration_seconds=card_video_duration_seconds,
        )
        if card_video["object_key"]:
            asset_snapshot = self.external_card_video_snapshot_service.build_snapshot(
                card_video_object_key=card_video["object_key"],
                card_video_path=card_video["path"],
                card_video_url=card_video["url"],
                card_video_name=card_video["name"],
                card_video_duration_seconds=card_video["duration_seconds"],
            )
        else:
            asset_snapshot = _empty_asset_snapshot()
        product_snapshot = _external_product_snapshot(
            product_url=product_url,
            product_name=product_name,
            product_code=product_code,
            videos=asset_snapshot.get("videos") or [],
        )
        has_card_video = bool(asset_snapshot.get("videos"))
        now = _now_iso()
        evaluation_run_id = f"eval_{uuid.uuid4().hex}"
        progress = _initial_progress(
            country_codes,
            product_snapshot=product_snapshot,
            asset_snapshot=asset_snapshot,
            link_check_result=link_check,
        )
        run = {
            "evaluation_run_id": evaluation_run_id,
            "product_id": "0",
            "status": "queued",
            "countries": country_codes,
            "product_snapshot": _with_asset_counts(product_snapshot, asset_snapshot),
            "product_facts": {},
            "summary": {},
            "frontend": {},
            "metadata": {
                "schema_version": "1.0",
                "model_profile": model_config["profile"],
                "model": model_config["model"],
                "provider": model_config["provider"],
                "force_refresh": bool(force_refresh),
                "include_assets": has_card_video,
                "include_videos": has_card_video,
                "locale": locale,
                "source_type": "external_product_link",
                "external_product_link": product_url,
                "external_card_video": card_video,
                "link_check": link_check,
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
            "product_id": "0",
            "status": "queued",
            "countries": country_codes,
            "created_at": now,
            "link_check": link_check,
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
        progress = _ensure_progress(
            run.get("progress"),
            country_codes,
            product_snapshot=product_snapshot,
            asset_snapshot=asset_snapshot,
        )
        metadata = _ensure_model_config_metadata(metadata)
        model_config = _fine_ai_model_config_from_metadata(metadata)
        gemini_client = self._gemini_client_for_metadata(metadata)

        try:
            progress = _mark_progress_step(
                progress,
                "product_fact_extraction",
                "running",
                "开始请求大模型整理商品事实",
                debug=_llm_debug(metadata, {
                    "Product URL": product_snapshot.get("product_url") or product_snapshot.get("landing_page_url") or "",
                    "Country Count": len(country_codes),
                }),
                provider=metadata.get("provider") or PROVIDER,
                model_id=metadata.get("model") or MODEL,
            )
            self.repository.update_run(
                evaluation_run_id,
                status="running",
                started_at=_now_iso(),
                progress=_progress(country_codes, "product_fact_extraction", base_progress=progress),
            )
            product_facts = gemini_client.generate_product_facts(
                product_snapshot=product_snapshot,
                countries=country_configs(country_codes),
            )
            call_trace = self._call_trace(product_facts)
            validate_json_schema(product_facts, PRODUCT_FACTS_SCHEMA)
            metadata = self._merge_call_metadata(metadata, "product_facts")
            progress = _mark_progress_step(
                progress,
                "product_fact_extraction",
                "completed",
                "商品事实整理完成",
                debug=[
                    *_llm_debug(metadata, {
                        "Category": product_facts.get("category_detected") or "-",
                        "Missing Data": len(product_facts.get("missing_data") or []),
                    }),
                    *_usage_debug(self._call_metadata()),
                ],
                llm_trace=call_trace,
            )
            self.repository.update_run(
                evaluation_run_id,
                product_facts=product_facts,
                metadata=metadata,
                progress=_progress(country_codes, "product_fact_extraction", base_progress=progress),
            )
        except Exception as exc:
            log.exception("fine AI product fact extraction failed: run=%s", evaluation_run_id)
            run_errors.append({"stage": "product_fact_extraction", "message": str(exc)[:500]})
            metadata["run_errors"] = run_errors
            progress = _mark_progress_step(
                progress,
                "product_fact_extraction",
                "failed",
                f"商品事实整理失败：{str(exc)[:160]}",
                level="error",
                debug=_llm_debug(metadata, {"Error": str(exc)[:500]}),
                llm_trace=self._call_trace(error=exc),
            )
            self.repository.update_run(
                evaluation_run_id,
                status="failed",
                metadata=metadata,
                failed_at=_now_iso(),
                error_message=str(exc)[:500],
                progress=_progress(country_codes, "failed", base_progress=progress),
            )
            return self.get_result(product_id, evaluation_run_id)

        product_facts = self._require_run(evaluation_run_id).get("product_facts") or {}
        for index, code in enumerate(country_codes):
            country = get_country_config(code)
            step_key = _country_step_key(code)
            if index > 0 and self.country_request_interval_seconds > 0:
                wait_label = _format_seconds(self.country_request_interval_seconds)
                progress = _mark_progress_step(
                    progress,
                    step_key,
                    "waiting",
                    f"{code} 等待 {wait_label} 后再请求大模型，避免触发频率限制",
                    debug=[
                        {"label": "Country", "value": code},
                        {"label": "Wait", "value": wait_label},
                    ],
                )
                self.repository.update_run(
                    evaluation_run_id,
                    status="running",
                    progress=_progress(
                        country_codes,
                        f"country_wait_{code}",
                        waiting_country=code,
                        completed_steps=_completed_step_count(progress),
                        completed_countries=completed_codes,
                        failed_countries=failed_codes,
                        base_progress=progress,
                    ),
                )
                self.country_request_sleeper(self.country_request_interval_seconds)
            progress = _mark_progress_step(
                progress,
                step_key,
                "running",
                f"{code} 开始请求大模型评估",
                debug=_llm_debug(metadata, {
                    "Country": code,
                    "Language": country.get("language") or "",
                    "Currency": country.get("currency") or "",
                    "Images": len(asset_snapshot.get("product_images") or []) + len(asset_snapshot.get("cover_images") or []),
                    "Videos": len(asset_snapshot.get("videos") or []),
                }),
                provider=metadata.get("provider") or PROVIDER,
                model_id=metadata.get("model") or MODEL,
            )
            self.repository.update_run(
                evaluation_run_id,
                status="running",
                progress=_progress(
                    country_codes,
                    f"country_evaluation_{code}",
                    running_country=code,
                    completed_steps=_completed_step_count(progress),
                    completed_countries=completed_codes,
                    failed_countries=failed_codes,
                    base_progress=progress,
                ),
            )
            for attempt in range(1, self.country_retry_attempts + 1):
                try:
                    result = gemini_client.generate_country_evaluation(
                        product_snapshot=product_snapshot,
                        product_facts=product_facts,
                        country=country,
                        asset_snapshot=asset_snapshot,
                        asset_paths=list(asset_snapshot.get("asset_paths") or []),
                    )
                    call_trace = self._call_trace(result)
                    result = _normalize_country_result(result, country, asset_snapshot)
                    validate_json_schema(result, COUNTRY_EVALUATION_SCHEMA)
                    completed_codes.append(code)
                    countries[code] = result
                    call_metadata = self._call_metadata()
                    call_metadata["attempts"] = attempt
                    progress = _mark_progress_step(
                        progress,
                        step_key,
                        "completed",
                        f"{code} 评估完成：{(result.get('decision') or {}).get('final_decision') or '-'} / {((result.get('scores') or {}).get('overall_score'))}",
                        debug=[
                            *_country_result_debug(result),
                            *_usage_debug(call_metadata),
                        ],
                        llm_trace=call_trace,
                    )
                    self.repository.upsert_country(
                        evaluation_run_id,
                        code,
                        {
                            "product_id": product_id,
                            "status": "completed",
                            "full_result": result,
                            "metadata": call_metadata,
                            "raw_response": _country_raw_response(call_trace, call_metadata),
                        },
                    )
                    break
                except Exception as exc:
                    call_metadata = self._call_metadata()
                    call_metadata["attempts"] = attempt
                    call_trace = self._call_trace(countries.get(code), error=exc)
                    if attempt < self.country_retry_attempts:
                        log.warning(
                            "fine AI country evaluation retrying: run=%s country=%s attempt=%s",
                            evaluation_run_id,
                            code,
                            attempt,
                            exc_info=True,
                        )
                        progress = _mark_progress_step(
                            progress,
                            step_key,
                            "running",
                            f"{code} 第 {attempt} 次失败，准备重试：{str(exc)[:160]}",
                            level="warning",
                            debug=[
                                {"label": "Country", "value": code},
                                {"label": "Attempt", "value": attempt},
                                {"label": "Error", "value": str(exc)[:500]},
                                *_usage_debug(call_metadata),
                            ],
                            llm_trace=call_trace,
                        )
                        self.repository.update_run(
                            evaluation_run_id,
                            status="running",
                            progress=_progress(
                                country_codes,
                                f"country_evaluation_{code}",
                                running_country=code,
                                completed_steps=_completed_step_count(progress),
                                completed_countries=completed_codes,
                                failed_countries=failed_codes,
                                base_progress=progress,
                            ),
                        )
                        continue
                    log.exception("fine AI country evaluation failed: run=%s country=%s", evaluation_run_id, code)
                    failed_codes.append(code)
                    failed = _failed_country_result(country, str(exc))
                    countries[code] = failed
                    progress = _mark_progress_step(
                        progress,
                        step_key,
                        "failed",
                        f"{code} 评估失败：{str(exc)[:160]}",
                        level="error",
                        debug=[
                            {"label": "Country", "value": code},
                            {"label": "Attempt", "value": attempt},
                            {"label": "Error", "value": str(exc)[:500]},
                            *_usage_debug(call_metadata),
                        ],
                        llm_trace=call_trace,
                    )
                    self.repository.upsert_country(
                        evaluation_run_id,
                        code,
                        {
                            "product_id": product_id,
                            "status": "failed",
                            "full_result": failed,
                            "metadata": call_metadata,
                            "raw_response": _country_raw_response(call_trace, call_metadata),
                            "error_message": str(exc)[:500],
                        },
                    )
            self.repository.update_run(
                evaluation_run_id,
                status="running",
                progress=_progress(
                    country_codes,
                    f"country_evaluation_{code}",
                    completed_steps=_completed_step_count(progress),
                    completed_countries=completed_codes,
                    failed_countries=failed_codes,
                    base_progress=progress,
                ),
            )

        countries = _unwrap_country_results(self.repository.list_countries(evaluation_run_id)) or countries
        progress = _mark_progress_step(
            progress,
            "summary",
            "running",
            "开始汇总五国评估结果",
            debug=[
                {"label": "Completed Countries", "value": ", ".join(completed_codes) or "-"},
                {"label": "Failed Countries", "value": ", ".join(failed_codes) or "-"},
            ],
        )
        self.repository.update_run(
            evaluation_run_id,
            status="running",
            progress=_progress(
                country_codes,
                "summary",
                completed_steps=_completed_step_count(progress),
                completed_countries=completed_codes,
                failed_countries=failed_codes,
                base_progress=progress,
            ),
        )
        summary = build_summary(countries)
        frontend = build_frontend(summary, countries)
        metadata.update({
            "model_profile": model_config["profile"],
            "model": model_config["model"],
            "provider": model_config["provider"],
            "countries_requested": country_codes,
            "countries_completed": completed_codes,
            "countries_failed": failed_codes,
            "run_errors": run_errors,
            "data_quality": metadata.get("data_quality") or _data_quality(product_snapshot, asset_snapshot),
        })
        final_status = "completed" if not failed_codes else "partially_completed"
        if not completed_codes and failed_codes:
            final_status = "failed"
        progress = _mark_progress_step(
            progress,
            "summary",
            "completed" if final_status in {"completed", "partially_completed"} else "failed",
            f"汇总完成：{final_status}",
            level="info" if final_status in {"completed", "partially_completed"} else "error",
            debug=[
                {"label": "Run Status", "value": final_status},
                {"label": "Overall Recommendation", "value": summary.get("overall_recommendation") or "-"},
                {"label": "Completed Countries", "value": len(completed_codes)},
                {"label": "Failed Countries", "value": len(failed_codes)},
            ],
        )
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
                "summary",
                completed_steps=_completed_step_count(progress),
                completed_countries=completed_codes,
                failed_countries=failed_codes,
                base_progress=progress,
            ),
        )
        return self.get_result(product_id, evaluation_run_id)

    def get_status(self, product_id: int | str, evaluation_run_id: str) -> dict[str, Any]:
        run = self._require_run(evaluation_run_id)
        self._assert_product(run, product_id)
        metadata = run.get("metadata") or {}
        return {
            "evaluation_run_id": run["evaluation_run_id"],
            "product_id": str(run.get("product_id") or ""),
            "status": run.get("status") or "queued",
            "progress": _fresh_progress_elapsed(
                run.get("progress") or _initial_progress(run.get("countries") or DEFAULT_COUNTRY_CODES)
            ),
            "product_snapshot": run.get("product_snapshot") or {},
            "metadata": _status_context_metadata(metadata),
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

    def get_latest_external_link_result(
        self,
        product_link: str,
        *,
        card_video_object_key: str = "",
        card_video_path: str = "",
        card_video_url: str = "",
        card_video_name: str = "",
    ) -> dict[str, Any]:
        link = str(product_link or "").strip()
        if not link:
            raise ValueError("product_link is required")
        run = self.repository.get_latest_external_link_run(
            link,
            card_video_object_key=str(card_video_object_key or "").strip(),
            card_video_path=str(card_video_path or "").strip(),
            card_video_url=str(card_video_url or "").strip(),
            card_video_name=str(card_video_name or "").strip(),
        )
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
        metadata = run.get("metadata") or {}
        completed_codes = [item for item in metadata.get("countries_completed") or [] if item != code]
        failed_codes = [item for item in metadata.get("countries_failed") or [] if item != code]
        progress = _ensure_progress(
            run.get("progress"),
            run.get("countries") or DEFAULT_COUNTRY_CODES,
            product_snapshot=run.get("product_snapshot") or {},
            asset_snapshot=metadata.get("asset_snapshot") or {},
        )
        progress = _mark_progress_step(
            progress,
            _country_step_key(code),
            "running",
            f"{code} 手动重跑开始",
            debug=[{"label": "Country", "value": code}],
        )
        self.repository.update_run(
            evaluation_run_id,
            status="running",
            progress=_progress(
                run.get("countries") or DEFAULT_COUNTRY_CODES,
                f"country_evaluation_{code}",
                running_country=code,
                completed_countries=completed_codes,
                failed_countries=failed_codes,
                base_progress=progress,
            ),
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
        if metadata.get("source_type") == "external_product_link":
            asset_snapshot = metadata.get("asset_snapshot") or _empty_asset_snapshot()
        else:
            asset_snapshot = self.asset_snapshot_service.build_snapshot(
                product_id,
                include_assets=include_assets,
                include_videos=include_videos,
            )
        metadata["asset_snapshot"] = asset_snapshot
        metadata = _ensure_model_config_metadata(metadata)
        gemini_client = self._gemini_client_for_metadata(metadata)
        product_facts = run.get("product_facts") or gemini_client.generate_product_facts(
            product_snapshot=product_snapshot,
            countries=country_configs(run.get("countries") or DEFAULT_COUNTRY_CODES),
        )
        country = get_country_config(country_code)
        run_country_codes = normalize_country_codes(run.get("countries") or DEFAULT_COUNTRY_CODES)
        progress = _ensure_progress(
            run.get("progress"),
            run_country_codes,
            product_snapshot=product_snapshot,
            asset_snapshot=asset_snapshot,
        )
        progress = _mark_progress_step(
            progress,
            _country_step_key(country_code),
            "running",
            f"{country_code} 手动重跑正在请求大模型",
            debug=_llm_debug(metadata, {
                "Country": country_code,
                "Language": country.get("language") or "",
                "Currency": country.get("currency") or "",
            }),
            provider=metadata.get("provider") or PROVIDER,
            model_id=metadata.get("model") or MODEL,
        )
        try:
            result = gemini_client.generate_country_evaluation(
                product_snapshot=product_snapshot,
                product_facts=product_facts,
                country=country,
                asset_snapshot=asset_snapshot,
                asset_paths=list(asset_snapshot.get("asset_paths") or []),
            )
            call_trace = self._call_trace(result)
            result = _normalize_country_result(result, country, asset_snapshot)
            call_metadata = self._call_metadata()
            progress = _mark_progress_step(
                progress,
                _country_step_key(country_code),
                "completed",
                f"{country_code} 手动重跑完成",
                debug=[
                    *_country_result_debug(result),
                    *_usage_debug(call_metadata),
                ],
                llm_trace=call_trace,
            )
            self.repository.upsert_country(
                evaluation_run_id,
                country_code,
                {
                    "product_id": str(product_id),
                    "status": "completed",
                    "full_result": result,
                    "metadata": call_metadata,
                    "raw_response": _country_raw_response(call_trace, call_metadata),
                },
            )
        except Exception as exc:
            failed = _failed_country_result(country, str(exc))
            call_metadata = self._call_metadata()
            call_trace = self._call_trace(failed, error=exc)
            progress = _mark_progress_step(
                progress,
                _country_step_key(country_code),
                "failed",
                f"{country_code} 手动重跑失败：{str(exc)[:160]}",
                level="error",
                debug=[
                    {"label": "Country", "value": country_code},
                    {"label": "Error", "value": str(exc)[:500]},
                    *_usage_debug(call_metadata),
                ],
                llm_trace=call_trace,
            )
            self.repository.upsert_country(
                evaluation_run_id,
                country_code,
                {
                    "product_id": str(product_id),
                    "status": "failed",
                    "full_result": failed,
                    "metadata": call_metadata,
                    "raw_response": _country_raw_response(call_trace, call_metadata),
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
        progress = _mark_progress_step(
            progress,
            "summary",
            "completed",
            "单国家重跑后汇总完成",
            debug=[
                {"label": "Completed Countries", "value": len(completed_codes)},
                {"label": "Failed Countries", "value": len(failed_codes)},
            ],
        )
        self.repository.update_run(
            evaluation_run_id,
            status="completed" if not failed_codes else "partially_completed",
            summary=summary,
            frontend=frontend,
            metadata=metadata,
            progress=_progress(
                run_country_codes,
                "summary",
                completed_steps=_completed_step_count(progress),
                completed_countries=completed_codes,
                failed_countries=failed_codes,
                base_progress=progress,
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

    def _gemini_client_for_metadata(self, metadata: dict[str, Any]):
        if self._gemini_client_injected:
            return self.gemini_client
        config = _fine_ai_model_config_from_metadata(metadata)
        self.gemini_client = FineAiGeminiClient(provider=config["provider"])
        return self.gemini_client

    def _call_metadata(self) -> dict[str, Any]:
        return dict(getattr(self.gemini_client, "last_call_metadata", {}) or {})

    def _call_trace(self, parsed_json: dict[str, Any] | None = None, error: Exception | None = None) -> dict[str, Any]:
        trace = dict(getattr(self.gemini_client, "last_call_trace", {}) or {})
        if not trace:
            metadata = self._call_metadata()
            trace = {
                "provider": metadata.get("provider") or PROVIDER,
                "model_id": metadata.get("model") or metadata.get("model_id") or MODEL,
                "request": {"summary": {}, "system_prompt": "", "prompt": "", "payload": {}},
                "response": {
                    "summary": dict(metadata.get("usage") or {}),
                    "parsed_json": parsed_json or {},
                    "raw_payload": {},
                },
            }
        if parsed_json is not None:
            response = dict(trace.get("response") or {})
            response.setdefault("parsed_json", parsed_json)
            trace["response"] = response
        if error is not None:
            trace.setdefault("error", {"type": type(error).__name__, "message": str(error)[:1000]})
        return trace

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
        _SERVICE = FineAiEvaluationService(
            country_request_interval_seconds=PRODUCTION_COUNTRY_REQUEST_INTERVAL_SECONDS
        )
    return _SERVICE


def _fine_ai_model_config_from_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    profile = str((metadata or {}).get("model_profile") or fine_ai_model_config.MANUAL_PROFILE).strip()
    provider = str((metadata or {}).get("provider") or "").strip()
    if provider in fine_ai_model_config.ALLOWED_PROVIDERS:
        return fine_ai_model_config.resolve_config(profile=profile, provider=provider)
    return fine_ai_model_config.get_profile_config(
        profile if profile in fine_ai_model_config.PROFILES else fine_ai_model_config.MANUAL_PROFILE
    )


def _ensure_model_config_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    out = dict(metadata or {})
    config = _fine_ai_model_config_from_metadata(out)
    out["model_profile"] = config["profile"]
    out["provider"] = config["provider"]
    out["model"] = config["model"]
    return out


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_seconds(seconds: float) -> str:
    value = float(seconds or 0)
    if value.is_integer():
        return f"{int(value)} 秒"
    return f"{value:.1f} 秒"


def _raw_response_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    raw = (metadata or {}).get("raw_response")
    return dict(raw) if isinstance(raw, dict) else {}


def _country_raw_response(call_trace: dict[str, Any], call_metadata: dict[str, Any]) -> dict[str, Any]:
    trace_raw = _trace_raw_response(call_trace)
    metadata_raw = _raw_response_from_metadata(call_metadata)
    if trace_raw and metadata_raw:
        merged = {**trace_raw, "parse_error_summary": metadata_raw}
        for key in ("text_preview", "text_length", "json_parse_error", "usage_log_id"):
            if key in metadata_raw and key not in merged:
                merged[key] = metadata_raw[key]
        return merged
    return trace_raw or metadata_raw


def _initial_progress(
    country_codes: list[str] | tuple[str, ...],
    *,
    product_snapshot: dict[str, Any] | None = None,
    asset_snapshot: dict[str, Any] | None = None,
    link_check_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    has_link_check = bool(link_check_result)
    progress = {
        "started_at": _now_iso(),
        "steps": _progress_steps(country_codes, include_link_check=has_link_check),
        "events": [],
    }
    if has_link_check:
        progress = _mark_progress_step(
            progress,
            "product_link_check",
            "completed" if link_check_result.get("ok") else "failed",
            str(link_check_result.get("message") or "商品链接检测完成"),
            debug=_link_check_debug(link_check_result),
            event=False,
        )
    progress = _mark_progress_step(
        progress,
        "data_preparation",
        "completed",
        "数据准备完成，等待商品事实整理",
        debug=_data_preparation_debug(product_snapshot or {}, asset_snapshot or {}, country_codes),
        event=False,
    )
    return _progress(country_codes, "product_fact_extraction", base_progress=progress)


def _progress(
    country_codes: list[str] | tuple[str, ...],
    current_step: str,
    *,
    running_country: str | None = None,
    waiting_country: str | None = None,
    completed_steps: int = 0,
    completed_countries: list[str] | None = None,
    failed_countries: list[str] | None = None,
    base_progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    completed = set(completed_countries or [])
    failed = set(failed_countries or [])
    statuses = {}
    for code in country_codes:
        if code in completed:
            statuses[code] = "completed"
        elif code in failed:
            statuses[code] = "failed"
        elif code == waiting_country:
            statuses[code] = "waiting"
        elif code == running_country:
            statuses[code] = "running"
        else:
            statuses[code] = "pending"
    progress = dict(base_progress or {})
    if "steps" not in progress:
        progress["steps"] = _progress_steps(country_codes)
    if "events" not in progress:
        progress["events"] = []
    completed_from_steps = _completed_step_count(progress)
    started_at = progress.get("started_at") or _now_iso()
    progress.update({
        "total_steps": len(progress.get("steps") or []),
        "completed_steps": int(completed_steps or completed_from_steps),
        "current_step": current_step,
        "current_country": running_country or waiting_country or "",
        "countries": statuses,
        "started_at": started_at,
        "elapsed_seconds": _elapsed_seconds(started_at),
    })
    return progress


def _ensure_progress(
    progress: dict[str, Any] | None,
    country_codes: list[str] | tuple[str, ...],
    *,
    product_snapshot: dict[str, Any],
    asset_snapshot: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(progress, dict) or not progress.get("steps"):
        return _initial_progress(country_codes, product_snapshot=product_snapshot, asset_snapshot=asset_snapshot)
    out = dict(progress)
    out.setdefault("steps", _progress_steps(country_codes))
    out.setdefault("events", [])
    out.setdefault("started_at", _now_iso())
    return out


def _fresh_progress_elapsed(progress: dict[str, Any]) -> dict[str, Any]:
    out = dict(progress or {})
    out["elapsed_seconds"] = _elapsed_seconds(out.get("started_at"))
    return out


def _progress_steps(
    country_codes: list[str] | tuple[str, ...],
    *,
    include_link_check: bool = False,
) -> list[dict[str, Any]]:
    steps = [
        _step("data_preparation", "数据准备", "准备商品链接、商品快照、素材数量和目标国家"),
        _step("product_fact_extraction", "商品事实整理", "抽取跨国家共享的商品事实", llm=True),
    ]
    if include_link_check:
        steps.insert(0, _step("product_link_check", "商品链接检测", "检测当前商品链接，必要时从明空候选链接中选择可访问链接"))
    for code in country_codes:
        country = get_country_config(code)
        steps.append(_step(_country_step_key(code), f"{code} {country['country_name_zh']}", "单国家市场、素材、落地页与风险评估", llm=True))
    steps.append(_step("summary", "汇总结果", "聚合五国结论和下一步动作"))
    return steps


def _sanitize_link_check_result(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    candidates = []
    for item in value.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        candidates.append({
            "url": str(item.get("url") or "")[:1024],
            "source": str(item.get("source") or "")[:80],
            "ok": bool(item.get("ok")),
            "http_status": (
                int(item["http_status"]) if item.get("http_status") is not None else None
            ),
            "error": (str(item.get("error") or "")[:240] or None),
            "elapsed_ms": (
                int(item["elapsed_ms"]) if item.get("elapsed_ms") is not None else None
            ),
            "used": bool(item.get("used")),
        })
    return {
        "ok": bool(value.get("ok")),
        "status": str(value.get("status") or "")[:80],
        "original_link": str(value.get("original_link") or "")[:1024],
        "selected_link": str(value.get("selected_link") or "")[:1024],
        "candidate_count": int(value.get("candidate_count") or len(candidates)),
        "candidates": candidates[:12],
        "checked_at": str(value.get("checked_at") or "")[:80],
        "message": str(value.get("message") or "")[:240],
    }


def _link_check_debug(link_check: dict[str, Any]) -> list[dict[str, Any]]:
    failed = [
        f"{item.get('http_status') or '-'} {item.get('error') or ''}".strip()
        for item in link_check.get("candidates") or []
        if not item.get("ok")
    ]
    return [
        {"label": "原始链接", "value": link_check.get("original_link") or "-"},
        {"label": "最终链接", "value": link_check.get("selected_link") or "-"},
        {"label": "检测结果", "value": link_check.get("status") or "-"},
        {"label": "候选数量", "value": link_check.get("candidate_count") or 0},
        {"label": "失败摘要", "value": " | ".join(failed[:3]) or "-"},
    ]


def _step(key: str, title: str, description: str, *, llm: bool = False) -> dict[str, Any]:
    step = {
        "key": key,
        "title": title,
        "description": description,
        "status": "pending",
        "message": "等待执行",
        "started_at": None,
        "completed_at": None,
        "logs": [],
        "debug": [],
    }
    if llm:
        step["provider"] = PROVIDER
        step["model_id"] = MODEL
    return step


def _country_step_key(code: str) -> str:
    return f"country_{code}"


def _mark_progress_step(
    progress: dict[str, Any],
    step_key: str,
    status: str,
    message: str,
    *,
    level: str = "info",
    debug: list[dict[str, Any]] | None = None,
    llm_trace: dict[str, Any] | None = None,
    provider: str | None = None,
    model_id: str | None = None,
    event: bool = True,
) -> dict[str, Any]:
    now = _now_iso()
    out = dict(progress or {})
    steps = [dict(step) for step in out.get("steps") or []]
    found = False
    for step in steps:
        if step.get("key") != step_key:
            continue
        found = True
        previous_status = step.get("status")
        step["status"] = status
        step["message"] = message
        if status in {"running", "waiting"} and not step.get("started_at"):
            step["started_at"] = now
        if status in {"completed", "failed", "skipped"}:
            if not step.get("started_at"):
                step["started_at"] = now
            step["completed_at"] = now
        if debug is not None:
            step["debug"] = _compact_debug(debug)
        if _is_llm_step_key(step_key):
            trace_provider = (llm_trace or {}).get("provider") or provider or step.get("provider") or PROVIDER
            trace_model = (
                (llm_trace or {}).get("model_id")
                or (llm_trace or {}).get("model")
                or model_id
                or step.get("model_id")
                or MODEL
            )
            step["provider"] = str(trace_provider or "")[:120]
            step["model_id"] = str(trace_model or "")[:240]
        if llm_trace is not None:
            step["llm_trace"] = llm_trace
        logs = list(step.get("logs") or [])
        if previous_status != status or message:
            logs.append({"ts": now, "level": level, "message": message})
        step["logs"] = logs[-20:]
        break
    if not found:
        step = _step(step_key, step_key, "", llm=_is_llm_step_key(step_key))
        steps.append(step)
        return _mark_progress_step(
            {**out, "steps": steps},
            step_key,
            status,
            message,
            level=level,
            debug=debug,
            llm_trace=llm_trace,
            provider=provider,
            model_id=model_id,
            event=event,
        )
    out["steps"] = steps
    if event:
        events = list(out.get("events") or [])
        events.append({"ts": now, "level": level, "step_key": step_key, "message": message})
        out["events"] = events[-120:]
    out["elapsed_seconds"] = _elapsed_seconds(out.get("started_at"))
    return out


def _completed_step_count(progress: dict[str, Any]) -> int:
    return sum(1 for step in progress.get("steps") or [] if step.get("status") in {"completed", "failed", "skipped"})


def _is_llm_step_key(step_key: str) -> bool:
    key = str(step_key or "")
    return key == "product_fact_extraction" or key.startswith("country_")


def _trace_raw_response(trace: dict[str, Any]) -> dict[str, Any]:
    response = trace.get("response") if isinstance(trace, dict) else {}
    if isinstance(response, dict) and isinstance(response.get("raw_payload"), dict):
        return response["raw_payload"]
    return {}


def _elapsed_seconds(started_at: str | None) -> int:
    if not started_at:
        return 0
    try:
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, int((datetime.now(UTC) - started).total_seconds()))


def _compact_debug(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = item.get("value")
        if not label:
            continue
        if isinstance(value, (dict, list)):
            value_text = str(value)[:240]
        else:
            value_text = str(value if value is not None else "")[:240]
        out.append({"label": label, "value": value_text})
    return out


def _data_preparation_debug(
    product_snapshot: dict[str, Any],
    asset_snapshot: dict[str, Any],
    country_codes: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    return [
        {"label": "Product URL", "value": product_snapshot.get("product_url") or product_snapshot.get("landing_page_url") or "-"},
        {"label": "Product Name", "value": product_snapshot.get("product_name") or "-"},
        {"label": "Images", "value": len(asset_snapshot.get("product_images") or []) + len(asset_snapshot.get("cover_images") or [])},
        {"label": "Videos", "value": len(asset_snapshot.get("videos") or [])},
        {"label": "Countries", "value": " -> ".join(country_codes)},
    ]


def _llm_debug(metadata: dict[str, Any], extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    debug = [
        {"label": "Provider", "value": metadata.get("provider") or PROVIDER},
        {"label": "Model", "value": metadata.get("model") or MODEL},
    ]
    for label, value in (extra or {}).items():
        debug.append({"label": label, "value": value})
    return debug


def _usage_debug(call_metadata: dict[str, Any]) -> list[dict[str, Any]]:
    usage = call_metadata.get("usage") or {}
    return [
        {"label": "Input Tokens", "value": usage.get("input_tokens") or usage.get("prompt_tokens") or "-"},
        {"label": "Output Tokens", "value": usage.get("output_tokens") or usage.get("completion_tokens") or "-"},
    ]


def _country_result_debug(result: dict[str, Any]) -> list[dict[str, Any]]:
    scores = result.get("scores") or {}
    decision = result.get("decision") or {}
    return [
        {"label": "Country", "value": result.get("country_code") or "-"},
        {"label": "Score", "value": scores.get("overall_score")},
        {"label": "Decision", "value": decision.get("final_decision") or "-"},
        {"label": "Confidence", "value": decision.get("confidence") or "-"},
        {"label": "Missing Data", "value": len(result.get("missing_data") or [])},
        {"label": "Sources", "value": len(result.get("sources") or [])},
    ]


def _with_asset_counts(product_snapshot: dict[str, Any], asset_snapshot: dict[str, Any]) -> dict[str, Any]:
    out = dict(product_snapshot or {})
    out["asset_count"] = {
        "images": len(asset_snapshot.get("product_images") or []),
        "videos": len(asset_snapshot.get("videos") or []),
    }
    return out


def _empty_asset_snapshot() -> dict[str, Any]:
    return {
        "cover_images": [],
        "product_images": [],
        "videos": [],
        "asset_paths": [],
        "warnings": [],
    }


def _external_card_video_metadata(
    *,
    object_key: str = "",
    path: str = "",
    url: str = "",
    name: str = "",
    duration_seconds: Any = None,
) -> dict[str, Any]:
    return {
        "object_key": str(object_key or "").strip(),
        "path": str(path or "").strip(),
        "url": str(url or "").strip(),
        "name": str(name or "").strip(),
        "duration_seconds": duration_seconds,
    }


def _external_product_snapshot(
    *,
    product_url: str,
    product_name: str = "",
    product_code: str = "",
    videos: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean_url = str(product_url or "").strip()
    clean_name = str(product_name or "").strip()
    clean_code = str(product_code or "").strip()
    clean_videos = list(videos or [])
    return {
        "product_id": "0",
        "source_type": "external_product_link",
        "product_name": clean_name,
        "brand": "",
        "category": "",
        "product_url": clean_url,
        "landing_page_url": clean_url,
        "description": "",
        "sku_options": [],
        "price": None,
        "currency": "",
        "compare_at_price": None,
        "inventory_status": "",
        "dimensions": {"length_cm": None, "width_cm": None, "height_cm": None},
        "weight": None,
        "materials": [],
        "claims": [],
        "selling_points": [],
        "usage_scenarios": [],
        "target_customers": [],
        "cost": None,
        "shipping_cost_by_country": {},
        "delivery_days_by_country": {},
        "return_policy": "",
        "product_images": [],
        "cover_images": [],
        "videos": clean_videos,
        "existing_ad_copy": [],
        "existing_landing_page_copy": [],
        "product_code": clean_code,
        "sku_count": 0,
        "asset_count": {"images": 0, "videos": len(clean_videos)},
    }


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
        "progress": run.get("progress") or _initial_progress(run.get("countries") or DEFAULT_COUNTRY_CODES),
    }


def _status_context_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_product_link": metadata.get("external_product_link") or "",
        "external_card_video": metadata.get("external_card_video") or {},
        "asset_snapshot": metadata.get("asset_snapshot") or {},
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
