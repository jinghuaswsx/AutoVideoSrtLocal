from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
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
EUROPE_FIT_TIMEOUT_SECONDS = 40
TARGET_MARKETS = ("Germany", "France", "Italy", "Spain")
TARGET_LANGUAGE_MARKETS = (
    ("Germany", "German"),
    ("France", "French"),
    ("Italy", "Italian"),
    ("Spain", "Spanish"),
)
RECOMMENDATIONS = {
    "translate_and_launch": "translate_and_launch",
    "translate": "translate_and_launch",
    "launch_after_translation": "translate_and_launch",
    "direct_reuse": "direct_reuse",
    "direct": "direct_reuse",
    "move_directly": "direct_reuse",
    "yes": "direct_reuse",
    "adapt_before_translation": "adapt_before_translation",
    "adapt_first": "adapt_before_translation",
    "adapt_then_translate": "adapt_before_translation",
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
        "Judge whether a short product-ad video and its product link are worth being translated and localized "
        "for Meta ads in Germany, France, Italy, Spain, and similar European markets. "
        "Be practical: consider product-market fit, visible product demo, spoken language, voiceover dependency, "
        "subtitle and on-screen text localization, claims/compliance risk, cultural fit, and localization effort before launch. "
        "Return only valid JSON matching the schema. "
        "All operator-facing explanation fields must be in Simplified Chinese."
    )


def build_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "suitability_score": {"type": "number", "minimum": 0, "maximum": 100},
            "recommendation": {
                "type": "string",
                "enum": ["translate_and_launch", "adapt_before_translation", "not_recommended"],
            },
            "direct_reuse": {"type": "boolean"},
            "translation_fit_score": {"type": "number", "minimum": 0, "maximum": 100},
            "best_countries": {
                "type": "array",
                "items": {"type": "string"},
            },
            "best_language_markets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "country": {"type": "string"},
                        "language": {"type": "string"},
                        "score": {"type": "number", "minimum": 0, "maximum": 100},
                        "notes": {"type": "string"},
                    },
                    "required": ["country", "language", "score"],
                    "additionalProperties": False,
                },
            },
            "country_scores": {
                "type": "object",
                "additionalProperties": {"type": "number"},
            },
            "source_language_detected": {"type": "string"},
            "speech_dependency": {"type": "string", "enum": ["none", "low", "medium", "high"]},
            "on_screen_text_dependency": {"type": "string", "enum": ["none", "low", "medium", "high"]},
            "needs_subtitle_translation": {"type": "boolean"},
            "needs_voiceover_or_dubbing": {"type": "boolean"},
            "needs_screen_text_replacement": {"type": "boolean"},
            "localization_difficulty": {"type": "string", "enum": ["low", "medium", "high"]},
            "country_localization_notes": {
                "type": "object",
                "additionalProperties": {
                    "type": "array",
                    "items": {"type": "string"},
                },
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
            "translation_fit_score",
            "best_countries",
            "best_language_markets",
            "country_scores",
            "source_language_detected",
            "speech_dependency",
            "on_screen_text_dependency",
            "needs_subtitle_translation",
            "needs_voiceover_or_dubbing",
            "needs_screen_text_replacement",
            "localization_difficulty",
            "country_localization_notes",
            "strengths",
            "risks",
            "required_changes",
            "reasoning",
        ],
        "additionalProperties": False,
    }


