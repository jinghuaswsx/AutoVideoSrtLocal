from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from appcore import api_keys
from appcore.meta_hot_posts import (
    categories,
    europe_fit,
    europe_fit_translation,
    product_analysis,
    product_title_translation,
    store,
    video_copyability,
    video_copyability_translation,
    video_localization,
)

MARK_STATUS_OK = "ok"
MARK_STATUS_BAD = "bad"
AI_VISIBILITY_PREF_SERVICE = "meta_hot_posts_ai_visibility"
DEFAULT_AI_ANALYSIS_VISIBILITY = {"us": False, "europe": False}
MANUAL_AI_TRANSLATE_PROVIDER = "openrouter"
MANUAL_AI_TRANSLATE_MODEL = "google/gemini-3.1-flash-lite"
MANUAL_US_AI_TRANSLATE_SOURCE = "meta_hot_posts_manual_us_ai_translate_zh"
MANUAL_EUROPE_AI_TRANSLATE_SOURCE = "meta_hot_posts_manual_europe_ai_translate_zh"


@dataclass(frozen=True)
class MetaHotPostsResponse:
    payload: dict[str, Any]
    status_code: int = 200


@dataclass(frozen=True)
class LocalVideoResponse:
    path: Path | None
    status_code: int = 200
    error: str | None = None


def _decode_sku_json(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, str) and value:
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [dict(item) for item in parsed if isinstance(item, dict)]
    return []

def _decode_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _decode_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _decode_json_dict(value: Any) -> dict[str, Any]:
    return _decode_json_object(value)


def _pop_video_copyability_payload(
    item: dict[str, Any],
    *,
    prefixed: bool,
) -> dict[str, Any] | None:
    if prefixed:
        keys = {
            "analysis_id": "video_copyability_analysis_id",
            "overall_score": "video_copyability_overall_score",
            "copyability_score": "video_copyability_copyability_score",
            "meta_us_ad_fit_score": "video_copyability_meta_us_ad_fit_score",
            "product_fit_score": "video_copyability_product_fit_score",
            "compliance_risk_score": "video_copyability_compliance_risk_score",
            "recommendation": "video_copyability_recommendation",
            "summary": "video_copyability_summary",
            "summary_zh": "video_copyability_summary_zh",
            "provider": "video_copyability_provider",
            "model": "video_copyability_model",
            "analyzed_at": "video_copyability_analyzed_at",
            "analysis_json": "video_copyability_analysis_json",
        }
    else:
        keys = {
            "analysis_id": "analysis_id",
            "overall_score": "overall_score",
            "copyability_score": "copyability_score",
            "meta_us_ad_fit_score": "meta_us_ad_fit_score",
            "product_fit_score": "product_fit_score",
            "compliance_risk_score": "compliance_risk_score",
            "recommendation": "recommendation",
            "summary": "summary",
            "summary_zh": "summary_zh",
            "provider": "llm_provider",
            "model": "llm_model",
            "analyzed_at": "analyzed_at",
            "analysis_json": "analysis_json",
        }
    if prefixed:
        values = {name: item.pop(key, None) for name, key in keys.items()}
    else:
        values = {name: item.get(key) for name, key in keys.items()}
    has_payload = any(
        values.get(name) not in (None, "")
        for name in (
            "overall_score",
            "copyability_score",
            "meta_us_ad_fit_score",
            "summary",
        )
    )
    if not has_payload:
        return None
    analysis = _decode_json_object(values.pop("analysis_json", None))
    return {**values, "raw": analysis}


def _duration_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if ":" in text:
            parts = text.split(":")
            try:
                total = 0.0
                for part in parts:
                    total = total * 60 + float(part)
                return total
            except ValueError:
                return None
        value = text
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if duration > 0 else None


