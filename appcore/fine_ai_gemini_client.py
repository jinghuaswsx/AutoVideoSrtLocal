"""Gemini wrapper for fine AI evaluation.

Uses the project-wide llm_client so provider credentials, billing logs, and
adapter behavior stay centralized.

Docs-anchor:
docs/superpowers/specs/2026-05-22-single-product-five-country-ai-evaluation-design.md
"""

from __future__ import annotations

import json
import logging
from typing import Any

from appcore import llm_client
from appcore.fine_ai_evaluation_prompts import (
    COUNTRY_EVALUATION_SYSTEM_PROMPT,
    JSON_REPAIR_SYSTEM_PROMPT,
    PRODUCT_FACT_SYSTEM_PROMPT,
    build_country_evaluation_prompt,
    build_json_repair_prompt,
    build_product_fact_prompt,
)
from appcore.fine_ai_evaluation_schemas import (
    COUNTRY_EVALUATION_SCHEMA,
    PRODUCT_FACTS_SCHEMA,
    validate_json_schema,
)
from appcore.llm_providers._helpers.vertex_json import parse_json_content

log = logging.getLogger(__name__)

PROVIDER = "gemini_vertex_adc"
MODEL = "gemini-3.5-flash"
PRODUCT_FACTS_USE_CASE = "fine_ai_evaluation.product_facts"
COUNTRY_USE_CASE = "fine_ai_evaluation.country"
RAW_RESPONSE_PREVIEW_CHARS = 8000
ORIGINAL_PARSE_RETRY_ATTEMPTS = 2


