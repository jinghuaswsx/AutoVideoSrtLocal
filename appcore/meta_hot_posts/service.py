from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from appcore.meta_hot_posts import categories, product_analysis, store, video_localization

MARK_STATUS_OK = "ok"
MARK_STATUS_BAD = "bad"


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
            "analysis_id",
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
    item["europe_fit_best_countries"] = _decode_json_list(
        item.pop("europe_fit_best_countries_json", None)
    )
    item["europe_fit_country_scores"] = _decode_json_dict(
        item.pop("europe_fit_country_scores_json", None)
    )
    item["europe_fit_strengths"] = _decode_json_list(
        item.pop("europe_fit_strengths_json", None)
    )
    item["europe_fit_risks"] = _decode_json_list(
        item.pop("europe_fit_risks_json", None)
    )
    item["europe_fit_required_changes"] = _decode_json_list(
        item.pop("europe_fit_required_changes_json", None)
    )
    item["europe_fit_video_optimization"] = _decode_json_dict(
        item.pop("europe_fit_video_optimization_json", None)
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


def build_list_response(args: Mapping[str, Any]) -> MetaHotPostsResponse:
    payload = store.list_hot_posts(args)
    payload["items"] = [_hydrate_item(item) for item in payload.get("items") or []]
    return MetaHotPostsResponse(payload)


def build_today_new_response(args: Mapping[str, Any]) -> MetaHotPostsResponse:
    payload = store.list_today_new_hot_posts(args)
    payload["items"] = [_hydrate_item(item) for item in payload.get("items") or []]
    return MetaHotPostsResponse(payload)


def build_europe_top_response(args: Mapping[str, Any]) -> MetaHotPostsResponse:
    try:
        limit = int(args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    items = store.list_top_europe_fit_materials(limit=limit)
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


def build_video_copyability_top50_response(args: Mapping[str, Any]) -> MetaHotPostsResponse:
    try:
        limit = int(args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    items = [
        _hydrate_video_copyability_item(row)
        for row in store.list_top_video_copyability_analyses(limit=limit)
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
