"""Compatibility layer: fine AI evaluation result -> legacy material_evaluation format.

Maps the structured fine AI evaluation result (per-country with nested scores,
decision objects, risks, recommendations) into the flat legacy format stored in
``media_products.ai_evaluation_detail``.

Docs-anchor: implementation_plan.md
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

_DECISION_MAP = {
    "GO": "适合推广",
    "TEST": "谨慎推广",
    "HOLD": "不适合推广",
}

_RECOMMENDATION_MAP = {
    "GO": "做",
    "TEST": "做",
    "HOLD": "不做",
}

_COUNTRY_TO_LANG = {
    "DE": "de",
    "FR": "fr",
    "IT": "it",
    "ES": "es",
    "JP": "ja",
}

_LANG_TO_COUNTRY_NAME = {
    "de": "德国",
    "fr": "法国",
    "it": "意大利",
    "es": "西班牙",
    "ja": "日本",
}

_LANG_TO_LANGUAGE_NAME = {
    "de": "德语",
    "fr": "法语",
    "it": "意大利语",
    "es": "西班牙语",
    "ja": "日语",
}


def fine_ai_result_to_legacy(
    fine_result: dict[str, Any],
    *,
    product_id: int | str | None = None,
    product_url: str = "",
) -> dict[str, Any]:
    """Convert a fine AI evaluation result payload into the legacy format."""
    countries_raw = fine_result.get("countries") or {}
    metadata = fine_result.get("metadata") or {}
    summary = fine_result.get("summary") or {}

    legacy_countries: list[dict[str, Any]] = []
    for code, country in _iter_countries(countries_raw):
        lang = _COUNTRY_TO_LANG.get(code, code.lower())
        scores = country.get("scores") or {}
        decision_obj = country.get("decision") or {}
        final_decision = str(decision_obj.get("final_decision") or "HOLD").upper()

        overall_score = _coerce_score(scores.get("overall_score"))
        legacy_decision = _DECISION_MAP.get(final_decision, "谨慎推广")
        legacy_recommendation = _RECOMMENDATION_MAP.get(final_decision, "不做")

        reason = str(
            decision_obj.get("one_sentence_reason")
            or _first_text(decision_obj.get("why"))
            or ""
        ).strip()[:100] or "见精细评估详情"

        summary_text = reason[:120]

        recs = country.get("recommendations") or {}
        risks = country.get("risks") or {}
        suggestions = _build_suggestions(recs, risks, country.get("missing_data"))

        is_suitable = final_decision == "GO" or (final_decision == "TEST" and overall_score >= 60)
        risk_level = _risk_level_from_decision(final_decision, overall_score)

        legacy_countries.append({
            "lang": lang,
            "language": _LANG_TO_LANGUAGE_NAME.get(lang, lang),
            "country": country.get("country_name_zh") or _LANG_TO_COUNTRY_NAME.get(lang, code),
            "is_suitable": is_suitable,
            "score": overall_score,
            "risk_level": risk_level,
            "decision": legacy_decision,
            "recommendation": legacy_recommendation,
            "summary": summary_text,
            "reason": reason,
            "suggestions": suggestions,
        })

    scores = [row["score"] for row in legacy_countries if row["score"] is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None
    suitable_count = sum(1 for row in legacy_countries if row["is_suitable"])

    if not legacy_countries:
        evaluation_result = "评估失败"
    elif suitable_count == len(legacy_countries):
        evaluation_result = "适合推广"
    elif suitable_count > 0:
        evaluation_result = "部分适合推广"
    else:
        evaluation_result = "不适合推广"

    detail = {
        "schema_version": 2,
        "source": "fine_ai_evaluation",
        "evaluation_run_id": fine_result.get("evaluation_run_id") or "",
        "channel": metadata.get("channel") or "adc",
        "use_case": "fine_ai_evaluation",
        "provider": metadata.get("provider") or "",
        "model": metadata.get("model") or "",
        "evaluated_at": fine_result.get("completed_at") or datetime.now(UTC).isoformat(),
        "product_id": product_id or fine_result.get("product_id") or "",
        "product_url": product_url or (fine_result.get("product_snapshot") or {}).get("product_url") or "",
        "countries": legacy_countries,
        "fine_ai_summary": summary,
    }

    return {
        "ai_score": avg_score,
        "ai_evaluation_result": evaluation_result,
        "ai_evaluation_detail": detail,
    }


def fine_ai_result_to_legacy_json(
    fine_result: dict[str, Any],
    **kwargs,
) -> dict[str, Any]:
    """Same as ``fine_ai_result_to_legacy`` but serializes detail to JSON string."""
    result = fine_ai_result_to_legacy(fine_result, **kwargs)
    result["ai_evaluation_detail"] = json.dumps(
        result["ai_evaluation_detail"], ensure_ascii=False
    )
    return result


def _iter_countries(
    countries: dict[str, Any] | list[Any],
) -> list[tuple[str, dict[str, Any]]]:
    preferred = ["DE", "FR", "IT", "ES", "JP"]
    if isinstance(countries, dict):
        by_code = {}
        for code, data in countries.items():
            if isinstance(data, dict):
                normalized_code = str(data.get("country_code") or code).strip().upper()
                by_code[normalized_code] = data
        result = [(code, by_code[code]) for code in preferred if code in by_code]
        for code, data in by_code.items():
            if code not in preferred:
                result.append((code, data))
        return result
    if isinstance(countries, list):
        by_code: dict[str, dict] = {}
        for item in countries:
            if isinstance(item, dict):
                code = str(
                    item.get("country_code") or item.get("lang") or item.get("code") or ""
                ).strip().upper()
                if code and code not in by_code:
                    by_code[code] = item
        result = [(code, by_code[code]) for code in preferred if code in by_code]
        for code, data in by_code.items():
            if code not in preferred:
                result.append((code, data))
        return result
    return []


def _coerce_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(100.0, round(score, 1)))


def _first_text(values: Any) -> str:
    if isinstance(values, str):
        return values
    if isinstance(values, list):
        for item in values:
            text = str(item or "").strip()
            if text:
                return text
    return ""


def _risk_level_from_decision(decision: str, score: float) -> str:
    if decision == "HOLD" or score < 50:
        return "high"
    if decision == "TEST" or score < 70:
        return "medium"
    return "low"


def _build_suggestions(
    recs: dict[str, Any],
    risks: dict[str, Any],
    missing_data: list[Any] | None,
) -> list[str]:
    items: list[str] = []
    for key in ("creative_actions", "landing_page_actions", "ad_test_angles"):
        for item in recs.get(key) or []:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
            if len(items) >= 3:
                return items
    for group in ("claim_risks", "compliance_risks", "operational_risks"):
        for item in risks.get(group) or []:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
            if len(items) >= 3:
                return items
    for item in missing_data or []:
        text = f"补充数据：{str(item or '').strip()}"
        if text not in items:
            items.append(text)
        if len(items) >= 3:
            return items
    return items[:3]