class FineAiGeminiClient:
    def __init__(self):
        self.last_call_metadata: dict[str, Any] = {}

    def generate_product_facts(
        self,
        *,
        product_snapshot: dict[str, Any],
        countries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = build_product_fact_prompt(product_snapshot=product_snapshot, countries=countries)
        result = self.generate_structured_json(
            prompt=prompt,
            schema=PRODUCT_FACTS_SCHEMA,
            use_case_code=PRODUCT_FACTS_USE_CASE,
            system=PRODUCT_FACT_SYSTEM_PROMPT,
            google_search=False,
            url_context=bool(product_snapshot.get("product_url")),
            thinking_level="medium",
            project_id=f"fine-ai-product-{product_snapshot.get('product_id')}",
        )
        validate_json_schema(result, PRODUCT_FACTS_SCHEMA)
        return result

    def generate_country_evaluation(
        self,
        *,
        product_snapshot: dict[str, Any],
        product_facts: dict[str, Any],
        country: dict[str, Any],
        asset_snapshot: dict[str, Any],
        asset_paths: list[str],
    ) -> dict[str, Any]:
        prompt = build_country_evaluation_prompt(
            product_snapshot=product_snapshot,
            product_facts=product_facts,
            country=country,
            asset_snapshot={key: value for key, value in asset_snapshot.items() if key != "asset_paths"},
        )
        result = self.generate_structured_json_with_assets(
            prompt=prompt,
            schema=COUNTRY_EVALUATION_SCHEMA,
            asset_paths_or_urls=asset_paths,
            use_case_code=COUNTRY_USE_CASE,
            system=COUNTRY_EVALUATION_SYSTEM_PROMPT,
            google_search=False,
            url_context=True,
            thinking_level="high",
            project_id=f"fine-ai-product-{product_snapshot.get('product_id')}-{country.get('country_code')}",
        )
        validate_json_schema(result, COUNTRY_EVALUATION_SCHEMA)
        return result

    def generate_structured_json(
        self,
        prompt: str,
        schema: dict,
        tools: list | None = None,
        thinking_level: str = "medium",
        *,
        use_case_code: str = PRODUCT_FACTS_USE_CASE,
        system: str | None = None,
        google_search: bool | None = None,
        url_context: bool | None = None,
        project_id: str | None = None,
    ) -> dict:
        return self._invoke(
            prompt=prompt,
            schema=schema,
            media=None,
            tools=tools,
            thinking_level=thinking_level,
            use_case_code=use_case_code,
            system=system,
            google_search=google_search,
            url_context=url_context,
            project_id=project_id,
        )

    def generate_structured_json_with_assets(
        self,
        prompt: str,
        schema: dict,
        asset_paths_or_urls: list,
        tools: list | None = None,
        thinking_level: str = "high",
        *,
        use_case_code: str = COUNTRY_USE_CASE,
        system: str | None = None,
        google_search: bool | None = None,
        url_context: bool | None = None,
        project_id: str | None = None,
    ) -> dict:
        return self._invoke(
            prompt=prompt,
            schema=schema,
            media=[str(path) for path in (asset_paths_or_urls or []) if str(path or "").strip()],
            tools=tools,
            thinking_level=thinking_level,
            use_case_code=use_case_code,
            system=system,
            google_search=google_search,
            url_context=url_context,
            project_id=project_id,
        )

    def _invoke(
        self,
        *,
        prompt: str,
        schema: dict,
        media: list[str] | None,
        tools: list | None,
        thinking_level: str,
        use_case_code: str,
        system: str | None,
        google_search: bool | None,
        url_context: bool | None,
        project_id: str | None,
    ) -> dict:
        self.last_call_metadata = {}
        retry_history: list[dict[str, Any]] = []
        last_error: Exception | None = None
        for attempt in range(1, ORIGINAL_PARSE_RETRY_ATTEMPTS + 1):
            attempt_project_id = project_id
            if attempt > 1 and project_id:
                attempt_project_id = f"{project_id}-retry-{attempt}"
            result = llm_client.invoke_generate(
                use_case_code,
                prompt=prompt,
                system=system,
                media=media or None,
                response_schema=schema,
                max_output_tokens=12288,
                provider_override=PROVIDER,
                model_override=MODEL,
                google_search=google_search,
                url_context=url_context,
                project_id=attempt_project_id,
                billing_extra={
                    "provider": PROVIDER,
                    "model": MODEL,
                    "thinking_level": thinking_level,
                    "google_search": bool(google_search),
                    "url_context": bool(url_context),
                    "tools": tools or [],
                    "structured_retry_attempt": attempt,
                },
            )
            metadata = _response_metadata(result, thinking_level=thinking_level)
            metadata["structured_retry_attempt"] = attempt
            if retry_history:
                metadata["structured_retry_history"] = retry_history[-3:]
            payload = result.get("json")
            if isinstance(payload, dict):
                self.last_call_metadata = metadata
                return payload
            raw = result.get("text") or ""
            try:
                parsed = _parse_json_with_repair(raw)
            except ValueError as exc:
                metadata["raw_response"] = _raw_response_summary(result, parse_error=str(exc))
                try:
                    repaired = self._repair_json_response(
                        raw_response=raw,
                        parse_error=str(result.get("json_parse_error") or exc),
                        schema=schema,
                        thinking_level=thinking_level,
                        use_case_code=use_case_code,
                        project_id=attempt_project_id,
                        metadata=metadata,
                    )
                except ValueError as repair_exc:
                    last_error = repair_exc
                    retry_history.append(_retry_history_item(attempt, metadata))
                    if attempt < ORIGINAL_PARSE_RETRY_ATTEMPTS:
                        continue
                    self.last_call_metadata = metadata
                    raise
                self.last_call_metadata = metadata
                return repaired
            if not isinstance(parsed, dict):
                last_error = ValueError("Gemini structured JSON response is not an object")
                metadata["raw_response"] = _raw_response_summary(result, parse_error=str(last_error))
                retry_history.append(_retry_history_item(attempt, metadata))
                if attempt < ORIGINAL_PARSE_RETRY_ATTEMPTS:
                    continue
                self.last_call_metadata = metadata
                raise last_error
            self.last_call_metadata = metadata
            return parsed
        if last_error:
            raise last_error
        raise ValueError("Gemini structured JSON response is empty")

    def _repair_json_response(
        self,
        *,
        raw_response: str,
        parse_error: str,
        schema: dict,
        thinking_level: str,
        use_case_code: str,
        project_id: str | None,
        metadata: dict[str, Any],
    ) -> dict:
        metadata["json_repair_attempted"] = True
        repair_result = llm_client.invoke_generate(
            use_case_code,
            prompt=build_json_repair_prompt(raw_response=raw_response, parse_error=parse_error),
            system=JSON_REPAIR_SYSTEM_PROMPT,
            media=None,
            response_schema=schema,
            max_output_tokens=12288,
            provider_override=PROVIDER,
            model_override=MODEL,
            google_search=False,
            url_context=False,
            project_id=f"{project_id}-json-repair" if project_id else None,
            billing_extra={
                "provider": PROVIDER,
                "model": MODEL,
                "thinking_level": thinking_level,
                "google_search": False,
                "url_context": False,
                "tools": [],
                "json_repair": True,
            },
        )
        repair_metadata = _response_metadata(repair_result, thinking_level=thinking_level)
        metadata["repair_usage"] = repair_metadata.get("usage") or {}
        metadata["repair_usage_log_id"] = repair_metadata.get("usage_log_id")
        payload = repair_result.get("json")
        if isinstance(payload, dict):
            metadata["json_repair_succeeded"] = True
            return payload
        repair_raw = repair_result.get("text") or ""
        try:
            parsed = _parse_json_with_repair(repair_raw)
        except ValueError as exc:
            metadata["json_repair_succeeded"] = False
            metadata["json_repair_error"] = str(exc)[:500]
            metadata["repair_raw_response"] = _raw_response_summary(repair_result, parse_error=str(exc))
            raise ValueError(f"Gemini JSON repair failed: {exc}") from exc
        if not isinstance(parsed, dict):
            metadata["json_repair_succeeded"] = False
            metadata["json_repair_error"] = "Gemini JSON repair response is not an object"
            metadata["repair_raw_response"] = _raw_response_summary(repair_result)
            raise ValueError("Gemini JSON repair response is not an object")
        metadata["json_repair_succeeded"] = True
        return parsed


def _parse_json_with_repair(raw: str) -> Any:
    last_error: Exception | None = None
    candidate = raw
    for _attempt in range(3):
        try:
            return parse_json_content(candidate)
        except Exception as exc:
            last_error = exc
            candidate = _repair_json_text(candidate)
    raise ValueError(f"JSON parse failed after repair: {last_error}") from last_error


def _repair_json_text(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start:end + 1]
    return text.strip()


def _response_metadata(result: dict[str, Any], *, thinking_level: str) -> dict[str, Any]:
    raw = result.get("raw")
    usage = result.get("usage") or {}
    metadata = {
        "provider": PROVIDER,
        "model": MODEL,
        "thinking_level": thinking_level,
        "usage": usage,
        "usage_log_id": result.get("usage_log_id"),
    }
    try:
        metadata["grounding_metadata"] = _extract_grounding(raw)
        metadata["url_context_metadata"] = _extract_url_context(raw)
    except Exception:
        log.debug("failed to extract Gemini metadata", exc_info=True)
    return metadata


def _raw_response_summary(result: dict[str, Any], *, parse_error: str = "") -> dict[str, Any]:
    raw_text = str(result.get("text") or "")
    return {
        "text_preview": raw_text[:RAW_RESPONSE_PREVIEW_CHARS],
        "text_length": len(raw_text),
        "json_parse_error": str(result.get("json_parse_error") or parse_error or "")[:500],
        "usage_log_id": result.get("usage_log_id"),
    }


def _retry_history_item(attempt: int, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "raw_response": metadata.get("raw_response") or {},
        "json_repair_error": metadata.get("json_repair_error") or "",
        "repair_raw_response": metadata.get("repair_raw_response") or {},
    }


def _extract_grounding(raw: Any) -> dict[str, Any]:
    candidates = getattr(raw, "candidates", None) or []
    if not candidates:
        return {}
    grounding = getattr(candidates[0], "grounding_metadata", None)
    return _jsonable(grounding)


def _extract_url_context(raw: Any) -> dict[str, Any]:
    meta = getattr(raw, "url_context_metadata", None)
    return _jsonable(meta)


def _jsonable(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)
