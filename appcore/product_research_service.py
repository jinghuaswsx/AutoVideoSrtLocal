"""Service layer for single-product AI research.

Handles CRUD, pipeline orchestration, aggregation, and frontend mapping.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from appcore import runner_lifecycle
from appcore.db import execute, query, query_one
from appcore.product_research_config import (
    DEFAULT_COUNTRY_CODES,
    PIPELINE_STEPS,
    country_configs,
    decision_from_score,
    get_country_config,
    normalize_country_codes,
)
from appcore.product_research_exchange_rate import get_exchange_rate_provider
from appcore.product_research_gemini_client import MODEL, PROVIDER, ProductResearchGeminiClient
from appcore.product_research_schemas import (
    COUNTRY_EVALUATION_SCHEMA,
    SCORE_KEYS,
    validate_json_schema,
    validate_scores,
)

log = logging.getLogger(__name__)

RUN_STATUSES = {"queued", "running", "completed", "partially_completed", "failed", "cancelled"}
COUNTRY_STATUSES = {"pending", "running", "completed", "failed", "skipped"}


class ProductResearchError(RuntimeError):
    code = "PRODUCT_RESEARCH_ERROR"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class ProductResearchNotFound(ProductResearchError):
    code = "RESEARCH_RUN_NOT_FOUND"


class ProductResearchService:
    def __init__(self, *, gemini_client=None):
        self.gemini_client = gemini_client or ProductResearchGeminiClient()

    # ── CRUD ──────────────────────────────────────────────

    def create_run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        research_run_id = f"research_{uuid.uuid4().hex}"
        now = _now_iso()
        input_snapshot = _sanitize_input(input_data)
        country_codes = normalize_country_codes(list(input_snapshot.get("shipping_inputs", {}).get("shipping_cost_by_country", {}).keys() or DEFAULT_COUNTRY_CODES))

        run = {
            "research_run_id": research_run_id,
            "status": "queued",
            "input_snapshot_json": json.dumps(input_snapshot, ensure_ascii=False),
            "pipeline_cards_json": json.dumps(_initial_cards(), ensure_ascii=False),
            "product_facts_json": None,
            "media_understanding_json": None,
            "pricing_strategy_json": None,
            "summary_json": None,
            "frontend_json": None,
            "metadata_json": json.dumps({
                "schema_version": "1.0",
                "model": MODEL,
                "provider": PROVIDER,
                "countries": country_codes,
                "countries_completed": [],
                "countries_failed": [],
                "run_errors": [],
                "token_usage": {},
            }, ensure_ascii=False),
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }
        execute(
            """INSERT INTO product_research_runs
            (research_run_id, status, input_snapshot_json, pipeline_cards_json, metadata_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (research_run_id, "queued", run["input_snapshot_json"], run["pipeline_cards_json"], run["metadata_json"], now, now),
        )
        return {
            "research_run_id": research_run_id,
            "status": "queued",
            "countries": country_codes,
            "created_at": now,
        }

    def start_run_async(self, research_run_id: str) -> bool:
        return runner_lifecycle.start_tracked_thread(
            project_type="product_research",
            task_id=str(research_run_id),
            target=self.run_pipeline,
            args=(research_run_id,),
            daemon=True,
            stage="run",
            interrupt_policy="cautious",
        )

    def get_status(self, research_run_id: str) -> dict[str, Any]:
        run = self._require_run(research_run_id)
        cards = _load_json(run.get("pipeline_cards_json"), [])
        metadata = _load_json(run.get("metadata_json"), {})
        country_codes = metadata.get("countries") or list(DEFAULT_COUNTRY_CODES)
        countries_status: dict[str, str] = {}
        for code in country_codes:
            cr = query_one(
                "SELECT status FROM product_research_country_results WHERE research_run_id = %s AND country_code = %s",
                (research_run_id, code),
            )
            countries_status[code] = cr["status"] if cr else "pending"

        completed = sum(1 for c in cards if c.get("status") in ("completed", "failed", "skipped"))
        current_card = next((c for c in cards if c.get("status") == "running"), None)

        return {
            "research_run_id": research_run_id,
            "status": run.get("status") or "queued",
            "progress": {
                "total_steps": len(cards),
                "completed_steps": completed,
                "current_step": current_card["card_id"] if current_card else "",
                "step_cards": cards,
                "countries": countries_status,
            },
            "created_at": _iso(run.get("created_at")),
            "updated_at": _iso(run.get("updated_at")),
        }

    def get_result(self, research_run_id: str) -> dict[str, Any]:
        run = self._require_run(research_run_id)
        metadata = _load_json(run.get("metadata_json"), {})
        country_codes = metadata.get("countries") or list(DEFAULT_COUNTRY_CODES)
        countries: dict[str, dict[str, Any]] = {}
        rows = query(
            "SELECT * FROM product_research_country_results WHERE research_run_id = %s ORDER BY country_code",
            (research_run_id,),
        )
        for row in rows:
            code = row["country_code"]
            countries[code] = _load_json(row.get("full_result_json"), {
                "country_code": code,
                "country_name": row.get("country_name", ""),
                "country_name_zh": row.get("country_name_zh", ""),
                "status": row.get("status", "pending"),
                "scores": {},
                "decision": {"final_decision": "HOLD", "confidence": "low", "one_sentence_reason": "", "why": [], "blocking_issues": []},
                "market_fit": {},
                "competitor_pricing": {"summary": "", "competitors": [], "price_band": {}, "evidence_gaps": []},
                "pricing_strategy": {},
                "shipping_strategy": {},
                "short_video_fit": {},
                "main_image_fit": {},
                "landing_page_localization": {},
                "risks": {},
                "recommendations": {},
                "sources": [],
                "missing_data": [],
                "warnings": [],
                "error": {"code": row.get("error_message") and "COUNTRY_EVALUATION_FAILED", "message": row.get("error_message") or ""} if row.get("error_message") else None,
            })

        return {
            "schema_version": "1.0",
            "research_run_id": research_run_id,
            "status": run.get("status") or "queued",
            "input_snapshot": _load_json(run.get("input_snapshot_json"), {}),
            "pipeline_cards": _load_json(run.get("pipeline_cards_json"), []),
            "product_facts": _load_json(run.get("product_facts_json"), {}),
            "media_understanding": _load_json(run.get("media_understanding_json"), {}),
            "countries": countries,
            "pricing_strategy": _load_json(run.get("pricing_strategy_json"), {}),
            "summary": _load_json(run.get("summary_json"), {}),
            "frontend": _load_json(run.get("frontend_json"), {}),
            "metadata": metadata,
            "created_at": _iso(run.get("created_at")),
            "updated_at": _iso(run.get("updated_at")),
            "completed_at": _iso(run.get("completed_at")),
        }

    def rerun_country(self, research_run_id: str, country_code: str) -> dict[str, Any]:
        code = normalize_country_codes([country_code])[0]
        execute(
            "UPDATE product_research_country_results SET status = 'running', error_message = NULL WHERE research_run_id = %s AND country_code = %s",
            (research_run_id, code),
        )
        execute(
            "UPDATE product_research_runs SET status = 'running', updated_at = %s WHERE research_run_id = %s",
            (_now_iso(), research_run_id),
        )
        runner_lifecycle.start_tracked_thread(
            project_type="product_research",
            task_id=f"{research_run_id}:{code}",
            target=self._rerun_country_sync,
            args=(research_run_id, code),
            daemon=True,
            stage="country_rerun",
            interrupt_policy="cautious",
        )
        return {"research_run_id": research_run_id, "country_code": code, "status": "running"}

    def cancel_run(self, research_run_id: str) -> dict[str, Any]:
        self._require_run(research_run_id)
        now = _now_iso()
        execute(
            "UPDATE product_research_runs SET status = 'cancelled', updated_at = %s WHERE research_run_id = %s AND status IN ('queued', 'running')",
            (now, research_run_id),
        )
        execute(
            "UPDATE product_research_country_results SET status = 'skipped', updated_at = %s WHERE research_run_id = %s AND status IN ('pending', 'running')",
            (now, research_run_id),
        )
        return {"research_run_id": research_run_id, "status": "cancelled"}

    # ── Pipeline ──────────────────────────────────────────

    def run_pipeline(self, research_run_id: str) -> dict[str, Any]:
        run = self._require_run(research_run_id)
        metadata = _load_json(run.get("metadata_json"), {})
        input_snapshot = _load_json(run.get("input_snapshot_json"), {})
        country_codes = metadata.get("countries") or list(DEFAULT_COUNTRY_CODES)
        run_errors: list[dict[str, str]] = []
        cards = _load_json(run.get("pipeline_cards_json"), _initial_cards())
        now = _now_iso()

        # Step 1: input_validation
        cards = _set_card(cards, "input_validation", "running", "正在校验输入数据")
        valid, validation_errors = _validate_input(input_snapshot)
        if not valid:
            cards = _set_card(cards, "input_validation", "failed", f"校验失败：{'; '.join(validation_errors[:3])}", error="; ".join(validation_errors))
            self._update(run_id=research_run_id, status="failed", cards=cards, error="; ".join(validation_errors))
            return self.get_result(research_run_id)
        cards = _set_card(cards, "input_validation", "completed", f"校验通过，准备评估 {len(country_codes)} 个国家")
        self._update(run_id=research_run_id, status="running", cards=cards, started_at=now)

        # Step 2: product_fact_extraction
        cards = _set_card(cards, "product_facts", "running", "正在调用 AI 抽取产品事实")
        self._update(run_id=research_run_id, cards=cards)
        try:
            product_facts = self.gemini_client.generate_product_facts(
                input_snapshot=input_snapshot,
                countries=country_configs(country_codes),
            )
            cards = _set_card(cards, "product_facts", "completed", f"产品事实抽取完成：{product_facts.get('category_detected', '-')}",
                             result=product_facts, result_summary=f"品类：{product_facts.get('category_detected', '-')}，缺失字段：{len(product_facts.get('missing_data', []))}")
            self._update(run_id=research_run_id, product_facts=product_facts, cards=cards)
        except Exception as exc:
            log.exception("product fact extraction failed: %s", research_run_id)
            run_errors.append({"stage": "product_fact_extraction", "message": str(exc)[:500]})
            cards = _set_card(cards, "product_facts", "failed", f"产品事实抽取失败：{str(exc)[:160]}", error=str(exc)[:500])
            self._update(run_id=research_run_id, status="failed", cards=cards, error=str(exc)[:500])
            return self.get_result(research_run_id)

        # Step 3: media_understanding
        cards = _set_card(cards, "media_understanding", "running", "正在调用 AI 分析主图和短视频")
        self._update(run_id=research_run_id, cards=cards)
        media_understanding: dict[str, Any] = {}
        media_paths = _collect_media_paths(input_snapshot)
        try:
            media_understanding = self.gemini_client.generate_media_understanding(
                input_snapshot=input_snapshot,
                product_facts=product_facts,
                media_paths=media_paths if media_paths else None,
            )
            cards = _set_card(cards, "media_understanding", "completed", "素材分析完成",
                             result=media_understanding, result_summary="主图和视频分析完成")
        except Exception as exc:
            log.exception("media understanding failed: %s", research_run_id)
            media_understanding = {"status": "failed", "error": str(exc)[:500]}
            cards = _set_card(cards, "media_understanding", "failed", f"素材分析失败：{str(exc)[:160]}", error=str(exc)[:500])
        self._update(run_id=research_run_id, media_understanding=media_understanding, cards=cards)

        # Steps 4-11: country evaluations
        completed_codes: list[str] = []
        failed_codes: list[str] = []
        for code in country_codes:
            country = get_country_config(code)
            card_id = f"country_{code}"
            cards = _set_card(cards, card_id, "running", f"正在评估 {country['country_name_zh']} 市场")
            self._update(run_id=research_run_id, cards=cards)

            try:
                country_result = self.gemini_client.generate_country_evaluation(
                    country=country,
                    input_snapshot=input_snapshot,
                    product_facts=product_facts,
                    media_understanding=media_understanding,
                )
                country_result = _normalize_country_result(country_result, country, input_snapshot)
                validate_json_schema(country_result, COUNTRY_EVALUATION_SCHEMA)
                completed_codes.append(code)
                decision = (country_result.get("decision") or {}).get("final_decision", "-")
                score = (country_result.get("scores") or {}).get("overall_score", 0)
                cards = _set_card(cards, card_id, "completed", f"{country['country_name_zh']} 评估完成：{decision} / {score}分",
                                 result=country_result, result_summary=f"决策：{decision}，总分：{score}")
                self._upsert_country(research_run_id, code, country, "completed", country_result)
            except Exception as exc:
                log.exception("country evaluation failed: %s / %s", research_run_id, code)
                failed_codes.append(code)
                failed_result = _failed_country_result(country, str(exc))
                cards = _set_card(cards, card_id, "failed", f"{country['country_name_zh']} 评估失败：{str(exc)[:160]}", error=str(exc)[:500])
                self._upsert_country(research_run_id, code, country, "failed", failed_result, error_message=str(exc)[:500])
            self._update(run_id=research_run_id, cards=cards)

        # Step 12: pricing_strategy_aggregation
        cards = _set_card(cards, "pricing_strategy", "running", "正在聚合 8 国定价策略")
        self._update(run_id=research_run_id, cards=cards)
        country_rows = query(
            "SELECT * FROM product_research_country_results WHERE research_run_id = %s",
            (research_run_id,),
        )
        all_countries = {}
        for row in country_rows:
            all_countries[row["country_code"]] = _load_json(row.get("full_result_json"), {})

        pricing_strategy = _aggregate_pricing(all_countries, input_snapshot)
        cards = _set_card(cards, "pricing_strategy", "completed", "定价策略聚合完成", result=pricing_strategy)
        self._update(run_id=research_run_id, pricing_strategy=pricing_strategy, cards=cards)

        # Step 13: final_conclusion
        cards = _set_card(cards, "final_conclusion", "running", "正在汇总最终结论")
        self._update(run_id=research_run_id, cards=cards)
        summary = _build_summary(all_countries)
        frontend = _build_frontend(summary, all_countries)
        final_status = "completed" if not failed_codes else "partially_completed"
        if not completed_codes and failed_codes:
            final_status = "failed"

        cards = _set_card(cards, "final_conclusion", "completed",
                         f"评估完成：GO {summary.get('go_count', 0)} / TEST {summary.get('test_count', 0)} / HOLD {summary.get('hold_count', 0)}",
                         result=summary, result_summary=f"最佳：{summary.get('best_country_zh', '-')}，最差：{summary.get('worst_country_zh', '-')}")
        metadata.update({
            "countries_completed": completed_codes,
            "countries_failed": failed_codes,
            "run_errors": run_errors,
        })
        self._update(
            run_id=research_run_id,
            status=final_status,
            cards=cards,
            summary=summary,
            frontend=frontend,
            metadata=metadata,
            completed_at=_now_iso() if final_status in ("completed", "partially_completed") else None,
            failed_at=_now_iso() if final_status == "failed" else None,
        )
        return self.get_result(research_run_id)

    # ── Internal helpers ──────────────────────────────────

    def _require_run(self, research_run_id: str) -> dict[str, Any]:
        run = query_one("SELECT * FROM product_research_runs WHERE research_run_id = %s", (research_run_id,))
        if not run:
            raise ProductResearchNotFound(f"Research run not found: {research_run_id}")
        return run

    def _update(self, *, run_id: str, status: str | None = None, cards: list | None = None,
                product_facts=None, media_understanding=None, pricing_strategy=None,
                summary=None, frontend=None, metadata=None,
                started_at=None, completed_at=None, failed_at=None, error=None):
        now = _now_iso()
        parts = ["updated_at = %s"]
        params: list[Any] = [now]
        if status is not None:
            parts.append("status = %s"); params.append(status)
        if cards is not None:
            parts.append("pipeline_cards_json = %s"); params.append(json.dumps(cards, ensure_ascii=False))
        if product_facts is not None:
            parts.append("product_facts_json = %s"); params.append(json.dumps(product_facts, ensure_ascii=False))
        if media_understanding is not None:
            parts.append("media_understanding_json = %s"); params.append(json.dumps(media_understanding, ensure_ascii=False))
        if pricing_strategy is not None:
            parts.append("pricing_strategy_json = %s"); params.append(json.dumps(pricing_strategy, ensure_ascii=False))
        if summary is not None:
            parts.append("summary_json = %s"); params.append(json.dumps(summary, ensure_ascii=False))
        if frontend is not None:
            parts.append("frontend_json = %s"); params.append(json.dumps(frontend, ensure_ascii=False))
        if metadata is not None:
            parts.append("metadata_json = %s"); params.append(json.dumps(metadata, ensure_ascii=False))
        if started_at is not None:
            parts.append("started_at = %s"); params.append(started_at)
        if completed_at is not None:
            parts.append("completed_at = %s"); params.append(completed_at)
        if failed_at is not None:
            parts.append("failed_at = %s"); params.append(failed_at)
        if error is not None:
            parts.append("error_message = %s"); params.append(str(error)[:1000])
        params.append(run_id)
        execute(f"UPDATE product_research_runs SET {', '.join(parts)} WHERE research_run_id = %s", tuple(params))

    def _upsert_country(self, run_id: str, code: str, country: dict, status: str,
                        result: dict, error_message: str | None = None):
        now = _now_iso()
        existing = query_one(
            "SELECT id FROM product_research_country_results WHERE research_run_id = %s AND country_code = %s",
            (run_id, code),
        )
        if existing:
            execute(
                """UPDATE product_research_country_results
                SET status=%s, full_result_json=%s, scores_json=%s, decision_json=%s,
                    competitor_pricing_json=%s, pricing_strategy_json=%s, shipping_strategy_json=%s,
                    short_video_fit_json=%s, main_image_fit_json=%s, landing_page_localization_json=%s,
                    risks_json=%s, recommendations_json=%s, sources_json=%s,
                    error_message=%s, updated_at=%s,
                    completed_at=%s, failed_at=%s
                WHERE research_run_id=%s AND country_code=%s""",
                (status,
                 json.dumps(result, ensure_ascii=False),
                 json.dumps(result.get("scores", {}), ensure_ascii=False),
                 json.dumps(result.get("decision", {}), ensure_ascii=False),
                 json.dumps(result.get("competitor_pricing", {}), ensure_ascii=False),
                 json.dumps(result.get("pricing_strategy", {}), ensure_ascii=False),
                 json.dumps(result.get("shipping_strategy", {}), ensure_ascii=False),
                 json.dumps(result.get("short_video_fit", {}), ensure_ascii=False),
                 json.dumps(result.get("main_image_fit", {}), ensure_ascii=False),
                 json.dumps(result.get("landing_page_localization", {}), ensure_ascii=False),
                 json.dumps(result.get("risks", {}), ensure_ascii=False),
                 json.dumps(result.get("recommendations", {}), ensure_ascii=False),
                 json.dumps(result.get("sources", []), ensure_ascii=False),
                 error_message,
                 now,
                 now if status == "completed" else None,
                 now if status == "failed" else None,
                 run_id, code),
            )
        else:
            execute(
                """INSERT INTO product_research_country_results
                (research_run_id, country_code, country_name, country_name_zh, status,
                 full_result_json, scores_json, decision_json, competitor_pricing_json,
                 pricing_strategy_json, shipping_strategy_json, short_video_fit_json,
                 main_image_fit_json, landing_page_localization_json, risks_json,
                 recommendations_json, sources_json, error_message, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (run_id, code, country["country_name"], country["country_name_zh"], status,
                 json.dumps(result, ensure_ascii=False),
                 json.dumps(result.get("scores", {}), ensure_ascii=False),
                 json.dumps(result.get("decision", {}), ensure_ascii=False),
                 json.dumps(result.get("competitor_pricing", {}), ensure_ascii=False),
                 json.dumps(result.get("pricing_strategy", {}), ensure_ascii=False),
                 json.dumps(result.get("shipping_strategy", {}), ensure_ascii=False),
                 json.dumps(result.get("short_video_fit", {}), ensure_ascii=False),
                 json.dumps(result.get("main_image_fit", {}), ensure_ascii=False),
                 json.dumps(result.get("landing_page_localization", {}), ensure_ascii=False),
                 json.dumps(result.get("risks", {}), ensure_ascii=False),
                 json.dumps(result.get("recommendations", {}), ensure_ascii=False),
                 json.dumps(result.get("sources", []), ensure_ascii=False),
                 error_message,
                 now, now),
            )

    def _rerun_country_sync(self, research_run_id: str, country_code: str) -> None:
        run = self._require_run(research_run_id)
        input_snapshot = _load_json(run.get("input_snapshot_json"), {})
        product_facts = _load_json(run.get("product_facts_json"), {})
        media_understanding = _load_json(run.get("media_understanding_json"), {})
        country = get_country_config(country_code)
        try:
            result = self.gemini_client.generate_country_evaluation(
                country=country, input_snapshot=input_snapshot,
                product_facts=product_facts, media_understanding=media_understanding,
            )
            result = _normalize_country_result(result, country, input_snapshot)
            self._upsert_country(research_run_id, country_code, country, "completed", result)
        except Exception as exc:
            failed = _failed_country_result(country, str(exc))
            self._upsert_country(research_run_id, country_code, country, "failed", failed, str(exc)[:500])

        # Recompute summary
        rows = query("SELECT * FROM product_research_country_results WHERE research_run_id = %s", (research_run_id,))
        all_countries = {row["country_code"]: _load_json(row.get("full_result_json"), {}) for row in rows}
        summary = _build_summary(all_countries)
        frontend = _build_frontend(summary, all_countries)
        self._update(run_id=research_run_id, status="completed", summary=summary, frontend=frontend)


# ── Module-level singleton ───────────────────────────────

_SERVICE: ProductResearchService | None = None


def get_service() -> ProductResearchService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ProductResearchService()
    return _SERVICE


# ── Helpers ──────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return str(value)


def _load_json(raw: Any, default: Any = None) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except Exception:
            return default
    return default


def _initial_cards() -> list[dict[str, Any]]:
    cards = []
    for step in PIPELINE_STEPS:
        cards.append({
            "card_id": step["card_id"],
            "title": step["title"],
            "subtitle": step.get("subtitle", ""),
            "status": "pending",
            "progress": 0,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "result_summary": "",
            "result": {},
            "result_ref": "",
        })
    return cards


def _set_card(cards: list[dict], card_id: str, status: str, message: str,
              result=None, result_summary=None, error=None) -> list[dict]:
    now = _now_iso()
    for card in cards:
        if card["card_id"] == card_id:
            card["status"] = status
            if status == "running":
                card["started_at"] = card["started_at"] or now
            if status in ("completed", "failed", "skipped"):
                card["completed_at"] = now
                card["progress"] = 100
            elif status == "running":
                card["progress"] = 50
            card["result_summary"] = result_summary or message
            if result is not None:
                card["result"] = result
            card["error"] = error
            return cards
    return cards


def _validate_input(input_snapshot: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not input_snapshot.get("product_url"):
        errors.append("PRODUCT_URL_REQUIRED: Product URL is required")
    main_image = input_snapshot.get("main_image") or {}
    if not main_image.get("url") and not main_image.get("asset_id"):
        errors.append("MAIN_IMAGE_REQUIRED: Main image is required")
    short_video = input_snapshot.get("short_video") or {}
    if not short_video.get("url") and not short_video.get("asset_id"):
        errors.append("SHORT_VIDEO_REQUIRED: Short video is required")
    return len(errors) == 0, errors


def _sanitize_input(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_url": str(data.get("product_url") or "").strip(),
        "main_image": data.get("main_image") or {},
        "short_video": data.get("short_video") or {},
        "current_price": data.get("current_price") or {},
        "product_cost": data.get("product_cost") or {},
        "package": data.get("package") or {},
        "shipping_inputs": data.get("shipping_inputs") or {},
        "business_targets": data.get("business_targets") or {},
        "notes": str(data.get("notes") or "").strip(),
    }


def _collect_media_paths(input_snapshot: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("main_image", "short_video"):
        asset = input_snapshot.get(key) or {}
        local = asset.get("local_path", "")
        if local:
            paths.append(local)
    return paths


def _normalize_country_result(result: dict, country: dict, input_snapshot: dict) -> dict:
    out = dict(result)
    out.setdefault("country_code", country["country_code"])
    out.setdefault("country_name", country["country_name"])
    out.setdefault("country_name_zh", country["country_name_zh"])
    out.setdefault("language", country["language"])
    out.setdefault("currency", country["currency"])
    out.setdefault("status", "completed")
    raw_scores = out.get("scores") or {}
    out["scores"] = _clamp_scores(raw_scores)
    out["scores"]["overall_score"] = _compute_overall(out["scores"])
    out.setdefault("decision", {})
    blocking = out["decision"].get("blocking_issues") or []
    out["decision"]["final_decision"] = out["decision"].get("final_decision") or decision_from_score(out["scores"]["overall_score"], blocking)
    out["decision"].setdefault("confidence", "medium")
    out["decision"].setdefault("one_sentence_reason", "")
    out["decision"].setdefault("why", [])
    out["decision"].setdefault("blocking_issues", [])
    out.setdefault("missing_data", [])
    out.setdefault("warnings", [])
    out.setdefault("sources", [])
    return out


def _clamp_scores(scores: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for key in SCORE_KEYS:
        try:
            val = int(round(float(scores.get(key, 0))))
        except (TypeError, ValueError):
            val = 0
        out[key] = max(0, min(100, val))
    return out


def _compute_overall(scores: dict[str, int]) -> int:
    from appcore.product_research_config import SCORE_WEIGHTS
    weighted = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        if key in scores:
            weighted += scores[key] * weight
    return int(round(weighted))


def _failed_country_result(country: dict, message: str) -> dict:
    return {
        "country_code": country["country_code"],
        "country_name": country["country_name"],
        "country_name_zh": country["country_name_zh"],
        "language": country["language"],
        "currency": country["currency"],
        "status": "failed",
        "scores": {k: 0 for k in SCORE_KEYS},
        "decision": {"final_decision": "HOLD", "confidence": "low", "one_sentence_reason": "评估失败，需重试", "why": [], "blocking_issues": ["country_evaluation_failed"]},
        "error": {"code": "COUNTRY_EVALUATION_FAILED", "message": message[:500]},
        "sources": [],
        "missing_data": [],
        "warnings": [message[:500]],
    }


# ── Aggregation ──────────────────────────────────────────

def _aggregate_pricing(countries: dict[str, dict], input_snapshot: dict) -> dict:
    fx = get_exchange_rate_provider()
    current_price = input_snapshot.get("current_price") or {}
    current_amount = current_price.get("amount")
    current_currency = str(current_price.get("currency") or "USD").upper()

    per_country: list[dict] = []
    for code, cdata in countries.items():
        country = get_country_config(code)
        pricing = cdata.get("pricing_strategy") or {}
        competitor = cdata.get("competitor_pricing") or {}
        price_band = competitor.get("price_band") or {}
        target_currency = country["currency"]

        current_local = None
        if current_amount is not None:
            current_local = fx.convert(Decimal(str(current_amount)), current_currency, target_currency)
            if current_local is not None:
                current_local = float(current_local)

        per_country.append({
            "country_code": code,
            "country_name_zh": country["country_name_zh"],
            "currency": target_currency,
            "current_price_local": current_local,
            "recommended_price": pricing.get("recommended_price", {}).get("amount"),
            "competitor_min": price_band.get("min"),
            "competitor_median": price_band.get("median"),
            "competitor_max": price_band.get("max"),
            "pricing_confidence": pricing.get("pricing_confidence", "low"),
        })

    return {
        "per_country": per_country,
        "input_currency": current_currency,
        "input_amount": current_amount,
    }


def _build_summary(countries: dict[str, dict]) -> dict:
    entries: list[dict] = []
    go_count = 0
    test_count = 0
    hold_count = 0

    for code, cdata in countries.items():
        scores = cdata.get("scores") or {}
        decision = cdata.get("decision") or {}
        fd = decision.get("final_decision", "HOLD")
        overall = scores.get("overall_score", 0)
        entry = {
            "country_code": code,
            "country_name_zh": cdata.get("country_name_zh", code),
            "overall_score": overall,
            "decision": fd,
            "confidence": decision.get("confidence", "low"),
            "one_sentence_reason": decision.get("one_sentence_reason", ""),
            "status": cdata.get("status", "unknown"),
        }
        entries.append(entry)
        if cdata.get("status") == "failed":
            hold_count += 1
        elif fd == "GO":
            go_count += 1
        elif fd == "TEST":
            test_count += 1
        else:
            hold_count += 1

    entries.sort(key=lambda x: x["overall_score"], reverse=True)
    valid = [e for e in entries if e["status"] != "failed"]
    avg_score = int(round(sum(e["overall_score"] for e in valid) / len(valid))) if valid else 0
    best = valid[0] if valid else None
    worst = valid[-1] if valid else None

    return {
        "ranking": entries,
        "average_score": avg_score,
        "best_country": best["country_code"] if best else "",
        "best_country_zh": best["country_name_zh"] if best else "",
        "worst_country": worst["country_code"] if worst else "",
        "worst_country_zh": worst["country_name_zh"] if worst else "",
        "go_count": go_count,
        "test_count": test_count,
        "hold_count": hold_count,
    }


def _build_frontend(summary: dict, countries: dict[str, dict]) -> dict:
    # Cards
    avg = summary.get("average_score", 0)
    severity = "success" if avg >= 75 else ("warning" if avg >= 60 else "danger")
    cards = [
        {"card_type": "summary_metric", "title": "平均分", "value": avg, "unit": "%", "severity": severity},
        {"card_type": "summary_metric", "title": "GO", "value": summary.get("go_count", 0), "unit": "国", "severity": "success"},
        {"card_type": "summary_metric", "title": "TEST", "value": summary.get("test_count", 0), "unit": "国", "severity": "warning"},
        {"card_type": "summary_metric", "title": "HOLD", "value": summary.get("hold_count", 0), "unit": "国", "severity": "danger"},
    ]

    # Charts
    bar_chart = []
    radar_chart = []
    pricing_chart = []
    for code, cdata in countries.items():
        scores = cdata.get("scores") or {}
        decision = cdata.get("decision") or {}
        pricing = cdata.get("pricing_strategy") or {}
        competitor = cdata.get("competitor_pricing") or {}
        price_band = competitor.get("price_band") or {}

        bar_chart.append({
            "country_code": code,
            "country_name_zh": cdata.get("country_name_zh", code),
            "overall_score": scores.get("overall_score", 0),
            "decision": decision.get("final_decision", "HOLD"),
        })
        radar_chart.append({
            "country_code": code,
            "product_market_fit_score": scores.get("product_market_fit_score", 0),
            "video_selling_fit_score": scores.get("video_selling_fit_score", 0),
            "pricing_score": scores.get("pricing_score", 0),
            "shipping_strategy_score": scores.get("shipping_strategy_score", 0),
            "landing_page_localization_score": scores.get("landing_page_localization_score", 0),
        })
        pricing_chart.append({
            "country_code": code,
            "current_price_local": (pricing.get("current_price_local") or {}).get("amount"),
            "recommended_price": (pricing.get("recommended_price") or {}).get("amount"),
            "competitor_min": price_band.get("min"),
            "competitor_median": price_band.get("median"),
            "competitor_max": price_band.get("max"),
            "currency": cdata.get("currency", "EUR"),
        })

    # Table
    overview = []
    for code, cdata in countries.items():
        scores = cdata.get("scores") or {}
        decision = cdata.get("decision") or {}
        pricing = cdata.get("pricing_strategy") or {}
        shipping = cdata.get("shipping_strategy") or {}
        recommendations = cdata.get("recommendations") or {}
        risks = cdata.get("risks") or {}
        all_risks = (risks.get("claim_risks") or []) + (risks.get("compliance_risks") or []) + (risks.get("operational_risks") or [])

        overview.append({
            "country_code": code,
            "country_name_zh": cdata.get("country_name_zh", code),
            "overall_score": scores.get("overall_score", 0),
            "decision": decision.get("final_decision", "HOLD"),
            "confidence": decision.get("confidence", "low"),
            "recommended_price": (pricing.get("recommended_price") or {}).get("amount"),
            "currency": cdata.get("currency", "EUR"),
            "shipping_strategy": shipping.get("recommended_model", "unknown"),
            "recommended_positioning": recommendations.get("recommended_positioning", ""),
            "top_risk": all_risks[0] if all_risks else "",
            "top_action": (recommendations.get("pricing_actions") or recommendations.get("creative_actions") or [""])[0],
        })

    # Badges & action items
    badges = []
    action_items = []
    for code, cdata in countries.items():
        decision = cdata.get("decision") or {}
        fd = decision.get("final_decision", "HOLD")
        sev = "success" if fd == "GO" else ("warning" if fd == "TEST" else "danger")
        badges.append({"country_code": code, "label": fd, "severity": sev})

        recommendations = cdata.get("recommendations") or {}
        for action in recommendations.get("pricing_actions") or []:
            action_items.append({"priority": "high", "country_code": code, "type": "pricing", "title": action[:80], "description": action})
        for action in recommendations.get("creative_actions") or []:
            action_items.append({"priority": "medium", "country_code": code, "type": "creative", "title": action[:80], "description": action})
        for action in recommendations.get("landing_page_actions") or []:
            action_items.append({"priority": "medium", "country_code": code, "type": "landing_page", "title": action[:80], "description": action})

    return {
        "cards": cards,
        "charts": {
            "country_score_bar": bar_chart,
            "score_radar": radar_chart,
            "pricing_comparison": pricing_chart,
        },
        "tables": {"country_overview": overview},
        "badges": badges,
        "action_items": action_items,
    }