def _clean_html_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def build_prompt(row: Mapping[str, Any]) -> str:
    markets = ", ".join(f"{country}/{language}" for country, language in TARGET_LANGUAGE_MARKETS)
    post_copy = _clean_html_text(row.get("message_zh_html") or row.get("message_html"))
    return (
        "Evaluate whether this Meta hot-post material is worth translating and localizing for European Meta ads.\n\n"
        f"Target market/language pairs: {markets}, and similar EU Meta ecosystems.\n"
        f"Product URL: {row.get('product_url') or '-'}\n"
        f"Product title: {row.get('product_title') or '-'}\n"
        f"Product category: {row.get('category_l1') or '-'}\n"
        f"Price: {row.get('currency') or ''} {row.get('price_min') or '-'}"
        f"{' - ' + str(row.get('price_max')) if row.get('price_max') else ''}\n"
        f"Current interactions: {row.get('latest_likes') or '-'} likes/reactions, "
        f"{row.get('latest_comments') or '-'} comments, {row.get('latest_shares') or '-'} shares.\n"
        f"Interaction change: {row.get('sync_period_likes') or '-'} over "
        f"{row.get('sync_period_hours') or '-'} hours.\n"
        f"Post copy: {post_copy or '-'}\n\n"
        "The attached video has been compressed for LLM review. Decide if the material can be "
        "translated/localized and launched, adapted before translation, or rejected. Evaluate spoken language, "
        "subtitle needs, voiceover/dubbing needs, on-screen text replacement, product-market fit, ad-policy risk, "
        "country-specific cultural risk, and how hard it would be to turn this into German, French, Italian, and Spanish variants.\n\n"
        "Return strengths, risks, required_changes, and reasoning in 简体中文 for Chinese ecommerce operators. "
        "Keep recommendation as one of translate_and_launch, adapt_before_translation, or not_recommended. "
        "best_countries may use country names or country codes."
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


def _language_market_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        country = str(item.get("country") or "").strip()
        language = str(item.get("language") or "").strip()
        if not country and not language:
            continue
        result.append(
            {
                "country": country[:64],
                "language": language[:64],
                "score": _score(item.get("score")),
                "notes": str(item.get("notes") or "").strip()[:500],
            }
        )
        if len(result) >= 8:
            break
    return result


def _notes_by_country(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, list[str]] = {}
    for key, notes in value.items():
        country = str(key or "").strip()
        if country:
            result[country[:64]] = _text_list(notes, limit=6)
    return result


def _enum_text(value: Any, *, allowed: set[str], default: str = "") -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text if text in allowed else default


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
        return "translate_and_launch"
    if score >= 60:
        return "adapt_before_translation"
    return "not_recommended"


def normalize_assessment_response(response: Mapping[str, Any]) -> dict[str, Any]:
    payload = _payload(response)
    score = _score(payload.get("suitability_score") or payload.get("score"))
    translation_score = _score(payload.get("translation_fit_score") or payload.get("translation_score"))
    direct_reuse = _bool(payload.get("direct_reuse"))
    recommendation = _recommendation(payload.get("recommendation"), score=score, direct_reuse=direct_reuse)
    if recommendation in {"direct_reuse", "translate_and_launch"}:
        direct_reuse = True
    elif recommendation == "not_recommended":
        direct_reuse = False
    return {
        "suitability_score": score,
        "recommendation": recommendation,
        "direct_reuse": direct_reuse,
        "translation_fit_score": translation_score,
        "best_countries": _text_list(payload.get("best_countries"), limit=6),
        "best_language_markets": _language_market_list(payload.get("best_language_markets")),
        "country_scores": _country_scores(payload.get("country_scores")),
        "source_language_detected": str(payload.get("source_language_detected") or "").strip()[:64],
        "speech_dependency": _enum_text(
            payload.get("speech_dependency"),
            allowed={"none", "low", "medium", "high"},
            default="medium",
        ),
        "on_screen_text_dependency": _enum_text(
            payload.get("on_screen_text_dependency"),
            allowed={"none", "low", "medium", "high"},
            default="medium",
        ),
        "needs_subtitle_translation": _bool(payload.get("needs_subtitle_translation")),
        "needs_voiceover_or_dubbing": _bool(payload.get("needs_voiceover_or_dubbing")),
        "needs_screen_text_replacement": _bool(payload.get("needs_screen_text_replacement")),
        "localization_difficulty": _enum_text(
            payload.get("localization_difficulty"),
            allowed={"low", "medium", "high"},
            default="medium",
        ),
        "country_localization_notes": _notes_by_country(payload.get("country_localization_notes")),
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
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                invoke,
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
            response = future.result(timeout=EUROPE_FIT_TIMEOUT_SECONDS)
    finally:
        cleanup_optimized_media(media_input)

    result = normalize_assessment_response(response)
    result["video_optimization"] = optimization
    return result
