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
    PRODUCT_FACT_SYSTEM_PROMPT,
    build_country_evaluation_prompt,
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


class FineAiGeminiClient:
    def __init__(self):
        self.last_call_metadata: dict[str, Any] = {}
        self.last_call_trace: dict[str, Any] = {}

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
            google_search=True,
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
        self.last_call_trace = {}
        request_payload = {
            "use_case_code": use_case_code,
            "prompt": prompt,
            "system": system,
            "media": media or None,
            "response_schema": schema,
            "max_output_tokens": 12288,
            "provider_override": PROVIDER,
            "model_override": MODEL,
            "google_search": google_search,
            "url_context": url_context,
            "project_id": project_id,
            "billing_extra": {
                "provider": PROVIDER,
                "model": MODEL,
                "thinking_level": thinking_level,
                "google_search": bool(google_search),
                "url_context": bool(url_context),
                "tools": tools or [],
            },
        }
        try:
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
                project_id=project_id,
                billing_extra=request_payload["billing_extra"],
            )
        except Exception as exc:
            self.last_call_trace = _build_call_trace(
                request_payload=request_payload,
                result={},
                parsed_json=None,
                thinking_level=thinking_level,
                error=exc,
            )
            raise
        self.last_call_metadata = _response_metadata(result, thinking_level=thinking_level)
        payload = result.get("json")
        try:
            if isinstance(payload, dict):
                parsed = payload
            else:
                raw = result.get("text") or ""
                parsed = _parse_json_with_repair(raw)
            if not isinstance(parsed, dict):
                raise ValueError("Gemini structured JSON response is not an object")
        except Exception as exc:
            self.last_call_trace = _build_call_trace(
                request_payload=request_payload,
                result=result,
                parsed_json=None,
                thinking_level=thinking_level,
                error=exc,
            )
            raise
        self.last_call_trace = _build_call_trace(
            request_payload=request_payload,
            result=result,
            parsed_json=parsed,
            thinking_level=thinking_level,
        )
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


def _build_call_trace(
    *,
    request_payload: dict[str, Any],
    result: dict[str, Any],
    parsed_json: dict[str, Any] | None,
    thinking_level: str,
    error: Exception | None = None,
) -> dict[str, Any]:
    usage = result.get("usage") or {}
    media = request_payload.get("media") or []
    trace = {
        "provider": PROVIDER,
        "model_id": MODEL,
        "use_case_code": request_payload.get("use_case_code") or "",
        "project_id": request_payload.get("project_id") or "",
        "request": {
            "summary": {
                "provider": PROVIDER,
                "model_id": MODEL,
                "use_case_code": request_payload.get("use_case_code") or "",
                "project_id": request_payload.get("project_id") or "",
                "media_count": len(media) if isinstance(media, list) else 1,
                "google_search": bool(request_payload.get("google_search")),
                "url_context": bool(request_payload.get("url_context")),
                "thinking_level": thinking_level,
                "max_output_tokens": request_payload.get("max_output_tokens"),
            },
            "system_prompt": request_payload.get("system") or "",
            "prompt": request_payload.get("prompt") or "",
            "payload": _redact_sensitive(_jsonable(request_payload)),
        },
        "response": {
            "summary": {
                "has_json": isinstance(result.get("json"), dict),
                "has_text": bool(result.get("text")),
                "input_tokens": usage.get("input_tokens") or usage.get("prompt_tokens") or "",
                "output_tokens": usage.get("output_tokens") or usage.get("completion_tokens") or "",
                "usage_log_id": result.get("usage_log_id") or "",
            },
            "parsed_json": _redact_sensitive(_jsonable(parsed_json or {})),
            "raw_payload": _redact_sensitive(_jsonable(result or {})),
        },
    }
    if error is not None:
        trace["error"] = {
            "type": type(error).__name__,
            "message": str(error)[:1000],
        }
        trace["response"]["summary"]["error"] = str(error)[:500]
    return trace


def _redact_sensitive(value: Any) -> Any:
    sensitive_keys = {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "secret",
        "password",
        "access_token",
        "refresh_token",
        "bearer",
    }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(token in lowered for token in sensitive_keys):
                out[key_text] = "[redacted]"
            else:
                out[key_text] = _redact_sensitive(item)
        return out
    return value


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
