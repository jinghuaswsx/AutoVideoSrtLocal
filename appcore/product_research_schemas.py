"""JSON Schemas for single-product AI research structured output."""

from __future__ import annotations

import copy
from typing import Any

SCORE_KEYS: tuple[str, ...] = (
    "overall_score",
    "product_market_fit_score",
    "demand_score",
    "competition_score",
    "video_selling_fit_score",
    "main_image_fit_score",
    "landing_page_localization_score",
    "operational_fit_score",
    "risk_score",
)

VALID_COUNTRY_CODES: tuple[str, ...] = ("DE", "FR", "IT", "ES", "NL", "PT", "SE", "JP")
VALID_DECISIONS: tuple[str, ...] = ("GO", "TEST", "HOLD")
VALID_CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")
VALID_VIDEO_DECISIONS: tuple[str, ...] = ("USE_AS_IS", "LOCALIZE_BEFORE_TEST", "RESHOOT_PARTIALLY", "DO_NOT_USE")
VALID_IMAGE_DECISIONS: tuple[str, ...] = ("USE_AS_IS", "LOCALIZE_BEFORE_TEST", "REDESIGN")
VALID_SHIPPING_MODELS: tuple[str, ...] = ("free_shipping", "fixed_shipping", "threshold_free_shipping", "unknown")

PRODUCT_FACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "product_name": {"type": "string"},
        "brand": {"type": "string"},
        "category_detected": {"type": "string"},
        "subcategory_detected": {"type": "string"},
        "description_summary": {"type": "string"},
        "key_selling_points": {"type": "array", "items": {"type": "string"}},
        "features_and_specs": {"type": "array", "items": {"type": "string"}},
        "materials": {"type": "array", "items": {"type": "string"}},
        "claims": {"type": "array", "items": {"type": "string"}},
        "claim_consistency_risk": {"type": "string"},
        "target_audience": {"type": "array", "items": {"type": "string"}},
        "use_cases": {"type": "array", "items": {"type": "string"}},
        "search_keywords_en": {"type": "array", "items": {"type": "string"}},
        "search_keywords_by_country": {"type": "object"},
        "missing_data": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["product_name", "category_detected", "key_selling_points", "search_keywords_en", "missing_data"],
}

MEDIA_UNDERSTANDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "main_image_analysis": {
            "type": "object",
            "properties": {
                "product_clarity": {"type": "string"},
                "visual_quality": {"type": "string"},
                "text_on_image": {"type": "array", "items": {"type": "string"}},
                "claims_on_image": {"type": "array", "items": {"type": "string"}},
                "localization_risks": {"type": "array", "items": {"type": "string"}},
                "overall_assessment": {"type": "string"},
            },
        },
        "video_analysis": {
            "type": "object",
            "properties": {
                "duration_seconds": {"type": "number"},
                "timestamp_findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "timestamp_start": {"type": "number"},
                            "timestamp_end": {"type": "number"},
                            "segment_type": {"type": "string"},
                            "description": {"type": "string"},
                            "has_text_overlay": {"type": "boolean"},
                            "text_content": {"type": "string"},
                            "assessment": {"type": "string"},
                        },
                    },
                },
                "hook_analysis": {"type": "string"},
                "pain_point_addressed": {"type": "string"},
                "solution_presentation": {"type": "string"},
                "demo_quality": {"type": "string"},
                "before_after_present": {"type": "boolean"},
                "cta_analysis": {"type": "string"},
                "subtitles_detected": {"type": "boolean"},
                "narration_language": {"type": "string"},
                "visual_style": {"type": "string"},
                "claims_in_video": {"type": "array", "items": {"type": "string"}},
                "scenes_to_keep": {"type": "array", "items": {"type": "string"}},
                "scenes_to_replace_or_reshoot": {"type": "array", "items": {"type": "string"}},
                "overall_assessment": {"type": "string"},
            },
        },
        "missing_data": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}

