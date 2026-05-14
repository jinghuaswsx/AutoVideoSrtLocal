from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from appcore import llm_client
from appcore.llm_media_optimizer import (
    REVIEW_480P_AUDIO,
    cleanup_optimized_media,
    media_debug_snapshot,
    prepare_video_for_llm,
)
from appcore.meta_hot_posts import video_localization


USE_CASE_CODE = "meta_hot_posts.europe_fit"
EUROPE_FIT_PROVIDER = "openrouter"
EUROPE_FIT_MODEL = "google/gemini-3-flash-preview"
TARGET_MARKETS = ("Germany", "France", "Italy", "Spain")
RECOMMENDATIONS = {
    "direct_reuse": "direct_reuse",
    "direct": "direct_reuse",
    "move_directly": "direct_reuse",
    "yes": "direct_reuse",
    "adapt_before_use": "adapt_before_use",
    "needs_adaptation": "adapt_before_use",
    "adapt": "adapt_before_use",
    "not_recommended": "not_recommended",
    "no": "not_recommended",
    "reject": "not_recommended",
}


class EuropeFitAssessmentError(RuntimeError):
    pass


def build_system_prompt() -> str:
    return (
        "You are a senior Meta performance creative reviewer for European e-commerce. "
        "Judge whether a short product-ad video and its product link can be directly moved "
        "into Meta ads for Germany, France, Italy, Spain, and similar European markets. "
        "Be practical: consider product-market fit, visual clarity, spoken language, on-screen text, "
        "claims/compliance risk, cultural fit, and whether the ad needs localization before launch. "
        "Return only valid JSON matching the schema."
    )


def build_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "suitability_score": {"type": "number", "minimum": 0, "maximum": 100},
            "recommendation": {
                "type": "string",
                "enum": ["direct_reuse", "adapt_before_use", "not_recommended"],
            },
            "direct_reuse": {"type": "boolean"},
            "best_countries": {
                "type": "array",
                "items": {"type": "string"},
            },
            "country_scores": {
                "type": "object",
                "additionalProperties": {"type": "number"},
            },
            "strengths": {
                "type": "array",
                "items": {"type": "string"},
            },
            "risks": {
                "type": "array",
                "items": {"type": "string"},
            },
            "required_changes": {
                "type": "array",
                "items": {"type": "string"},
            },
            "reasoning": {"type": "string"},
        },
        "required": [
            "suitability_score",
            "recommendation",
            "direct_reuse",
            "best_countries",
            "country_scores",
            "strengths",
            "risks",
            "required_changes",
            "reasoning",
        ],
        "additionalProperties": False,
    }


def build_prompt(row: Mapping[str, Any]) -> str:
    markets = ", ".join(TARGET_MARKETS)
    return (
        "Evaluate whether this Meta hot-post material can be directly moved into European Meta ads.\n\n"
        f"Target markets: {markets}, and similar EU Meta ecosystems.\n"
        f"Product URL: {row.get('product_url') or '-'}\n"
        f"Product title: {row.get('product_title') or '-'}\n"
        f"Product category: {row.get('category_l1') or '-'}\n"
        f"Price: {row.get('currency') or ''} {row.get('price_min') or '-'}"
        f"{' - ' + str(row.get('price_max')) if row.get('price_max') else ''}\n"
        f"Current interactions: {row.get('latest_likes') or '-'} likes/reactions, "
        f"{row.get('latest_comments') or '-'} comments, {row.get('latest_shares') or '-'} shares.\n"
        f"Interaction change: {row.get('sync_period_likes') or '-'} over "
        f"{row.get('sync_period_hours') or '-'} hours.\n\n"
        "The attached video has been compressed for LLM review. Decide if the material can be "
        "used as-is, directly moved with only normal campaign setup, or must be adapted first. "
        "Prefer strict judgments for medical claims, exaggerated results, unsafe demonstrations, "
        "language/caption mismatch, platform policy risk, and country-specific cultural risk."
    )


def _json_from_text(text: str) -> dict[str, Any]:
    body = str(text or "").strip()
    if not body:
        return {}
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*", "", body, flags=re.I)
        body = re.sub(r"\s*```$", "", body)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", body, re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _payload(response: Mapping[str, Any]) -> dict[str, Any]:
    raw = response.get("json")
    if isinstance(raw, Mapping):
        return dict(raw)
    return _json_from_text(str(response.get("text") or ""))


def _score(value: Any) -> int:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return int(max(0.0, min(100.0, round(parsed))))


def _text_list(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = []
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            result.append(text[:500])
        if len(result) >= limit:
            break
    return result


def _country_scores(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, score in value.items():
        country = str(key or "").strip().upper()
        if country:
            result[country[:32]] = _score(score)
    return result


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "direct", "direct_reuse"}
    return False


def _recommendation(value: Any, *, score: int, direct_reuse: bool) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    mapped = RECOMMENDATIONS.get(normalized)
    if mapped:
        return mapped
    if direct_reuse and score >= 80:
        return "direct_reuse"
    if score >= 60:
        return "adapt_before_use"
    return "not_recommended"


def normalize_assessment_response(response: Mapping[str, Any]) -> dict[str, Any]:
    payload = _payload(response)
    score = _score(payload.get("suitability_score") or payload.get("score"))
    direct_reuse = _bool(payload.get("direct_reuse"))
    recommendation = _recommendation(payload.get("recommendation"), score=score, direct_reuse=direct_reuse)
    if recommendation == "direct_reuse":
        direct_reuse = True
    elif recommendation == "not_recommended":
        direct_reuse = False
    return {
        "suitability_score": score,
        "recommendation": recommendation,
        "direct_reuse": direct_reuse,
        "best_countries": _text_list(payload.get("best_countries"), limit=6),
        "country_scores": _country_scores(payload.get("country_scores")),
        "strengths": _text_list(payload.get("strengths"), limit=8),
        "risks": _text_list(payload.get("risks"), limit=8),
        "required_changes": _text_list(payload.get("required_changes"), limit=8),
        "reasoning": str(payload.get("reasoning") or "").strip()[:2000],
        "provider": response.get("provider") or EUROPE_FIT_PROVIDER,
        "model": response.get("model") or EUROPE_FIT_MODEL,
        "raw_response": dict(response),
    }


def assess_material(
    row: Mapping[str, Any],
    *,
    user_id: int | None = None,
    invoke_fn=None,
) -> dict[str, Any]:
    local_video_path = str(row.get("local_video_path") or "").strip()
    resolved = video_localization.resolve_local_video_path(local_video_path)
    if resolved is None:
        raise EuropeFitAssessmentError("local video is missing or outside the hot-post cache")

    media_input = prepare_video_for_llm(
        str(resolved),
        REVIEW_480P_AUDIO,
        output_dir=Path(resolved).parent,
    )
    optimization = media_debug_snapshot(media_input)
    try:
        invoke = invoke_fn or llm_client.invoke_generate
        response = invoke(
            USE_CASE_CODE,
            prompt=build_prompt(row),
            system=build_system_prompt(),
            media=[media_input.llm_path],
            user_id=user_id,
            project_id=f"meta-hot-post-{row.get('id') or 'unknown'}",
            response_schema=build_response_schema(),
            temperature=0.1,
            max_output_tokens=2048,
            provider_override=EUROPE_FIT_PROVIDER,
            model_override=EUROPE_FIT_MODEL,
            billing_extra={"source": "meta_hot_posts", "target_market": "europe"},
        )
    finally:
        cleanup_optimized_media(media_input)

    result = normalize_assessment_response(response)
    result["video_optimization"] = optimization
    return result
