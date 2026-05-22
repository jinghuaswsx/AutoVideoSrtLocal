"""Gemini client for single-product AI research.

Uses Google AI Studio (gemini_aistudio) with gemini-3.5-flash.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from appcore import llm_client
from appcore.llm_providers._helpers.vertex_json import parse_json_content
from appcore.product_research_prompts import (
    COUNTRY_EVALUATION_SYSTEM_PROMPT,
    MEDIA_UNDERSTANDING_SYSTEM_PROMPT,
    PRODUCT_FACT_SYSTEM_PROMPT,
    build_country_evaluation_prompt,
    build_media_understanding_prompt,
    build_product_fact_prompt,
)
from appcore.product_research_schemas import (
    COUNTRY_EVALUATION_SCHEMA,
    MEDIA_UNDERSTANDING_SCHEMA,
    PRODUCT_FACTS_SCHEMA,
    validate_json_schema,
)

log = logging.getLogger(__name__)

PROVIDER = "gemini_aistudio"
MODEL = "gemini-3.5-flash"
PRODUCT_FACTS_USE_CASE = "product_research.product_facts"
MEDIA_UNDERSTANDING_USE_CASE = "product_research.media_understanding"
COUNTRY_USE_CASE = "product_research.country"


class ProductResearchGeminiClient:
    def __init__(self):
        self.last_call_metadata: dict[str, Any] = {}

    def generate_product_facts(
        self,
        *,
        input_snapshot: dict[str, Any],
        countries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = build_product_fact_prompt(input_snapshot=input_snapshot, countries=countries)
        result = self._invoke(
            prompt=prompt,
            schema=PRODUCT_FACTS_SCHEMA,
            use_case_code=PRODUCT_FACTS_USE_CASE,
            system=PRODUCT_FACT_SYSTEM_PROMPT,
            google_search=True,
            url_context=bool(input_snapshot.get("product_url")),
            project_id=f"pr-facts-{_short_id()}",
        )
        validate_json_schema(result, PRODUCT_FACTS_SCHEMA)
        return result

    def generate_media_understanding(
        self,
        *,
        input_snapshot: dict[str, Any],
        product_facts: dict[str, Any],
        media_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        prompt = build_media_understanding_prompt(
            input_snapshot=input_snapshot,
            product_facts=product_facts,
        )
        result = self._invoke(
            prompt=prompt,
            schema=MEDIA_UNDERSTANDING_SCHEMA,
            media=media_paths or None,
            use_case_code=MEDIA_UNDERSTANDING_USE_CASE,
            system=MEDIA_UNDERSTANDING_SYSTEM_PROMPT,
            google_search=True,
            project_id=f"pr-media-{_short_id()}",
        )
        validate_json_schema(result, MEDIA_UNDERSTANDING_SCHEMA)
        return result

    def generate_country_evaluation(
        self,
        *,
        country: dict[str, Any],
        input_snapshot: dict[str, Any],
        product_facts: dict[str, Any],
        media_understanding: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = build_country_evaluation_prompt(
            country=country,
            input_snapshot=input_snapshot,
            product_facts=product_facts,
            media_understanding=media_understanding,
        )
        result = self._invoke(
            prompt=prompt,
            schema=COUNTRY_EVALUATION_SCHEMA,
            use_case_code=COUNTRY_USE_CASE,
            system=COUNTRY_EVALUATION_SYSTEM_PROMPT,
            google_search=True,
            url_context=True,
            project_id=f"pr-country-{country.get('country_code', '')}-{_short_id()}",
        )
        validate_json_schema(result, COUNTRY_EVALUATION_SCHEMA)
        return result

    def _invoke(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        media: list[str] | None = None,
        use_case_code: str,
        system: str | None = None,
        google_search: bool = False,
        url_context: bool = False,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        self.last_call_metadata = {}
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
            billing_extra={
                "provider": PROVIDER,
                "model": MODEL,
                "google_search": bool(google_search),
                "url_context": bool(url_context),
            },
        )
        self.last_call_metadata = _response_metadata(result)
        payload = result.get("json")
        if isinstance(payload, dict):
            return payload
        raw = result.get("text") or ""
        parsed = _parse_json_with_repair(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Gemini structured JSON response is not an object")
        return parsed


def _parse_json_with_repair(raw: str) -> Any:
    candidate = raw
    for _attempt in range(2):
        try:
            return parse_json_content(candidate)
        except Exception:
            candidate = _repair_json_text(candidate)
    raise ValueError("JSON parse failed after 2 repair attempts")


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
        text = text[start : end + 1]
    return text.strip()


def _response_metadata(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("raw")
    usage = result.get("usage") or {}
    metadata: dict[str, Any] = {
        "provider": PROVIDER,
        "model": MODEL,
        "usage": usage,
        "usage_log_id": result.get("usage_log_id"),
    }
    try:
        metadata["grounding_metadata"] = _extract_grounding(raw)
        metadata["url_context_metadata"] = _extract_url_context(raw)
    except Exception:
        log.debug("failed to extract Gemini metadata", exc_info=True)
    return metadata


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


def _short_id() -> str:
    import uuid
    return uuid.uuid4().hex[:8]