COUNTRY_EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "country_code": {"type": "string"},
        "country_name": {"type": "string"},
        "country_name_zh": {"type": "string"},
        "language": {"type": "string"},
        "currency": {"type": "string"},
        "status": {"type": "string"},
        "scores": {
            "type": "object",
            "properties": {key: {"type": "integer"} for key in SCORE_KEYS},
        },
        "decision": {
            "type": "object",
            "properties": {
                "final_decision": {"type": "string"},
                "confidence": {"type": "string"},
                "one_sentence_reason": {"type": "string"},
                "why": {"type": "array", "items": {"type": "string"}},
                "blocking_issues": {"type": "array", "items": {"type": "string"}},
            },
        },
        "market_fit": {
            "type": "object",
            "properties": {
                "local_positioning": {"type": "string"},
                "target_segments": {"type": "array", "items": {"type": "string"}},
                "use_cases": {"type": "array", "items": {"type": "string"}},
                "demand_summary": {"type": "string"},
                "seasonality": {"type": "array", "items": {"type": "string"}},
                "market_entry_notes": {"type": "array", "items": {"type": "string"}},
            },
        },
        "competitor_pricing": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "competitors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "platform": {"type": "string"},
                            "url": {"type": "string"},
                            "price": {"type": ["number", "null"]},
                            "currency": {"type": "string"},
                            "shipping_fee": {"type": ["number", "null"]},
                            "rating": {"type": ["number", "null"]},
                            "review_count": {"type": ["integer", "null"]},
                            "key_features": {"type": "array", "items": {"type": "string"}},
                            "evidence_quality": {"type": "string"},
                        },
                    },
                },
                "price_band": {
                    "type": "object",
                    "properties": {
                        "min": {"type": ["number", "null"]},
                        "max": {"type": ["number", "null"]},
                        "median": {"type": ["number", "null"]},
                        "currency": {"type": "string"},
                    },
                },
                "evidence_gaps": {"type": "array", "items": {"type": "string"}},
            },
        },
        "pricing_strategy": {
            "type": "object",
            "properties": {
                "current_price_local": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": ["number", "null"]},
                        "currency": {"type": "string"},
                    },
                },
                "recommended_price": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": ["number", "null"]},
                        "currency": {"type": "string"},
                    },
                },
                "recommended_price_range": {
                    "type": "object",
                    "properties": {
                        "min": {"type": ["number", "null"]},
                        "max": {"type": ["number", "null"]},
                        "currency": {"type": "string"},
                    },
                },
                "recommended_price_ending": {"type": "string"},
                "margin_warnings": {"type": "array", "items": {"type": "string"}},
                "pricing_confidence": {"type": "string"},
            },
        },
        "shipping_strategy": {
            "type": "object",
            "properties": {
                "recommended_model": {"type": "string"},
                "customer_shipping_fee": {"type": ["number", "null"]},
                "free_shipping_threshold": {"type": ["number", "null"]},
                "currency": {"type": "string"},
                "reason": {"type": "string"},
                "missing_inputs": {"type": "array", "items": {"type": "string"}},
            },
        },
        "short_video_fit": {
            "type": "object",
            "properties": {
                "final_video_decision": {"type": "string"},
                "hook_fit": {"type": "string"},
                "local_language_fit": {"type": "string"},
                "cultural_fit": {"type": "string"},
                "claim_risks": {"type": "array", "items": {"type": "string"}},
                "scenes_to_keep": {"type": "array", "items": {"type": "string"}},
                "scenes_to_replace_or_reshoot": {"type": "array", "items": {"type": "string"}},
                "localized_hook_directions": {"type": "array", "items": {"type": "string"}},
                "localized_cta_directions": {"type": "array", "items": {"type": "string"}},
            },
        },
        "main_image_fit": {
            "type": "object",
            "properties": {
                "decision": {"type": "string"},
                "issues": {"type": "array", "items": {"type": "string"}},
                "localization_directions": {"type": "array", "items": {"type": "string"}},
            },
        },
        "landing_page_localization": {
            "type": "object",
            "properties": {
                "localization_difficulty": {"type": "integer"},
                "hero_direction": {"type": "string"},
                "sections_needed": {"type": "array", "items": {"type": "object"}},
                "trust_elements_needed": {"type": "array", "items": {"type": "string"}},
                "claims_to_avoid_or_rewrite": {"type": "array", "items": {"type": "string"}},
                "unit_and_currency_notes": {"type": "string"},
                "faq_directions": {"type": "string"},
            },
        },
        "risks": {
            "type": "object",
            "properties": {
                "claim_risks": {"type": "array", "items": {"type": "string"}},
                "compliance_risks": {"type": "array", "items": {"type": "string"}},
                "operational_risks": {"type": "array", "items": {"type": "string"}},
                "trust_risks": {"type": "array", "items": {"type": "string"}},
                "localization_risks": {"type": "array", "items": {"type": "string"}},
            },
        },
        "recommendations": {
            "type": "object",
            "properties": {
                "recommended_positioning": {"type": "string"},
                "ad_test_angles": {"type": "array", "items": {"type": "string"}},
                "creative_actions": {"type": "array", "items": {"type": "string"}},
                "pricing_actions": {"type": "array", "items": {"type": "string"}},
                "shipping_actions": {"type": "array", "items": {"type": "string"}},
                "landing_page_actions": {"type": "array", "items": {"type": "string"}},
                "first_30_day_test_plan": {
                    "type": "object",
                    "properties": {
                        "test_priority": {"type": "string"},
                        "creative_variants": {"type": "array", "items": {"type": "string"}},
                        "pricing_variants": {"type": "array", "items": {"type": "string"}},
                        "success_metrics": {"type": "array", "items": {"type": "string"}},
                        "kill_criteria": {"type": "array", "items": {"type": "string"}},
                        "scale_criteria": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "source_type": {"type": "string"},
                    "used_for": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "missing_data": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


def validate_json_schema(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    errors: list[str] = []
    _validate_object(instance, schema, "", errors)
    if errors:
        raise ValueError(f"Schema validation failed: {'; '.join(errors[:10])}")


def validate_scores(scores: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in SCORE_KEYS:
        value = scores.get(key)
        if value is None:
            continue
        try:
            v = int(value)
            if v < 0 or v > 100:
                errors.append(f"{key} must be 0-100, got {v}")
        except (TypeError, ValueError):
            errors.append(f"{key} must be an integer, got {type(value).__name__}")
    return errors


def validate_country_code(code: str) -> str | None:
    c = str(code or "").strip().upper()
    if c not in VALID_COUNTRY_CODES:
        return f"Invalid country_code: {code}"
    return None


def validate_decision(decision: str) -> str | None:
    d = str(decision or "").strip()
    if d not in VALID_DECISIONS:
        return f"Invalid final_decision: {decision}"
    return None


def validate_confidence(confidence: str) -> str | None:
    c = str(confidence or "").strip()
    if c not in VALID_CONFIDENCES:
        return f"Invalid confidence: {confidence}"
    return None


def _validate_object(instance: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if not isinstance(instance, dict):
        return
    required = schema.get("required") or []
    for key in required:
        if key not in instance:
            errors.append(f"{path}.{key}: required field missing")
    properties = schema.get("properties") or {}
    for key, prop_schema in properties.items():
        if key not in instance:
            continue
        value = instance[key]
        prop_type = prop_schema.get("type")
        field_path = f"{path}.{key}" if path else key
        if prop_type == "array":
            if isinstance(value, list) and "items" in prop_schema:
                items_schema = prop_schema["items"]
                if isinstance(items_schema, dict) and items_schema.get("type") == "object":
                    for idx, item in enumerate(value):
                        _validate_object(item, items_schema, f"{field_path}[{idx}]", errors)
        elif prop_type == "object":
            if isinstance(value, dict):
                _validate_object(value, prop_schema, field_path, errors)
        elif prop_type == "integer":
            if value is not None and not isinstance(value, int):
                errors.append(f"{field_path}: expected integer, got {type(value).__name__}")