def _extract_video_duration_seconds(payload: Mapping[str, Any] | None) -> int | None:
    if not payload:
        return None
    second_keys = (
        "video_duration_seconds",
        "duration_seconds",
        "videoDurationSeconds",
        "durationSeconds",
        "video_duration",
        "duration",
        "videoLength",
        "length",
    )
    millisecond_keys = (
        "video_duration_ms",
        "duration_ms",
        "videoDuration",
        "videoDurationMs",
        "video_length_ms",
        "videoLengthMs",
    )
    for key in second_keys:
        duration = _duration_number(payload.get(key))
        if duration is not None:
            return int(round(duration))
    for key in millisecond_keys:
        duration = _duration_number(payload.get(key))
        if duration is not None:
            return int(round(duration / 1000))
    for key in ("video_meta", "video_info", "videoData", "media"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            duration = _extract_video_duration_seconds(nested)
            if duration is not None:
                return duration
    video = payload.get("video")
    if isinstance(video, Mapping):
        return _extract_video_duration_seconds(video)
    return None


def _bool_payload(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "checked", "marked"}
    return False


def _int_payload(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_ai_visibility(value: Mapping[str, Any] | None) -> dict[str, bool]:
    source = value or {}
    return {
        "us": _bool_payload(source.get("us", source.get("show_us", source.get("show_us_ai_analysis")))),
        "europe": _bool_payload(
            source.get("europe", source.get("show_europe", source.get("show_europe_ai_analysis")))
        ),
    }


def _ai_analysis_visibility_payload_for_user(user_id: int | None) -> Mapping[str, Any]:
    if not user_id:
        return {}
    raw = api_keys.get_key(int(user_id), AI_VISIBILITY_PREF_SERVICE)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def ai_analysis_visibility_for_user(user_id: int | None) -> dict[str, bool]:
    if not user_id:
        return dict(DEFAULT_AI_ANALYSIS_VISIBILITY)
    return _normalize_ai_visibility(_ai_analysis_visibility_payload_for_user(user_id))


def build_ai_analysis_visibility_response(user_id: int | None) -> MetaHotPostsResponse:
    if not user_id:
        return MetaHotPostsResponse({"error": "missing_user"}, 400)
    return MetaHotPostsResponse(
        {"ok": True, "preferences": ai_analysis_visibility_for_user(int(user_id))}
    )


def build_ai_analysis_visibility_update_response(
    payload: Mapping[str, Any] | None,
    *,
    user_id: int | None,
) -> MetaHotPostsResponse:
    if not user_id:
        return MetaHotPostsResponse({"error": "missing_user"}, 400)
    payload = payload or {}
    source = payload.get("preferences") if isinstance(payload.get("preferences"), Mapping) else payload
    preferences = _normalize_ai_visibility(source if isinstance(source, Mapping) else {})
    client_id = str(payload.get("client_id") or payload.get("clientId") or "").strip()
    save_version = _int_payload(payload.get("save_version", payload.get("saveVersion")))
    existing_payload = _ai_analysis_visibility_payload_for_user(int(user_id))
    existing_client_id = str(existing_payload.get("_client_id") or "").strip()
    existing_save_version = _int_payload(existing_payload.get("_save_version"))
    if (
        client_id
        and client_id == existing_client_id
        and save_version is not None
        and existing_save_version is not None
        and save_version < existing_save_version
    ):
        return MetaHotPostsResponse(
            {"ok": True, "preferences": _normalize_ai_visibility(existing_payload)}
        )
    stored_preferences: dict[str, Any] = dict(preferences)
    if client_id:
        stored_preferences["_client_id"] = client_id
    if save_version is not None:
        stored_preferences["_save_version"] = save_version
    api_keys.set_key(
        int(user_id),
        AI_VISIBILITY_PREF_SERVICE,
        json.dumps(stored_preferences, ensure_ascii=False, sort_keys=True),
    )
    return MetaHotPostsResponse({"ok": True, "preferences": preferences})


def _normalize_mark_status(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    if raw in {MARK_STATUS_OK, "pass", "yes", "行"}:
        return MARK_STATUS_OK
    if raw in {MARK_STATUS_BAD, "fail", "no", "不行"}:
        return MARK_STATUS_BAD
    return None


def _hydrate_item(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    raw_json = _decode_json_object(item.pop("raw_json", None))
    item["sku_prices"] = _decode_sku_json(item.pop("sku_prices_json", None))
    item["sku_count"] = len(item["sku_prices"])
    item.setdefault("analysis_status", "pending")
    persisted_duration = _duration_number(item.get("local_video_duration_seconds"))
    item["video_duration_seconds"] = (
        int(round(persisted_duration))
        if persisted_duration is not None
        else _extract_video_duration_seconds(raw_json)
    )
    item["category_l1_zh"] = categories.category_label_zh(item.get("category_l1"))
    product_title = str(item.get("product_title") or "").strip()
    product_title_zh = str(item.get("product_title_zh") or "").strip()
    item["product_title"] = product_title
    item["product_title_zh"] = product_title_zh
    item["product_title_display"] = product_title_zh or product_title
    item["product_title_is_translated"] = bool(product_title_zh)
    source_message = str(item.get("message_html") or "")
    translated_message = str(item.get("message_zh_html") or "").strip()
    item["message_source_html"] = source_message
    item["message_is_translated"] = bool(translated_message)
    if translated_message:
        item["message_html"] = translated_message
    if (
        item.get("id")
        and item.get("local_video_status") == "downloaded"
        and item.get("local_video_path")
    ):
        item["local_video_url"] = f"/xuanpin/api/meta-hot-posts/{int(item['id'])}/local-video"
        item["local_video_cover_url"] = (
            f"/xuanpin/api/meta-hot-posts/{int(item['id'])}/local-video-cover"
            if item.get("local_video_cover_path")
            else ""
        )
        from appcore import tos_backup_storage
        from appcore.meta_hot_posts import tos_sync
        import config
        if config.TOS_BACKUP_ENABLED:
            try:
                tos_key = tos_sync.backup_object_key_for_relative_path(item["local_video_path"])
                if not tos_key:
                    raise ValueError("invalid local video path")
                item["tos_video_url"] = tos_backup_storage.generate_signed_download_url(tos_key)
            except Exception:
                item["tos_video_url"] = ""
            try:
                cover_key = tos_sync.backup_object_key_for_relative_path(
                    item.get("local_video_cover_path")
                )
                item["tos_video_cover_url"] = (
                    tos_backup_storage.generate_signed_download_url(cover_key)
                    if cover_key
                    else ""
                )
            except Exception:
                item["tos_video_cover_url"] = ""
        else:
            item["tos_video_url"] = ""
            item["tos_video_cover_url"] = ""
    else:
        item["local_video_url"] = ""
        item["local_video_cover_url"] = ""
        item["tos_video_url"] = ""
        item["tos_video_cover_url"] = ""
    mark_status = _normalize_mark_status(item.get("mark_status"))
    if not mark_status and _bool_payload(item.get("is_marked")):
        mark_status = MARK_STATUS_BAD
    item["mark_status"] = mark_status
    item["is_marked"] = bool(mark_status)
    item["is_pushed"] = _bool_payload(item.get("is_pushed"))
    if item.get("is_favorited") in (None, ""):
        item["is_favorited"] = bool(item.get("favorited_at"))
    else:
        item["is_favorited"] = _bool_payload(item.get("is_favorited"))
    item["europe_fit_best_countries"] = _decode_json_list(
        item.pop("europe_fit_best_countries_json", None)
    )
    item["europe_fit_country_scores"] = _decode_json_dict(
        item.pop("europe_fit_country_scores_json", None)
    )
    item["europe_fit_strengths"] = _decode_json_list(
        item.pop("europe_fit_strengths_json", None)
    )
    item["europe_fit_strengths_zh"] = _decode_json_list(
        item.pop("europe_fit_strengths_zh_json", None)
    )
    item["europe_fit_risks"] = _decode_json_list(
        item.pop("europe_fit_risks_json", None)
    )
    item["europe_fit_risks_zh"] = _decode_json_list(
        item.pop("europe_fit_risks_zh_json", None)
    )
    item["europe_fit_required_changes"] = _decode_json_list(
        item.pop("europe_fit_required_changes_json", None)
    )
    item["europe_fit_required_changes_zh"] = _decode_json_list(
        item.pop("europe_fit_required_changes_zh_json", None)
    )
    item["europe_fit_video_optimization"] = _decode_json_dict(
        item.pop("europe_fit_video_optimization_json", None)
    )
    item["europe_fit_raw_response"] = _decode_json_dict(
        item.pop("europe_fit_llm_response_json", None)
    )
    if "europe_fit_direct_reuse" in item:
        item["europe_fit_direct_reuse"] = _bool_payload(item.get("europe_fit_direct_reuse"))
    video_copyability = _pop_video_copyability_payload(item, prefixed=True)
    if video_copyability:
        item["video_copyability"] = video_copyability
    return item


def _hydrate_video_copyability_item(row: Mapping[str, Any]) -> dict[str, Any]:
    item = _hydrate_item(row)
    item["video_copyability"] = _pop_video_copyability_payload(item, prefixed=False) or {}
    return item


def _hydrate_product_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    category = str(item.get("category_l1") or "").strip()
    product_title = str(item.get("product_title") or "").strip()
    product_title_zh = str(item.get("product_title_zh") or "").strip()
    product_url = str(item.get("product_url") or "").strip()
    item["category_l1"] = category
    item["category_l1_zh"] = categories.category_label_zh(category) or "未分类"
    item["product_title"] = product_title
    item["product_title_zh"] = product_title_zh
    item["product_title_display"] = product_title_zh or product_title or product_url or "未命名产品"
    item["material_count"] = _int_payload(item.get("material_count")) or 0
    return item


def build_list_response(
    args: Mapping[str, Any],
    *,
    user_id: int | None = None,
) -> MetaHotPostsResponse:
    payload = (
        store.list_hot_posts(args, user_id=user_id)
        if user_id
        else store.list_hot_posts(args)
    )
    payload["items"] = [_hydrate_item(item) for item in payload.get("items") or []]
    return MetaHotPostsResponse(payload)


def build_today_new_response(
    args: Mapping[str, Any],
    *,
    user_id: int | None = None,
) -> MetaHotPostsResponse:
    payload = (
        store.list_today_new_hot_posts(args, user_id=user_id)
        if user_id
        else store.list_today_new_hot_posts(args)
    )
    payload["items"] = [_hydrate_item(item) for item in payload.get("items") or []]
    return MetaHotPostsResponse(payload)


def build_favorites_response(
    args: Mapping[str, Any],
    *,
    user_id: int | None,
) -> MetaHotPostsResponse:
    if not user_id:
        return MetaHotPostsResponse({"error": "missing_user"}, 400)
    payload = store.list_favorite_hot_posts(args, user_id=int(user_id))
    payload["items"] = [_hydrate_item(item) for item in payload.get("items") or []]
    return MetaHotPostsResponse(payload)


def build_product_list_response(args: Mapping[str, Any]) -> MetaHotPostsResponse:
    payload = store.list_product_summaries(args)
    payload["items"] = [
        _hydrate_product_summary(item)
        for item in payload.get("items") or []
    ]
    return MetaHotPostsResponse(payload)


def build_europe_top_response(
    args: Mapping[str, Any],
    *,
    user_id: int | None = None,
) -> MetaHotPostsResponse:
    try:
        limit = int(args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    items = (
        store.list_top_europe_fit_materials(limit=limit, user_id=user_id)
        if user_id
        else store.list_top_europe_fit_materials(limit=limit)
    )
    hydrated = [_hydrate_item(item) for item in items]
    return MetaHotPostsResponse({"items": hydrated, "total": len(hydrated), "limit": max(1, min(50, limit))})


def category_options() -> list[dict[str, Any]]:
    dynamic = store.list_category_options()
    if dynamic:
        seen = {str(item.get("value") or "") for item in dynamic}
        hydrated = [
            categories.category_option(item.get("value") or item.get("label"))
            for item in dynamic
        ]
        return hydrated + [item for item in categories.category_options() if item["value"] not in seen]
    return categories.category_options()


def build_category_options_response() -> MetaHotPostsResponse:
    return MetaHotPostsResponse({"items": category_options()})


def build_category_prompt_response() -> MetaHotPostsResponse:
    prompt = product_analysis.build_category_prompt(
        product_title="{product_title}",
        product_url="{product_url}",
    )
    return MetaHotPostsResponse(
        {
            "prompt": prompt,
            "categories": categories.TIKTOK_SHOP_US_L1_CATEGORIES,
            "use_case": "meta_hot_posts.categorize",
            "model": product_analysis.CATEGORY_MODEL,
            "provider": product_analysis.CATEGORY_PROVIDER,
        }
    )


def build_failures_response(args: Mapping[str, Any]) -> MetaHotPostsResponse:
    try:
        limit = int(args.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    items = store.list_failed_product_analyses(limit=limit)
    return MetaHotPostsResponse({"items": items, "total": len(items), "limit": max(1, min(100, limit))})


def build_mark_response(
    post_id: int,
    payload: Mapping[str, Any] | None = None,
    *,
    user_id: int | None = None,
) -> MetaHotPostsResponse:
    payload = payload or {}
    if "mark_status" in payload or "status" in payload:
        mark_status = _normalize_mark_status(payload.get("mark_status", payload.get("status")))
    else:
        mark_status = MARK_STATUS_BAD if _bool_payload(payload.get("marked")) else None
    affected = store.set_hot_post_mark_status(post_id, mark_status=mark_status, user_id=user_id)
    if not affected:
        return MetaHotPostsResponse({"error": "not_found"}, 404)
    return MetaHotPostsResponse(
        {"ok": True, "id": int(post_id), "mark_status": mark_status, "is_marked": bool(mark_status)}
    )


def build_favorite_response(
    post_id: int,
    payload: Mapping[str, Any] | None = None,
    *,
    user_id: int | None = None,
) -> MetaHotPostsResponse:
    if not user_id:
        return MetaHotPostsResponse({"error": "missing_user"}, 400)
    payload = payload or {}
    favorited = _bool_payload(payload.get("favorited", True))
    store.set_hot_post_favorite(post_id, user_id=int(user_id), favorited=favorited)
    return MetaHotPostsResponse(
        {"ok": True, "id": int(post_id), "is_favorited": bool(favorited)}
    )


def build_product_title_translate_zh_response(
    post_id: int,
    *,
    user_id: int | None = None,
) -> MetaHotPostsResponse:
    row = _get_ai_analysis_row(post_id)
    if not row:
        return MetaHotPostsResponse({"error": "not_found"}, 404)
    item = _hydrate_ai_analysis_row(row)
    product_title = str(item.get("product_title") or "").strip()
    if not product_title:
        return MetaHotPostsResponse({"error": "missing_product_title"}, 400)
    if str(item.get("product_title_zh") or "").strip():
        return MetaHotPostsResponse({"ok": True, "cached": True, "item": item})
    analysis_id = _int_payload(row.get("product_analysis_id"))
    if not analysis_id:
        return MetaHotPostsResponse({"error": "missing_product_analysis"}, 400)

    store.mark_product_title_translation_running(analysis_id)
    try:
        translated_title = product_title_translation.translate_product_title(
            product_title,
            user_id=user_id,
        )
    except Exception as exc:
        message = str(exc)[:1000]
        store.finish_product_title_translation(
            analysis_id,
            translated_title=None,
            error_message=message,
        )
        return MetaHotPostsResponse(
            {"ok": False, "error": message, "item": item},
            500,
        )
    store.finish_product_title_translation(
        analysis_id,
        translated_title=translated_title,
        error_message=None,
    )
    refreshed = _get_ai_analysis_row(post_id) or row
    return MetaHotPostsResponse(
        {"ok": True, "cached": False, "item": _hydrate_ai_analysis_row(refreshed)}
    )


AI_ANALYSIS_MODE_US_COPYABILITY = "us_copyability"
AI_ANALYSIS_MODE_EUROPE_TRANSLATION = "europe_translation"


def _normalize_ai_analysis_mode(mode: str) -> str | None:
    normalized = str(mode or "").strip().lower().replace("-", "_")
    if normalized in {AI_ANALYSIS_MODE_US_COPYABILITY, "us", "video_copyability"}:
        return AI_ANALYSIS_MODE_US_COPYABILITY
    if normalized in {AI_ANALYSIS_MODE_EUROPE_TRANSLATION, "europe_fit", "europe"}:
        return AI_ANALYSIS_MODE_EUROPE_TRANSLATION
    return None


def _ai_analysis_mode_meta(mode: str) -> dict[str, Any] | None:
    normalized = _normalize_ai_analysis_mode(mode)
    if normalized == AI_ANALYSIS_MODE_US_COPYABILITY:
        return {
            "mode": normalized,
            "label": "美国AI分析",
            "use_case": video_copyability.VIDEO_COPYABILITY_USE_CASE,
            "provider": video_copyability.VIDEO_COPYABILITY_PROVIDER,
            "model": video_copyability.VIDEO_COPYABILITY_MODEL,
            "system": video_copyability.build_system_prompt(),
            "schema": video_copyability.build_response_schema(),
            "temperature": 0.2,
            "max_output_tokens": 1400,
        }
    if normalized == AI_ANALYSIS_MODE_EUROPE_TRANSLATION:
        return {
            "mode": normalized,
            "label": "欧洲AI分析",
            "use_case": europe_fit.USE_CASE_CODE,
            "provider": europe_fit.EUROPE_FIT_PROVIDER,
            "model": europe_fit.EUROPE_FIT_MODEL,
            "system": europe_fit.build_system_prompt(),
            "schema": europe_fit.build_response_schema(),
            "temperature": 0.1,
            "max_output_tokens": 2048,
        }
    return None


def _get_ai_analysis_row(post_id: int) -> dict[str, Any] | None:
    return store.get_hot_post_ai_analysis_row(int(post_id))


def _hydrate_ai_analysis_row(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item.setdefault("sku_prices_json", "[]")
    return _hydrate_item(item)


def _has_ai_analysis_result(row: Mapping[str, Any], mode: str) -> bool:
    normalized = _normalize_ai_analysis_mode(mode)
    if normalized == AI_ANALYSIS_MODE_US_COPYABILITY:
        return str(row.get("video_copyability_status") or "").lower() == "done" and any(
            row.get(key) not in (None, "")
            for key in (
                "video_copyability_overall_score",
                "video_copyability_summary",
                "video_copyability_analysis_json",
            )
        )
    if normalized == AI_ANALYSIS_MODE_EUROPE_TRANSLATION:
        return str(row.get("europe_fit_status") or "").lower() == "done" and any(
            row.get(key) not in (None, "")
            for key in (
                "europe_fit_score",
                "europe_fit_reasoning",
                "europe_fit_llm_response_json",
            )
        )
    return False


def _ai_analysis_prompt(mode: str, row: Mapping[str, Any]) -> str:
    normalized = _normalize_ai_analysis_mode(mode)
    analysis_row = dict(row)
    analysis_row["hot_post_id"] = analysis_row.get("hot_post_id") or analysis_row.get("id")
    if normalized == AI_ANALYSIS_MODE_US_COPYABILITY:
        return video_copyability.build_prompt(analysis_row)
    if normalized == AI_ANALYSIS_MODE_EUROPE_TRANSLATION:
        return europe_fit.build_prompt(analysis_row)
    return ""


def _build_ai_analysis_request_payload(
    post_id: int,
    mode: str,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    meta = _ai_analysis_mode_meta(mode)
    if not meta:
        return {}
    item = _hydrate_ai_analysis_row(row)
    video_url = item.get("local_video_url") or item.get("tos_video_url") or item.get("video_url") or ""
    media = [
        {
            "role": "product_main_image",
            "type": "image",
            "url": item.get("product_main_image_url") or item.get("image_url") or "",
            "sent_to_llm": False,
        },
        {
            "role": "video",
            "type": "video",
            "url": video_url,
            "cover_url": item.get("local_video_cover_url") or item.get("tos_video_cover_url") or item.get("image_url") or "",
            "source_path": item.get("local_video_path") or "",
            "sent_to_llm": True,
        },
    ]
    request_payload = {
        "use_case": meta["use_case"],
        "prompt": _ai_analysis_prompt(meta["mode"], row),
        "system": meta["system"],
        "media": [
            {
                "role": "video",
                "type": "video",
                "path": item.get("local_video_path") or "",
                "runtime_note": "The analyzer prepares/compresses this local video before sending it to the LLM.",
            }
        ],
        "response_schema": meta["schema"],
        "provider_override": meta["provider"],
        "model_override": meta["model"],
        "temperature": meta["temperature"],
        "max_output_tokens": meta["max_output_tokens"],
        "project_id": f"meta-hot-post-{post_id}",
    }
    return {
        "mode": meta["mode"],
        "label": meta["label"],
        "use_case": meta["use_case"],
        "provider": meta["provider"],
        "model": meta["model"],
        "product": {
            "title": item.get("product_title") or "",
            "url": item.get("product_url") or "",
            "main_image_url": item.get("product_main_image_url") or item.get("image_url") or "",
            "category": item.get("category_l1") or "",
            "category_label": item.get("category_l1_zh") or item.get("category_l1") or "",
            "price_min": item.get("price_min"),
            "price_max": item.get("price_max"),
            "currency": item.get("currency") or "",
        },
        "post": {
            "id": item.get("id"),
            "post_url": item.get("post_url") or "",
            "ad_library_url": item.get("ad_library_url") or "",
            "message_html": item.get("message_html") or "",
            "message_source_html": item.get("message_source_html") or "",
            "latest_likes": item.get("latest_likes"),
            "latest_comments": item.get("latest_comments"),
            "latest_shares": item.get("latest_shares"),
            "sync_period_likes": item.get("sync_period_likes"),
            "sync_period_hours": item.get("sync_period_hours"),
        },
        "media": media,
        "prompts": {
            "system": meta["system"],
            "user": request_payload["prompt"],
        },
        "response_schema": meta["schema"],
        "request": request_payload,
        "has_result": _has_ai_analysis_result(row, meta["mode"]),
        "full_payload_url": f"/xuanpin/api/meta-hot-posts/{int(post_id)}/ai-analysis/{meta['mode']}/request-payload",
    }


def build_ai_analysis_request_preview_response(post_id: int, mode: str) -> MetaHotPostsResponse:
    meta = _ai_analysis_mode_meta(mode)
    if not meta:
        return MetaHotPostsResponse({"error": "invalid_mode"}, 400)
    row = _get_ai_analysis_row(post_id)
    if not row:
        return MetaHotPostsResponse({"error": "not_found"}, 404)
    return MetaHotPostsResponse(
        {"ok": True, "payload": _build_ai_analysis_request_payload(post_id, meta["mode"], row)}
    )


def build_ai_analysis_request_payload_response(post_id: int, mode: str) -> MetaHotPostsResponse:
    return build_ai_analysis_request_preview_response(post_id, mode)


def _build_ai_analysis_result_payload(
    post_id: int,
    mode: str,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    meta = _ai_analysis_mode_meta(mode)
    item = _hydrate_ai_analysis_row(row)
    if not meta or not _has_ai_analysis_result(row, meta["mode"]):
        return {
            "ok": True,
            "mode": meta["mode"] if meta else mode,
            "has_result": False,
            "item": item,
        }
    if meta["mode"] == AI_ANALYSIS_MODE_US_COPYABILITY:
        result = item.get("video_copyability") or {}
        raw_response = result.get("raw") or {}
    else:
        result = {
            "suitability_score": item.get("europe_fit_score"),
            "recommendation": item.get("europe_fit_recommendation"),
            "direct_reuse": item.get("europe_fit_direct_reuse"),
            "best_countries": item.get("europe_fit_best_countries") or [],
            "country_scores": item.get("europe_fit_country_scores") or {},
            "strengths": item.get("europe_fit_strengths") or [],
            "strengths_zh": item.get("europe_fit_strengths_zh") or [],
            "risks": item.get("europe_fit_risks") or [],
            "risks_zh": item.get("europe_fit_risks_zh") or [],
            "required_changes": item.get("europe_fit_required_changes") or [],
            "required_changes_zh": item.get("europe_fit_required_changes_zh") or [],
            "reasoning": item.get("europe_fit_reasoning") or "",
            "reasoning_zh": item.get("europe_fit_reasoning_zh") or "",
            "provider": item.get("europe_fit_provider") or "",
            "model": item.get("europe_fit_model") or "",
            "assessed_at": item.get("europe_fit_assessed_at") or "",
            "video_optimization": item.get("europe_fit_video_optimization") or {},
        }
        raw_response = item.get("europe_fit_raw_response") or {}
        if isinstance(raw_response.get("json"), Mapping):
            result.update(
                {
                    key: raw_response["json"].get(key)
                    for key in (
                        "translation_fit_score",
                        "best_language_markets",
                        "source_language_detected",
                        "speech_dependency",
                        "on_screen_text_dependency",
                        "needs_subtitle_translation",
                        "needs_voiceover_or_dubbing",
                        "needs_screen_text_replacement",
                        "localization_difficulty",
                        "country_localization_notes",
                    )
                    if key in raw_response["json"]
                }
            )
    return {
        "ok": True,
        "mode": meta["mode"],
        "label": meta["label"],
        "has_result": True,
        "result": result,
        "raw_response": raw_response,
        "item": item,
    }


def build_ai_analysis_result_response(post_id: int, mode: str) -> MetaHotPostsResponse:
    meta = _ai_analysis_mode_meta(mode)
    if not meta:
        return MetaHotPostsResponse({"error": "invalid_mode"}, 400)
    row = _get_ai_analysis_row(post_id)
    if not row:
        return MetaHotPostsResponse({"error": "not_found"}, 404)
    return MetaHotPostsResponse(_build_ai_analysis_result_payload(post_id, meta["mode"], row))


def _has_us_copyability_zh_cache(item: Mapping[str, Any]) -> bool:
    data = item.get("video_copyability") if isinstance(item, Mapping) else None
    if not isinstance(data, Mapping):
        return False
    return bool(str(data.get("summary_zh") or "").strip())


def _has_europe_fit_zh_cache(item: Mapping[str, Any]) -> bool:
    if not isinstance(item, Mapping):
        return False

    def cached_for(source_key: str, translated_key: str) -> bool:
        source = item.get(source_key)
        translated = item.get(translated_key)
        has_source = bool(source) if not isinstance(source, str) else bool(source.strip())
        has_translation = bool(translated) if not isinstance(translated, str) else bool(translated.strip())
        return (not has_source) or has_translation

    has_any_translation = any(
        bool(item.get(key))
        for key in (
            "europe_fit_strengths_zh",
            "europe_fit_risks_zh",
            "europe_fit_required_changes_zh",
            "europe_fit_reasoning_zh",
        )
    )
    return has_any_translation and all(
        (
            cached_for("europe_fit_strengths", "europe_fit_strengths_zh"),
            cached_for("europe_fit_risks", "europe_fit_risks_zh"),
            cached_for("europe_fit_required_changes", "europe_fit_required_changes_zh"),
            cached_for("europe_fit_reasoning", "europe_fit_reasoning_zh"),
        )
    )


def _video_copyability_translation_row(item: Mapping[str, Any]) -> dict[str, Any]:
    data = item.get("video_copyability") if isinstance(item, Mapping) else None
    payload = dict(data or {}) if isinstance(data, Mapping) else {}
    return {
        "analysis_id": payload.get("analysis_id"),
        "recommendation": payload.get("recommendation"),
        "summary": payload.get("summary"),
        "analysis_json": payload.get("raw") or {},
    }


def _europe_fit_translation_row(post_id: int, row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "post_id": int(post_id),
        "recommendation": row.get("europe_fit_recommendation"),
        "best_countries_json": row.get("europe_fit_best_countries_json"),
        "strengths_json": row.get("europe_fit_strengths_json"),
        "risks_json": row.get("europe_fit_risks_json"),
        "required_changes_json": row.get("europe_fit_required_changes_json"),
        "reasoning": row.get("europe_fit_reasoning"),
    }


def _refreshed_ai_analysis_result(
    post_id: int,
    mode: str,
    *,
    cached: bool,
    fallback_row: Mapping[str, Any],
) -> MetaHotPostsResponse:
    refreshed = _get_ai_analysis_row(post_id) or dict(fallback_row)
    payload = _build_ai_analysis_result_payload(post_id, mode, refreshed)
    payload["cached"] = bool(cached)
    return MetaHotPostsResponse(payload)


def _build_ai_translation_error_response(
    mode: str,
    exc: Exception,
    *,
    finish_error_fn,
) -> MetaHotPostsResponse:
    message = str(exc)[:1000]
    finish_error_fn(message)
    rate_limited = video_copyability_translation.is_rate_limited_error(exc)
    return MetaHotPostsResponse(
        {
            "ok": False,
            "mode": mode,
            "error": message,
            "rate_limited": bool(rate_limited),
        },
        429 if rate_limited else 500,
    )


def build_ai_analysis_translate_zh_response(
    post_id: int,
    mode: str,
    *,
    user_id: int | None = None,
) -> MetaHotPostsResponse:
    meta = _ai_analysis_mode_meta(mode)
    if not meta:
        return MetaHotPostsResponse({"error": "invalid_mode"}, 400)
    row = _get_ai_analysis_row(post_id)
    if not row:
        return MetaHotPostsResponse({"error": "not_found"}, 404)
    if not _has_ai_analysis_result(row, meta["mode"]):
        return MetaHotPostsResponse({"error": "missing_result", "mode": meta["mode"]}, 400)

    item = _hydrate_ai_analysis_row(row)
    if meta["mode"] == AI_ANALYSIS_MODE_US_COPYABILITY:
        if _has_us_copyability_zh_cache(item):
            result = _build_ai_analysis_result_payload(post_id, meta["mode"], row)
            result["cached"] = True
            return MetaHotPostsResponse(result)
        translation_row = _video_copyability_translation_row(item)
        analysis_id = translation_row.get("analysis_id")
        if not analysis_id:
            return MetaHotPostsResponse({"error": "missing_analysis_id", "mode": meta["mode"]}, 400)
        store.mark_video_copyability_summary_translation_running(int(analysis_id))
        try:
            translated_summary = video_copyability_translation.translate_summary(
                translation_row,
                user_id=user_id,
                provider_override=MANUAL_AI_TRANSLATE_PROVIDER,
                model_override=MANUAL_AI_TRANSLATE_MODEL,
                billing_source=MANUAL_US_AI_TRANSLATE_SOURCE,
            )
        except Exception as exc:
            return _build_ai_translation_error_response(
                meta["mode"],
                exc,
                finish_error_fn=lambda error: store.finish_video_copyability_summary_translation(
                    int(analysis_id),
                    translated_summary=None,
                    error_message=error,
                ),
            )
        store.finish_video_copyability_summary_translation(
            int(analysis_id),
            translated_summary=translated_summary,
            error_message=None,
        )
        return _refreshed_ai_analysis_result(post_id, meta["mode"], cached=False, fallback_row=row)

    if _has_europe_fit_zh_cache(item):
        result = _build_ai_analysis_result_payload(post_id, meta["mode"], row)
        result["cached"] = True
        return MetaHotPostsResponse(result)
    translation_row = _europe_fit_translation_row(post_id, row)
    store.mark_europe_fit_translation_running(int(post_id))
    try:
        translated = europe_fit_translation.translate_assessment(
            translation_row,
            user_id=user_id,
            provider_override=MANUAL_AI_TRANSLATE_PROVIDER,
            model_override=MANUAL_AI_TRANSLATE_MODEL,
            billing_source=MANUAL_EUROPE_AI_TRANSLATE_SOURCE,
        )
    except Exception as exc:
        return _build_ai_translation_error_response(
            meta["mode"],
            exc,
            finish_error_fn=lambda error: store.finish_europe_fit_translation(
                int(post_id),
                translated=None,
                error_message=error,
            ),
        )
    store.finish_europe_fit_translation(
        int(post_id),
        translated=translated,
        error_message=None,
    )
    return _refreshed_ai_analysis_result(post_id, meta["mode"], cached=False, fallback_row=row)


def _analysis_attempts_after_mark(state: Mapping[str, Any] | None) -> int:
    try:
        return int((state or {}).get("attempts") or 0) + 1
    except (TypeError, ValueError):
        return 1


def _status_after_failed_manual_attempt(state: Mapping[str, Any] | None) -> str:
    return "suspended" if _analysis_attempts_after_mark(state) >= 3 else "failed"


def _analysis_row_for_analyzer(row: Mapping[str, Any], *, post_id: int) -> dict[str, Any]:
    analysis_row = dict(row)
    analysis_row.setdefault("id", int(post_id))
    analysis_row["hot_post_id"] = int(post_id)
    return analysis_row


def _is_rate_limited_exception(exc: Exception) -> bool:
    from appcore.meta_hot_posts import scheduler

    return scheduler._is_rate_limited_error(exc)


def _run_single_us_copyability(post_id: int, row: Mapping[str, Any], *, user_id: int | None) -> None:
    from appcore.meta_hot_posts import scheduler

    before_state = store.get_video_copyability_analysis_state(post_id)
    store.ensure_video_copyability_candidate_for_post(post_id)
    state = store.get_video_copyability_analysis_state(post_id)
    if not state:
        raise ValueError("video copyability candidate is not available")
    analysis_id = int(state["id"])
    store.mark_video_copyability_running(analysis_id)
    try:
        result = video_copyability.analyze_video_copyability(
            _analysis_row_for_analyzer(row, post_id=post_id),
            user_id=scheduler.resolve_billing_user_id(user_id),
        )
    except Exception as exc:
        if _is_rate_limited_exception(exc):
            if before_state:
                store.restore_video_copyability_analysis_state(
                    analysis_id,
                    status=str(before_state.get("status") or "pending"),
                    attempts=int(before_state.get("attempts") or 0),
                    last_error=before_state.get("last_error"),
                )
            else:
                store.delete_video_copyability_analysis_for_post(post_id)
            raise
        store.finish_video_copyability_analysis(
            analysis_id,
            result={},
            error_message=str(exc)[:1000],
            status_override=_status_after_failed_manual_attempt(state),
        )
        raise
    store.finish_video_copyability_analysis(
        analysis_id,
        result=result,
        error_message=None,
    )


def _run_single_europe_translation(post_id: int, row: Mapping[str, Any], *, user_id: int | None) -> None:
    from appcore.meta_hot_posts import scheduler

    before_state = store.get_europe_fit_assessment_state(post_id)
    store.ensure_europe_fit_candidate_for_post(post_id)
    state = store.get_europe_fit_assessment_state(post_id)
    if not state:
        raise ValueError("Europe translation candidate is not available")
    store.mark_europe_fit_running(post_id)
    try:
        result = europe_fit.assess_material(
            _analysis_row_for_analyzer(row, post_id=post_id),
            user_id=scheduler.resolve_billing_user_id(user_id),
        )
    except Exception as exc:
        if _is_rate_limited_exception(exc):
            if before_state:
                store.restore_europe_fit_assessment_state(
                    post_id,
                    status=str(before_state.get("status") or "pending"),
                    attempts=int(before_state.get("attempts") or 0),
                    last_error=before_state.get("last_error"),
                )
            else:
                store.delete_europe_fit_assessment_for_post(post_id)
            raise
        store.finish_europe_fit_assessment(
            post_id,
            status=_status_after_failed_manual_attempt(state),
            result={},
            video_optimization={},
            error_message=str(exc)[:1000],
        )
        raise
    store.finish_europe_fit_assessment(
        post_id,
        status="done",
        result=result,
        video_optimization=result.get("video_optimization") or {},
        error_message=None,
    )


def build_ai_analysis_run_response(
    post_id: int,
    mode: str,
    payload: Mapping[str, Any] | None = None,
    *,
    user_id: int | None = None,
) -> MetaHotPostsResponse:
    meta = _ai_analysis_mode_meta(mode)
    if not meta:
        return MetaHotPostsResponse({"error": "invalid_mode"}, 400)
    row = _get_ai_analysis_row(post_id)
    if not row:
        return MetaHotPostsResponse({"error": "not_found"}, 404)
    force = _bool_payload((payload or {}).get("force"))
    if not force and _has_ai_analysis_result(row, meta["mode"]):
        result = _build_ai_analysis_result_payload(post_id, meta["mode"], row)
        result["cached"] = True
        return MetaHotPostsResponse(result)
    try:
        if meta["mode"] == AI_ANALYSIS_MODE_US_COPYABILITY:
            _run_single_us_copyability(post_id, row, user_id=user_id)
        else:
            _run_single_europe_translation(post_id, row, user_id=user_id)
    except Exception as exc:
        if _is_rate_limited_exception(exc):
            return MetaHotPostsResponse(
                {
                    "ok": False,
                    "mode": meta["mode"],
                    "error": str(exc)[:1000],
                    "rate_limited": True,
                },
                429,
            )
        return MetaHotPostsResponse(
            {"ok": False, "mode": meta["mode"], "error": str(exc)[:1000]},
            500,
        )
    refreshed = _get_ai_analysis_row(post_id)
    if not refreshed:
        return MetaHotPostsResponse({"error": "not_found"}, 404)
    result = _build_ai_analysis_result_payload(post_id, meta["mode"], refreshed)
    result["cached"] = False
    return MetaHotPostsResponse(result)


def build_refresh_response() -> MetaHotPostsResponse:
    from appcore.meta_hot_posts import scheduler

    return MetaHotPostsResponse({"ok": True, "result": scheduler.sync_tick_once()}, 202)


def build_analyze_response(payload: Mapping[str, Any] | None = None) -> MetaHotPostsResponse:
    from appcore.meta_hot_posts import scheduler

    payload = payload or {}
    try:
        limit = int(payload.get("limit") or scheduler.SCHEDULED_ANALYSIS_LIMIT)
    except (TypeError, ValueError):
        limit = scheduler.SCHEDULED_ANALYSIS_LIMIT
    try:
        delay = float(
            payload.get("per_item_delay_seconds")
            if payload.get("per_item_delay_seconds") is not None
            else scheduler.SCHEDULED_ANALYSIS_DELAY_SECONDS
        )
    except (TypeError, ValueError):
        delay = scheduler.SCHEDULED_ANALYSIS_DELAY_SECONDS
    try:
        user_id = int(payload.get("user_id") or 0) or None
    except (TypeError, ValueError):
        user_id = None
    recategorize_only = bool(payload.get("recategorize_only") or payload.get("recategorize"))
    include_all_categories = bool(payload.get("include_all_categories") or payload.get("include_all"))
    return MetaHotPostsResponse(
        {
            "ok": True,
            "result": scheduler.analysis_tick_once(
                limit=limit,
                user_id=user_id,
                recategorize_only=recategorize_only,
                include_all_categories=include_all_categories,
                per_item_delay_seconds=delay,
            ),
        },
        202,
    )


def build_translate_response(payload: Mapping[str, Any] | None = None) -> MetaHotPostsResponse:
    from appcore.meta_hot_posts import scheduler

    payload = payload or {}
    try:
        limit = int(payload.get("limit") or scheduler.SCHEDULED_TRANSLATION_LIMIT)
    except (TypeError, ValueError):
        limit = scheduler.SCHEDULED_TRANSLATION_LIMIT
    try:
        delay = float(
            payload.get("per_item_delay_seconds")
            if payload.get("per_item_delay_seconds") is not None
            else scheduler.SCHEDULED_TRANSLATION_DELAY_SECONDS
        )
    except (TypeError, ValueError):
        delay = scheduler.SCHEDULED_TRANSLATION_DELAY_SECONDS
    try:
        user_id = int(payload.get("user_id") or 0) or None
    except (TypeError, ValueError):
        user_id = None
    return MetaHotPostsResponse(
        {
            "ok": True,
            "result": scheduler.translation_tick_once(
                limit=limit,
                user_id=user_id,
                per_item_delay_seconds=delay,
            ),
        },
        202,
    )


def build_localize_videos_response(payload: Mapping[str, Any] | None = None) -> MetaHotPostsResponse:
    from appcore.meta_hot_posts import scheduler

    payload = payload or {}
    try:
        limit = int(payload.get("limit") or scheduler.SCHEDULED_VIDEO_LOCALIZATION_LIMIT)
    except (TypeError, ValueError):
        limit = scheduler.SCHEDULED_VIDEO_LOCALIZATION_LIMIT
    try:
        delay = float(
            payload.get("min_delay_seconds")
            if payload.get("min_delay_seconds") is not None
            else payload.get("per_item_delay_seconds")
            if payload.get("per_item_delay_seconds") is not None
            else scheduler.SCHEDULED_VIDEO_LOCALIZATION_DELAY_SECONDS
        )
    except (TypeError, ValueError):
        delay = scheduler.SCHEDULED_VIDEO_LOCALIZATION_DELAY_SECONDS
    return MetaHotPostsResponse(
        {
            "ok": True,
            "result": scheduler.video_localization_tick_once(
                limit=limit,
                min_delay_seconds=delay,
            ),
        },
        202,
    )

def build_europe_fit_response(payload: Mapping[str, Any] | None = None) -> MetaHotPostsResponse:
    from appcore.meta_hot_posts import scheduler

    payload = payload or {}
    try:
        limit = int(payload.get("limit") or scheduler.SCHEDULED_VIDEO_ANALYSIS_QUEUE_LIMIT)
    except (TypeError, ValueError):
        limit = scheduler.SCHEDULED_VIDEO_ANALYSIS_QUEUE_LIMIT
    try:
        user_id = int(payload.get("user_id") or 0) or None
    except (TypeError, ValueError):
        user_id = None
    return MetaHotPostsResponse(
        {
            "ok": True,
            "result": scheduler.video_analysis_queue_tick_once(
                limit=max(1, min(scheduler.SCHEDULED_VIDEO_ANALYSIS_QUEUE_LIMIT, limit)),
                user_id=user_id,
                respect_rate_limit_circuit=False,
            ),
        },
        202,
    )


def build_video_copyability_response(payload: Mapping[str, Any] | None = None) -> MetaHotPostsResponse:
    from appcore.meta_hot_posts import scheduler

    payload = payload or {}
    try:
        limit = int(payload.get("limit") or scheduler.SCHEDULED_VIDEO_ANALYSIS_QUEUE_LIMIT)
    except (TypeError, ValueError):
        limit = scheduler.SCHEDULED_VIDEO_ANALYSIS_QUEUE_LIMIT
    try:
        user_id = int(payload.get("user_id") or 0) or None
    except (TypeError, ValueError):
        user_id = None
    return MetaHotPostsResponse(
        {
            "ok": True,
            "result": scheduler.video_analysis_queue_tick_once(
                limit=max(1, min(scheduler.SCHEDULED_VIDEO_ANALYSIS_QUEUE_LIMIT, limit)),
                user_id=user_id,
                respect_rate_limit_circuit=False,
            ),
        },
        202,
    )


def build_video_copyability_top50_response(
    args: Mapping[str, Any],
    *,
    user_id: int | None = None,
) -> MetaHotPostsResponse:
    try:
        limit = int(args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    rows = (
        store.list_top_video_copyability_analyses(limit=limit, user_id=user_id)
        if user_id
        else store.list_top_video_copyability_analyses(limit=limit)
    )
    items = [
        _hydrate_video_copyability_item(row)
        for row in rows
    ]
    return MetaHotPostsResponse({"items": items, "total": len(items), "limit": max(1, min(50, limit))})


def resolve_local_video_response(post_id: int) -> LocalVideoResponse:
    row = store.get_hot_post_local_video(post_id)
    if not row:
        return LocalVideoResponse(None, 404, "not_found")
    if row.get("local_video_status") != "downloaded" or not row.get("local_video_path"):
        return LocalVideoResponse(None, 404, "not_downloaded")
    path = video_localization.resolve_local_video_path(str(row.get("local_video_path") or ""))
    if path is None:
        return LocalVideoResponse(None, 404, "not_found")
    return LocalVideoResponse(path, 200, None)


def resolve_local_video_cover_response(post_id: int) -> LocalVideoResponse:
    row = store.get_hot_post_local_video(post_id)
    if not row:
        return LocalVideoResponse(None, 404, "not_found")
    if row.get("local_video_status") != "downloaded" or not row.get("local_video_cover_path"):
        return LocalVideoResponse(None, 404, "not_found")
    path = video_localization.resolve_output_relative_file_path(
        str(row.get("local_video_cover_path") or "")
    )
    if path is None:
        return LocalVideoResponse(None, 404, "not_found")
    return LocalVideoResponse(path, 200, None)


def import_hot_post(post_id: int, translator_id: int, actor_user_id: int) -> dict[str, Any]:
    from appcore.db import execute, query_one
    from appcore import object_keys, local_media_storage
    from appcore.medias import create_item
    from appcore.meta_hot_posts import video_localization
    import os

    # 1. Fetch hot post and check existing import
    row = query_one(
        "SELECT p.*, a.product_title, a.product_main_image_url, a.category_l1 "
        "FROM meta_hot_posts p "
        "LEFT JOIN meta_hot_post_product_analyses a ON a.product_url_hash = p.product_url_hash "
        "WHERE p.id = %s LIMIT 1",
        (int(post_id),)
    )
    if not row:
        raise ValueError(f"Meta hot post not found: {post_id}")

    local_product_id = row.get("local_product_id")
    local_media_item_id = row.get("local_media_item_id")
    if local_product_id and local_media_item_id:
        return {
            "media_product_id": int(local_product_id),
            "media_item_id": int(local_media_item_id),
            "is_new_product": False,
        }

    # 2. Check localized video file
    if row.get("local_video_status") != "downloaded" or not row.get("local_video_path"):
        raise ValueError("本地视频尚未就绪，请等待视频本地化完成")

    local_path = video_localization.resolve_local_video_path(str(row.get("local_video_path") or ""))
    if local_path is None or not local_path.exists():
        raise ValueError("本地视频文件不存在")

    # Resolve cover
    local_cover_path = None
    if row.get("local_video_cover_path"):
        cover_res = video_localization.resolve_output_relative_file_path(str(row.get("local_video_cover_path") or ""))
        if cover_res and cover_res.exists():
            local_cover_path = cover_res

    # 3. Create or reuse media product
    product_code = f"meta-hot-{post_id}"
    existing_product = query_one(
        "SELECT id, user_id FROM media_products WHERE product_code = %s AND deleted_at IS NULL LIMIT 1",
        (product_code,)
    )

    is_new = existing_product is None
    if is_new:
        product_title = row.get("product_title") or f"Meta热帖产品 {post_id}"
        product_link = row.get("product_url") or ""
        main_image = row.get("product_main_image_url") or row.get("image_url") or ""
        product_id = execute(
            "INSERT INTO media_products (user_id, name, product_code, product_link, main_image) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                int(translator_id),
                str(product_title)[:255],
                product_code,
                str(product_link)[:2047],
                str(main_image)[:2047],
            )
        )
        owner_uid = int(translator_id)
    else:
        product_id = int(existing_product["id"])
        owner_uid = int(existing_product["user_id"])

    # 4. Copy video to local media store
    filename = f"meta_hot_{post_id}.mp4"
    dest_key = object_keys.build_media_object_key(owner_uid, product_id, filename)
    with open(local_path, "rb") as handle:
        local_media_storage.write_stream(dest_key, handle)
    file_size = local_path.stat().st_size

    # Copy cover to local media store if exists
    cover_key = None
    if local_cover_path:
        cover_filename = f"meta_hot_{post_id}_cover.jpg"
        cover_key = object_keys.build_media_object_key(owner_uid, product_id, cover_filename)
        with open(local_cover_path, "rb") as handle:
            local_media_storage.write_stream(cover_key, handle)

    # 5. Insert media item via create_item
    duration_val = row.get("local_video_duration_seconds") or 0
    try:
        duration_seconds = float(duration_val)
    except (TypeError, ValueError):
        duration_seconds = 0.0

    item_id = create_item(
        product_id=product_id,
        user_id=owner_uid,
        filename=filename,
        object_key=dest_key,
        display_name=row.get("product_title") or f"Meta热帖素材 {post_id}",
        duration_seconds=duration_seconds if duration_seconds > 0 else None,
        file_size=file_size,
        cover_object_key=cover_key,
        lang="en",
    )

    # 6. Update mapping back to meta_hot_posts
    execute(
        "UPDATE meta_hot_posts SET local_product_id = %s, local_media_item_id = %s WHERE id = %s",
        (product_id, item_id, int(post_id))
    )

    return {
        "media_product_id": product_id,
        "media_item_id": item_id,
        "is_new_product": is_new,
    }

