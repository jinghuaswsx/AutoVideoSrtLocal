"""JSON schemas and lightweight validation for fine AI evaluation.

Docs-anchor:
docs/superpowers/specs/2026-05-22-single-product-five-country-ai-evaluation-design.md
"""

from __future__ import annotations

from typing import Any

from appcore.fine_ai_evaluation_country_config import DEFAULT_COUNTRY_CODES


SCORE_KEYS = (
    "overall_score",
    "product_market_fit_score",
    "demand_score",
    "competition_score",
    "pricing_score",
    "creative_fit_score",
    "landing_page_fit_score",
    "operational_fit_score",
    "compliance_risk_score",
)


def _array(item_schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item_schema}


PRODUCT_FACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "product_id",
        "product_name",
        "category_detected",
        "sku_facts",
        "price_facts",
        "dimension_facts",
        "material_facts",
        "feature_facts",
        "claim_inventory",
        "claim_consistency_risks",
        "missing_data",
        "assumptions",
        "generated_search_keywords",
    ],
    "properties": {
        "product_id": {"type": "string"},
        "product_name": {"type": "string", "nullable": True},
        "category_detected": {"type": "string", "nullable": True},
        "sku_facts": _array({"type": "object"}),
        "price_facts": _array({"type": "object"}),
        "dimension_facts": _array({"type": "object"}),
        "material_facts": _array({"type": "object"}),
        "feature_facts": _array({"type": "string"}),
        "claim_inventory": _array({"type": "object"}),
        "claim_consistency_risks": _array({"type": "string"}),
        "missing_data": _array({"type": "string"}),
        "assumptions": _array({"type": "string"}),
        "generated_search_keywords": {
            "type": "object",
            "required": ["english_keywords", "country_keyword_hints"],
            "properties": {
                "english_keywords": _array({"type": "string"}),
                "country_keyword_hints": {
                    "type": "object",
                    "properties": {
                        "DE": _array({"type": "string"}),
                        "FR": _array({"type": "string"}),
                        "IT": _array({"type": "string"}),
                        "ES": _array({"type": "string"}),
                        "JP": _array({"type": "string"}),
                    },
                },
            },
        },
    },
}


SCORES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": list(SCORE_KEYS),
    "properties": {
        key: {"type": "integer", "minimum": 0, "maximum": 100}
        for key in SCORE_KEYS
    },
}


COUNTRY_EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "country_code",
        "country_name",
        "country_name_zh",
        "status",
        "scores",
        "decision",
        "market_fit",
        "competitor_analysis",
        "pricing_analysis",
        "creative_fit",
        "landing_page_localization",
        "risks",
        "recommendations",
        "sources",
        "missing_data",
        "warnings",
    ],
    "properties": {
        "country_code": {"type": "string", "enum": list(DEFAULT_COUNTRY_CODES)},
        "country_name": {"type": "string"},
        "country_name_zh": {"type": "string"},
        "language": {"type": "string"},
        "currency": {"type": "string"},
        "status": {"type": "string", "enum": ["completed", "failed", "skipped"]},
        "scores": SCORES_SCHEMA,
        "decision": {
            "type": "object",
            "required": ["final_decision", "confidence", "one_sentence_reason", "why", "blocking_issues"],
            "properties": {
                "final_decision": {"type": "string", "enum": ["GO", "TEST", "HOLD"]},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "one_sentence_reason": {"type": "string"},
                "why": _array({"type": "string"}),
                "blocking_issues": _array({"type": "string"}),
            },
        },
        "market_fit": {"type": "object"},
        "competitor_analysis": {"type": "object"},
        "pricing_analysis": {"type": "object"},
        "creative_fit": {
            "type": "object",
            "properties": {
                "creative_missing": {"type": "boolean"},
                "final_creative_decision": {
                    "type": "string",
                    "enum": [
                        "USE_AS_IS",
                        "LOCALIZE_BEFORE_TEST",
                        "RESHOOT_PARTIALLY",
                        "DO_NOT_USE",
                        "NO_CREATIVE_PROVIDED",
                    ],
                },
            },
        },
        "landing_page_localization": {"type": "object"},
        "risks": {"type": "object"},
        "recommendations": {"type": "object"},
        "sources": _array({"type": "object"}),
        "missing_data": _array({"type": "string"}),
        "warnings": _array({"type": "string"}),
    },
}


PRODUCT_EVALUATION_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "evaluation_run_id",
        "product_id",
        "status",
        "product_snapshot",
        "product_facts",
        "summary",
        "countries",
        "frontend",
        "metadata",
    ],
    "properties": {
        "schema_version": {"type": "string"},
        "evaluation_run_id": {"type": "string"},
        "product_id": {"type": "string"},
        "status": {"type": "string", "enum": ["queued", "running", "completed", "partially_completed", "failed", "cancelled"]},
        "product_snapshot": {"type": "object"},
        "product_facts": PRODUCT_FACTS_SCHEMA,
        "summary": {"type": "object"},
        "countries": {"type": "object"},
        "frontend": {"type": "object"},
        "metadata": {"type": "object"},
    },
}


def validate_json_schema(data: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    """Small JSON Schema subset validator for runtime guardrails and tests."""
    if data is None and schema.get("nullable") is True:
        return
    _validate_type(data, schema.get("type"), path)
    if "enum" in schema and data not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}; got {data!r}")
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if "minimum" in schema and data < schema["minimum"]:
            raise ValueError(f"{path} must be >= {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            raise ValueError(f"{path} must be <= {schema['maximum']}")
    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                raise ValueError(f"{path}.{key} is required")
        properties = schema.get("properties") or {}
        for key, child_schema in properties.items():
            if key in data:
                validate_json_schema(data[key], child_schema, path=f"{path}.{key}")
    if isinstance(data, list) and schema.get("items"):
        for index, item in enumerate(data):
            validate_json_schema(item, schema["items"], path=f"{path}[{index}]")


def _validate_type(data: Any, expected: Any, path: str) -> None:
    if expected is None:
        return
    if isinstance(expected, list):
        if any(_is_type(data, item) for item in expected):
            return
        raise ValueError(f"{path} must be {expected}; got {type(data).__name__}")
    if not _is_type(data, expected):
        raise ValueError(f"{path} must be {expected}; got {type(data).__name__}")


def _is_type(data: Any, expected: str) -> bool:
    if expected == "null":
        return data is None
    if expected == "object":
        return isinstance(data, dict)
    if expected == "array":
        return isinstance(data, list)
    if expected == "string":
        return isinstance(data, str)
    if expected == "integer":
        return isinstance(data, int) and not isinstance(data, bool)
    if expected == "number":
        return isinstance(data, (int, float)) and not isinstance(data, bool)
    if expected == "boolean":
        return isinstance(data, bool)
    return True
