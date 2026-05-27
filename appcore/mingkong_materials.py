"""Daily Mingkong material snapshots and yesterday-spend ranking."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlparse

import requests

from appcore import local_media_storage, mingkong_login_autofill, pushes, scheduled_tasks
from appcore.db import execute, get_conn, query, query_one
from web.services.media_mk_selection import normalize_mk_media_path


_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.I)
_COVER_CACHE_PREFIX = "artifacts/mingkong-material-covers"
_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_SECONDS_PER_DAY = 24 * 60 * 60
# Legacy table/helper names still say top100; the business archive now keeps Top300.
YESTERDAY_SPEND_TOP_LIMIT = 300
_AD_STATUS_SCOPE_PRODUCT = "product"
_AD_STATUS_SCOPE_MATERIAL = "material"
logger = logging.getLogger(__name__)


def guard_against_windows_local_mysql() -> None:
    if os.name != "nt":
        return
    from config import DB_HOST, DB_PORT

    host = str(DB_HOST or "").strip().lower()
    if host in {"127.0.0.1", "localhost", "::1"} and int(DB_PORT) == 3306:
        raise RuntimeError(
            "项目规则禁止在 Windows 本机连接 127.0.0.1:3306 MySQL；"
            "明空素材每日快照请在服务器或测试服务器环境运行。"
        )


def _coerce_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")[:10]


def _coerce_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return f"{value.isoformat()} 00:00:00"
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return ""
    if len(text) == 10:
        return f"{text} 00:00:00"
    return text[:19]


def _today() -> date:
    return date.today()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = _coerce_datetime(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _snapshot_slot_for(snapshot_at: Any) -> str:
    value = _parse_datetime(snapshot_at)
    if value is None:
        return ""
    return "0500" if value.hour < 12 else "1700"


def _snapshot_identity(snapshot_date: str | None = None, snapshot_at: Any = None) -> tuple[str, str, str]:
    resolved_at = _coerce_datetime(snapshot_at) if snapshot_at else _coerce_datetime(datetime.now())
    resolved_date = _coerce_date(snapshot_date) if snapshot_date else resolved_at[:10]
    if snapshot_date and not snapshot_at:
        resolved_at = f"{resolved_date} {resolved_at[11:19]}"
    return resolved_date, resolved_at, _snapshot_slot_for(resolved_at)


def _trim(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _strip_rjc(value: Any) -> str:
    return _RJC_SUFFIX_RE.sub("", str(value or "").strip()).lower()


def _product_handle(value: Any) -> str:
    parsed = urlparse(str(value or ""))
    parts = [part for part in parsed.path.split("/") if part]
    if "products" not in parts:
        return ""
    index = parts.index("products")
    if index + 1 >= len(parts):
        return ""
    return _strip_rjc(parts[index + 1])


def _raw_product_handle(value: Any) -> str:
    parsed = urlparse(str(value or ""))
    parts = [part for part in parsed.path.split("/") if part]
    if "products" not in parts:
        return ""
    index = parts.index("products")
    if index + 1 >= len(parts):
        return ""
    return str(parts[index + 1] or "").strip().lower()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(",", "")
    if not text:
        return default
    multiplier = 1.0
    if "万" in text:
        multiplier = 10000.0
        text = text.replace("万", "")
    elif "千" in text:
        multiplier = 1000.0
        text = text.replace("千", "")
    elif text.lower().endswith("k"):
        multiplier = 1000.0
        text = text[:-1]
    text = (
        text.replace("CNY", "")
        .replace("USD", "")
        .replace("$", "")
        .replace("¥", "")
        .strip()
    )
    try:
        return float(text) * multiplier
    except (TypeError, ValueError):
        return default


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _metadata_for_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("mk_video_metadata")
    if isinstance(metadata, dict):
        return metadata
    loaded = _json_loads(row.get("mk_video_metadata_json"), {})
    return loaded if isinstance(loaded, dict) else {}


def _raw_spend_text(row: dict[str, Any], metadata: dict[str, Any] | None = None) -> str:
    source = metadata if metadata is not None else _metadata_for_row(row)
    for container in (row, source):
        for key in ("video_spends_text", "spends_text", "spends"):
            value = container.get(key) if isinstance(container, dict) else None
            text = str(value or "").strip()
            if text and text != "0":
                return text
    return ""


def _spend_from_row(row: dict[str, Any], key: str, metadata: dict[str, Any] | None = None) -> float:
    stored = _as_float(row.get(key))
    raw = _as_float(_raw_spend_text(row, metadata))
    if stored <= 0 and raw > 0:
        return raw
    return stored


def _normalized_product_code(value: Any) -> str:
    return str(value or "").strip().lower()


def _delta_from_previous_baseline(
    *,
    current_spend: float,
    previous_spend: float | None,
    product_code: Any,
    previous_product_codes: set[str] | None,
    has_previous_snapshot: bool,
) -> float:
    if previous_spend is not None:
        return max(0.0, current_spend - previous_spend)
    if not has_previous_snapshot or previous_product_codes is None:
        return current_spend
    if _normalized_product_code(product_code) in previous_product_codes:
        return current_spend
    return 0.0


def _metadata_for_write(row: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(row.get("mk_video_metadata") or {})
    spends_text = str(row.get("video_spends_text") or "").strip()
    if spends_text and not str(metadata.get("spends") or "").strip():
        metadata["spends"] = spends_text
    return metadata


def _iso_datetime(value: Any) -> Any:
    return value.isoformat(sep=" ") if hasattr(value, "isoformat") else value


def _local_media_url(object_key: Any) -> str:
    key = str(object_key or "").strip()
    if not key:
        return ""
    return f"/medias/object?object_key={quote(key, safe='')}"


def media_search_code_for(product_code: Any) -> str:
    base = _strip_rjc(product_code)
    return f"{base}-rjc" if base else ""


def status_lookup_hash(lookup_key: Any) -> str:
    return hashlib.sha256(str(lookup_key or "").strip().lower().encode("utf-8")).hexdigest()


def _material_status_lookup_key(video_path: Any) -> str:
    return normalize_mk_media_path(str(video_path or ""))


def _legacy_material_name_key(value: Any) -> str:
    raw = normalize_mk_media_path(str(value or ""))
    if not raw:
        return ""
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    return re.sub(r"\s+", " ", os.path.basename(raw).strip().lower())


def _legacy_material_match_keys(item: dict[str, Any]) -> set[str]:
    metadata = item.get("mk_video_metadata") if isinstance(item.get("mk_video_metadata"), dict) else {}
    values = [
        item.get("video_name"),
        item.get("video_path"),
        metadata.get("name"),
        metadata.get("filename"),
        metadata.get("video_path"),
        metadata.get("path"),
    ]
    return {key for key in (_legacy_material_name_key(value) for value in values) if key}


def _legacy_media_row_match_keys(row: dict[str, Any]) -> set[str]:
    return {
        key
        for key in (
            _legacy_material_name_key(row.get("filename")),
            _legacy_material_name_key(row.get("display_name")),
            _legacy_material_name_key(row.get("object_key")),
        )
        if key
    }


def _legacy_keys_match(card_key: str, media_key: str) -> bool:
    if not card_key or not media_key:
        return False
    return media_key == card_key or media_key.endswith(card_key)


def _legacy_material_rows_by_product(product_ids: set[int]) -> dict[int, list[dict[str, Any]]]:
    ids = sorted({int(pid) for pid in product_ids if pid})
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    rows = query(
        "SELECT i.id AS media_item_id, i.product_id AS media_product_id, "
        "       p.product_code, i.filename, i.display_name, i.object_key, i.created_at "
        "FROM media_items i "
        "JOIN media_products p ON p.id=i.product_id "
        f"WHERE i.product_id IN ({placeholders}) "
        "  AND i.lang='en' "
        "  AND i.deleted_at IS NULL "
        "  AND p.deleted_at IS NULL",
        tuple(ids),
    )
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rows or []:
        pid = _as_int(row.get("media_product_id"))
        if pid:
            out.setdefault(pid, []).append(row)
    return out


def _legacy_material_status_for_item(
    item: dict[str, Any],
    *,
    product_status: dict[str, Any],
    rows_by_product: dict[int, list[dict[str, Any]]],
    lookup_key: str,
) -> dict[str, Any] | None:
    product_id = _as_int(product_status.get("media_product_id"))
    if not product_id:
        return None
    card_keys = _legacy_material_match_keys(item)
    if not card_keys:
        return None
    for row in rows_by_product.get(product_id, []):
        media_keys = _legacy_media_row_match_keys(row)
        if any(_legacy_keys_match(card_key, media_key) for card_key in card_keys for media_key in media_keys):
            return {
                "status_scope": _AD_STATUS_SCOPE_MATERIAL,
                "lookup_key": lookup_key,
                "product_code": str(row.get("product_code") or product_status.get("product_code") or ""),
                "media_product_id": product_id,
                "media_item_id": row.get("media_item_id"),
                "has_local_match": True,
                "has_running_ad": False,
                "ad_spend_usd": 0.0,
                "latest_activity_at": _iso_datetime(row.get("created_at")),
                "summary": {"source": "media_items_legacy_product_scope"},
                "refreshed_at": None,
            }
    return None


def _media_search_url(media_search_code: str) -> str:
    return f"/medias/?q={quote(media_search_code, safe='')}" if media_search_code else ""


def _empty_ad_status(scope: str, lookup_key: str) -> dict[str, Any]:
    return {
        "status_scope": scope,
        "lookup_key": lookup_key,
        "product_code": "",
        "media_product_id": None,
        "media_item_id": None,
        "has_local_match": False,
        "has_running_ad": False,
        "ad_spend_usd": 0.0,
        "latest_activity_at": None,
        "ai_evaluation_result": "",
        "ai_evaluation_detail": "",
        "fine_ai_evaluation": None,
        "summary": {},
        "refreshed_at": None,
    }


def _serialize_ad_status_row(row: dict[str, Any] | None, *, scope: str, lookup_key: str) -> dict[str, Any]:
    if not row:
        return _empty_ad_status(scope, lookup_key)
    return {
        "status_scope": str(row.get("status_scope") or scope),
        "lookup_key": str(row.get("lookup_key") or lookup_key),
        "product_code": str(row.get("product_code") or ""),
        "media_product_id": row.get("media_product_id"),
        "media_item_id": row.get("media_item_id"),
        "has_local_match": bool(row.get("has_local_match")),
        "has_running_ad": bool(row.get("has_running_ad")),
        "ad_spend_usd": _as_float(row.get("ad_spend_usd")),
        "latest_activity_at": _iso_datetime(row.get("latest_activity_at")),
        "ai_evaluation_result": str(row.get("ai_evaluation_result") or ""),
        "ai_evaluation_detail": row.get("ai_evaluation_detail") or "",
        "fine_ai_evaluation": row.get("fine_ai_evaluation") or None,
        "summary": _json_loads(row.get("summary_json"), {}) or {},
        "refreshed_at": _iso_datetime(row.get("refreshed_at")),
    }


def _status_cache_by_hash(scope: str, lookup_hashes: set[str]) -> dict[str, dict[str, Any]]:
    hashes = [value for value in sorted(lookup_hashes) if value]
    if not hashes:
        return {}
    placeholders = ",".join(["%s"] * len(hashes))
    rows = query(
        "SELECT * FROM mingkong_material_ad_status_cache "
        f"WHERE status_scope = %s AND lookup_hash IN ({placeholders})",
        tuple([scope] + hashes),
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        row_scope = str(row.get("status_scope") or "")
        row_hash = str(row.get("lookup_hash") or "")
        if row_scope == scope and row_hash:
            out[row_hash] = row
    return out


def _ai_evaluation_status_by_product_ids(product_ids: set[int]) -> dict[int, dict[str, Any]]:
    ids = [int(pid) for pid in sorted(product_ids) if int(pid) > 0]
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    try:
        rows = query(
            f"""
            SELECT id, ai_evaluation_result, ai_evaluation_detail
            FROM media_products
            WHERE deleted_at IS NULL AND id IN ({placeholders})
            """,
            tuple(ids),
        )
    except Exception:
        logger.exception("failed to load media product AI evaluation statuses")
        return {}
    out: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        product_id = _as_int(row.get("id"))
        if product_id <= 0:
            continue
        out[product_id] = {
            "ai_evaluation_result": str(row.get("ai_evaluation_result") or ""),
            "ai_evaluation_detail": row.get("ai_evaluation_detail") or "",
        }
    return out


def _fine_ai_country_payload(row: dict[str, Any]) -> dict[str, Any]:
    full_result = _json_loads(row.get("full_result_json"), {}) or {}
    if not isinstance(full_result, dict):
        full_result = {}
    decision = full_result.get("decision") or _json_loads(row.get("decision_json"), {}) or {}
    scores = full_result.get("scores") or _json_loads(row.get("scores_json"), {}) or {}
    code = str(full_result.get("country_code") or row.get("country_code") or "").upper()
    status = str(full_result.get("status") or row.get("status") or "").strip()
    payload = dict(full_result)
    payload.update(
        {
            "country_code": code,
            "country_name": full_result.get("country_name") or row.get("country_name") or "",
            "country_name_zh": full_result.get("country_name_zh") or full_result.get("country_name") or row.get("country_name") or "",
            "status": status,
            "scores": scores if isinstance(scores, dict) else {},
            "decision": decision if isinstance(decision, dict) else {},
            "error_message": row.get("error_message") or full_result.get("error_message") or "",
        }
    )
    return payload


def _fine_ai_has_result(status: str, countries: dict[str, dict[str, Any]]) -> bool:
    value = str(status or "").lower()
    if value not in {"completed", "partially_completed"}:
        return False
    return bool(countries)


def _fine_ai_status_by_product_ids(product_ids: set[int]) -> dict[int, dict[str, Any]]:
    ids = [int(pid) for pid in sorted(product_ids) if int(pid) > 0]
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    try:
        rows = query(
            f"""
            SELECT id, evaluation_run_id, product_id, status,
                   summary_json, frontend_json, progress_json,
                   created_at, updated_at, completed_at, failed_at
            FROM ai_evaluation_runs
            WHERE product_id IN ({placeholders})
            ORDER BY product_id ASC, created_at DESC, id DESC
            """,
            tuple(ids),
        )
    except Exception:
        logger.exception("failed to load fine AI evaluation runs for mingkong cards")
        return {}

    latest_by_product: dict[int, dict[str, Any]] = {}
    run_ids: list[str] = []
    for row in rows or []:
        product_id = _as_int(row.get("product_id"))
        run_id = str(row.get("evaluation_run_id") or "").strip()
        if product_id <= 0 or not run_id or product_id in latest_by_product:
            continue
        latest_by_product[product_id] = row
        run_ids.append(run_id)

    countries_by_run: dict[str, dict[str, dict[str, Any]]] = {run_id: {} for run_id in run_ids}
    if run_ids:
        country_placeholders = ",".join(["%s"] * len(run_ids))
        try:
            country_rows = query(
                f"""
                SELECT evaluation_run_id, country_code, country_name, status,
                       scores_json, decision_json, full_result_json, error_message
                FROM ai_country_evaluations
                WHERE evaluation_run_id IN ({country_placeholders})
                ORDER BY id ASC
                """,
                tuple(run_ids),
            )
        except Exception:
            logger.exception("failed to load fine AI country evaluations for mingkong cards")
            country_rows = []
        for row in country_rows or []:
            run_id = str(row.get("evaluation_run_id") or "").strip()
            country = _fine_ai_country_payload(row)
            code = str(country.get("country_code") or row.get("country_code") or "").upper()
            if run_id and code:
                countries_by_run.setdefault(run_id, {})[code] = country

    out: dict[int, dict[str, Any]] = {}
    for product_id, row in latest_by_product.items():
        run_id = str(row.get("evaluation_run_id") or "").strip()
        status = str(row.get("status") or "").strip()
        countries = countries_by_run.get(run_id) or {}
        out[product_id] = {
            "evaluation_run_id": run_id,
            "product_id": product_id,
            "status": status,
            "has_result": _fine_ai_has_result(status, countries),
            "summary": _json_loads(row.get("summary_json"), {}) or {},
            "frontend": _json_loads(row.get("frontend_json"), {}) or {},
            "progress": _json_loads(row.get("progress_json"), {}) or {},
            "countries": countries,
            "created_at": _iso_datetime(row.get("created_at")),
            "updated_at": _iso_datetime(row.get("updated_at")),
            "completed_at": _iso_datetime(row.get("completed_at")),
            "failed_at": _iso_datetime(row.get("failed_at")),
        }
    return out


def _fine_ai_card_links(item: dict[str, Any]) -> set[str]:
    links = {
        str(item.get("mk_product_link") or "").strip(),
        str(item.get("product_url") or "").strip(),
    }
    metadata = _metadata_for_row(item)
    if isinstance(metadata, dict):
        links.add(str(metadata.get("product_link") or "").strip())
    return {link for link in links if link}


def _fine_ai_run_links(metadata: dict[str, Any]) -> set[str]:
    link_check = metadata.get("link_check") if isinstance(metadata, dict) else {}
    if not isinstance(link_check, dict):
        link_check = {}
    return {
        link
        for link in {
            str(metadata.get("external_product_link") or "").strip(),
            str(link_check.get("original_link") or "").strip(),
            str(link_check.get("selected_link") or "").strip(),
        }
        if link
    }


def _fine_ai_external_card_video_path(metadata: dict[str, Any]) -> str:
    card_video = metadata.get("external_card_video") if isinstance(metadata, dict) else {}
    if not isinstance(card_video, dict):
        return ""
    return normalize_mk_media_path(str(card_video.get("path") or card_video.get("source_path") or ""))


def _fine_ai_status_by_external_cards(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    card_index: dict[str, dict[str, Any]] = {}
    for item in items or []:
        material_key = str(item.get("material_key") or "").strip()
        video_path = normalize_mk_media_path(str(item.get("video_path") or ""))
        links = _fine_ai_card_links(item)
        if material_key and video_path and links:
            card_index[material_key] = {
                "video_path": video_path,
                "links": links,
            }
    if not card_index:
        return {}

    matched: dict[str, dict[str, Any]] = {}
    run_ids: list[str] = []
    
    # 1. First, retrieve auto-evaluation runs from mingkong_fine_ai_auto_evaluations
    auto_run_ids = set()
    run_to_material: dict[str, str] = {}
    auto_lookup = {material_key.lower(): material_key for material_key in card_index}
    auto_keys = list(auto_lookup)
    if auto_keys:
        placeholders = ",".join(["%s"] * len(auto_keys))
        try:
            auto_rows = query(
                f"""
                SELECT material_key, evaluation_run_id
                FROM mingkong_fine_ai_auto_evaluations
                WHERE material_key IN ({placeholders})
                  AND status IN ('completed', 'partially_completed')
                  AND evaluation_run_id IS NOT NULL
                  AND evaluation_run_id <> ''
                ORDER BY updated_at DESC, id DESC
                """,
                tuple(auto_keys),
            )
            for row in auto_rows or []:
                material_key = auto_lookup.get(str(row.get("material_key") or "").strip().lower())
                run_id = str(row.get("evaluation_run_id") or "").strip()
                if material_key and run_id:
                    auto_run_ids.add(run_id)
                    run_to_material[run_id] = material_key
        except Exception:
            logger.exception("failed to load Mingkong fine AI auto evaluation links")

    # 2. Extract card video paths for manual/external runs
    video_paths = {
        normalize_mk_media_path(str(item.get("video_path") or ""))
        for item in items or []
        if str(item.get("video_path") or "").strip()
    }
    
    run_rows = []
    if video_paths:
        placeholders = ",".join(["%s"] * len(video_paths))
        try:
            # Retrieve runs matching any card video path, regardless of product_id
            run_rows = query(
                f"""
                SELECT id, evaluation_run_id, product_id, status,
                       summary_json, frontend_json, progress_json, metadata_json,
                       created_at, updated_at, completed_at, failed_at
                FROM ai_evaluation_runs
                WHERE (
                  JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.card_video_path')) IN ({placeholders})
                  OR JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.external_card_video.path')) IN ({placeholders})
                  OR JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.video_path')) IN ({placeholders})
                )
                  AND status IN ('completed', 'partially_completed')
                ORDER BY created_at DESC, id DESC
                """,
                tuple(list(video_paths) * 3)
            )
        except Exception:
            logger.exception("failed to load real-time fine AI evaluation runs by video paths")

    # Fetch any missing auto-runs that were not returned by video path search
    fetched_run_ids = {str(row.get("evaluation_run_id")) for row in run_rows if row.get("evaluation_run_id")}
    missing_auto_run_ids = auto_run_ids - fetched_run_ids
    if missing_auto_run_ids:
        placeholders = ",".join(["%s"] * len(missing_auto_run_ids))
        try:
            auto_run_rows = query(
                f"""
                SELECT id, evaluation_run_id, product_id, status,
                       summary_json, frontend_json, progress_json, metadata_json,
                       created_at, updated_at, completed_at, failed_at
                FROM ai_evaluation_runs
                WHERE evaluation_run_id IN ({placeholders})
                  AND status IN ('completed', 'partially_completed')
                ORDER BY created_at DESC, id DESC
                """,
                tuple(missing_auto_run_ids),
            )
            run_rows.extend(auto_run_rows)
        except Exception:
            logger.exception("failed to load missing auto evaluation runs")

    # Map by material_key first (100% robust for auto-evaluations)
    for row in run_rows:
        run_id = str(row.get("evaluation_run_id") or "").strip()
        material_key = run_to_material.get(run_id)
        if material_key and material_key not in matched:
            matched[material_key] = row
            run_ids.append(run_id)

    # For manual/external runs, prefer video+link matches, then fall back to
    # video-only. The evaluation belongs to the rendered video card; historical
    # range cards can carry a drifted product link while the video path stays
    # stable.
    matched_run_ids = {str(row.get("evaluation_run_id") or "").strip() for row in matched.values()}

    def _match_external_runs(*, require_link_match: bool) -> None:
        for row in run_rows:
            run_id = str(row.get("evaluation_run_id") or "").strip()
            if not run_id or run_id in matched_run_ids:
                continue
            metadata = _json_loads(row.get("metadata_json"), {}) or {}
            if not isinstance(metadata, dict):
                continue
            run_video_path = _fine_ai_external_card_video_path(metadata)
            if not run_video_path:
                continue
            run_links = _fine_ai_run_links(metadata)
            for material_key, card in card_index.items():
                if material_key in matched:
                    continue
                if run_video_path != card["video_path"]:
                    continue
                if require_link_match and not (run_links and (run_links & card["links"])):
                    continue
                matched[material_key] = row
                run_ids.append(run_id)
                matched_run_ids.add(run_id)
                break

    _match_external_runs(require_link_match=True)
    _match_external_runs(require_link_match=False)

    countries_by_run: dict[str, dict[str, dict[str, Any]]] = {run_id: {} for run_id in run_ids}
    if run_ids:
        placeholders = ",".join(["%s"] * len(run_ids))
        try:
            country_rows = query(
                f"""
                SELECT evaluation_run_id, country_code, country_name, status,
                       scores_json, decision_json, full_result_json, error_message
                FROM ai_country_evaluations
                WHERE evaluation_run_id IN ({placeholders})
                ORDER BY id ASC
                """,
                tuple(run_ids),
            )
        except Exception:
            logger.exception("failed to load external fine AI country evaluations for mingkong cards")
            country_rows = []
        for row in country_rows or []:
            run_id = str(row.get("evaluation_run_id") or "").strip()
            country = _fine_ai_country_payload(row)
            code = str(country.get("country_code") or row.get("country_code") or "").upper()
            if run_id and code:
                countries_by_run.setdefault(run_id, {})[code] = country

    out: dict[str, dict[str, Any]] = {}
    for material_key, row in matched.items():
        run_id = str(row.get("evaluation_run_id") or "").strip()
        status = str(row.get("status") or "").strip()
        countries = countries_by_run.get(run_id) or {}
        out[material_key] = {
            "evaluation_run_id": run_id,
            "product_id": 0,
            "status": status,
            "has_result": _fine_ai_has_result(status, countries),
            "summary": _json_loads(row.get("summary_json"), {}) or {},
            "frontend": _json_loads(row.get("frontend_json"), {}) or {},
            "progress": _json_loads(row.get("progress_json"), {}) or {},
            "countries": countries,
            "created_at": _iso_datetime(row.get("created_at")),
            "updated_at": _iso_datetime(row.get("updated_at")),
            "completed_at": _iso_datetime(row.get("completed_at")),
            "failed_at": _iso_datetime(row.get("failed_at")),
        }
    return out


def _enrich_cached_ad_statuses(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    product_hashes: set[str] = set()
    material_hashes: set[str] = set()
    per_item: list[tuple[dict[str, Any], str, str, str, str]] = []

    for item in items:
        media_code = media_search_code_for(item.get("product_code") or item.get("product_handle"))
        material_key = _material_status_lookup_key(item.get("video_path"))
        product_hash = status_lookup_hash(media_code) if media_code else ""
        material_hash = status_lookup_hash(material_key) if material_key else ""
        if product_hash:
            product_hashes.add(product_hash)
        if material_hash:
            material_hashes.add(material_hash)
        per_item.append((item, media_code, material_key, product_hash, material_hash))

    product_cache = _status_cache_by_hash(_AD_STATUS_SCOPE_PRODUCT, product_hashes)
    material_cache = _status_cache_by_hash(_AD_STATUS_SCOPE_MATERIAL, material_hashes)

    matched_product_ids: set[int] = set()
    preliminary: list[tuple[dict[str, Any], str, str, str, str, dict[str, Any], dict[str, Any]]] = []
    for item, media_code, material_key, product_hash, material_hash in per_item:
        product_status = _serialize_ad_status_row(
            product_cache.get(product_hash),
            scope=_AD_STATUS_SCOPE_PRODUCT,
            lookup_key=media_code,
        )
        material_status = _serialize_ad_status_row(
            material_cache.get(material_hash),
            scope=_AD_STATUS_SCOPE_MATERIAL,
            lookup_key=material_key,
        )
        product_id = _as_int(product_status.get("media_product_id"))
        if product_id and not material_status["has_local_match"]:
            matched_product_ids.add(product_id)
        preliminary.append((item, media_code, material_key, product_hash, material_hash, product_status, material_status))

    legacy_rows_by_product = _legacy_material_rows_by_product(matched_product_ids)
    product_ids = {
        _as_int(product_status.get("media_product_id"))
        for *_prefix, product_status, _material_status in preliminary
        if _as_int(product_status.get("media_product_id")) > 0
    }
    ai_status_by_product_id = _ai_evaluation_status_by_product_ids(product_ids)
    fine_ai_status_by_product_id = _fine_ai_status_by_product_ids(product_ids)
    fine_ai_status_by_material_key = _fine_ai_status_by_external_cards(items)

    for item, media_code, material_key, _product_hash, _material_hash, product_status, material_status in preliminary:
        product_id = _as_int(product_status.get("media_product_id"))
        if product_id in ai_status_by_product_id:
            product_status.update(ai_status_by_product_id[product_id])
        # Prioritize card-specific fine AI evaluation over product-level fallback
        product_status["fine_ai_evaluation"] = (
            fine_ai_status_by_material_key.get(str(item.get("material_key") or "").strip())
            or fine_ai_status_by_product_id.get(product_id)
        )
        if not material_status["has_local_match"]:
            legacy_status = _legacy_material_status_for_item(
                item,
                product_status=product_status,
                rows_by_product=legacy_rows_by_product,
                lookup_key=material_key,
            )
            if legacy_status:
                material_status = legacy_status
        item["media_search_code"] = media_code
        item["media_search_url"] = _media_search_url(media_code)
        item["product_ad_status"] = product_status
        item["material_ad_status"] = material_status
        item["has_local_product_in_library"] = bool(product_status["has_local_match"])
        item["has_local_product_running_ad"] = bool(
            product_status["has_local_match"] and product_status["has_running_ad"]
        )
        material_in_library = bool(material_status["has_local_match"])
        item["has_local_material_in_library"] = material_in_library
        # Backward-compatible alias for older callers/templates. Mingkong videos are
        # raw source materials, so the card icon means local library match only.
        item["has_local_material_running_ad"] = material_in_library
    return items


def _distinct_archive_product_codes() -> list[str]:
    rows = query(
        """
        SELECT DISTINCT product_code
        FROM (
            SELECT product_code FROM mingkong_material_daily_snapshots
            UNION
            SELECT product_code FROM mingkong_material_daily_top100
        ) archive_codes
        WHERE product_code IS NOT NULL AND product_code <> ''
        """,
        (),
    )
    return [str(row.get("product_code") or "").strip() for row in rows or [] if str(row.get("product_code") or "").strip()]


def _distinct_archive_video_paths() -> list[str]:
    rows = query(
        """
        SELECT DISTINCT video_path
        FROM (
            SELECT video_path FROM mingkong_material_daily_snapshots
            UNION
            SELECT video_path FROM mingkong_material_daily_top100
        ) archive_paths
        WHERE video_path IS NOT NULL AND video_path <> ''
        """,
        (),
    )
    out: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        path = _material_status_lookup_key(row.get("video_path"))
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _upsert_ad_status_cache(
    *,
    scope: str,
    lookup_key: str,
    product_code: str = "",
    media_product_id: int | None = None,
    media_item_id: int | None = None,
    has_local_match: bool = False,
    has_running_ad: bool = False,
    ad_spend_usd: float = 0.0,
    latest_activity_at: Any = None,
    summary: dict[str, Any] | None = None,
) -> None:
    execute(
        """
        INSERT INTO mingkong_material_ad_status_cache
        (status_scope, lookup_hash, lookup_key, product_code, media_product_id,
         has_local_match, ad_spend_usd, has_running_ad, media_item_id,
         latest_activity_at, summary_json, refreshed_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON DUPLICATE KEY UPDATE
          lookup_key=VALUES(lookup_key),
          product_code=VALUES(product_code),
          media_product_id=VALUES(media_product_id),
          media_item_id=VALUES(media_item_id),
          has_local_match=VALUES(has_local_match),
          has_running_ad=VALUES(has_running_ad),
          ad_spend_usd=VALUES(ad_spend_usd),
          latest_activity_at=VALUES(latest_activity_at),
          summary_json=VALUES(summary_json),
          refreshed_at=NOW(),
          updated_at=NOW()
        """,
        (
            scope,
            status_lookup_hash(lookup_key),
            lookup_key,
            product_code or None,
            media_product_id,
            1 if has_local_match else 0,
            float(ad_spend_usd or 0),
            1 if has_running_ad else 0,
            media_item_id,
            latest_activity_at,
            _json_dumps(summary or {}),
        ),
    )


def _refresh_product_ad_status(media_search_code: str) -> dict[str, Any]:
    product = query_one(
        "SELECT id, product_code, name FROM media_products "
        "WHERE product_code=%s AND deleted_at IS NULL",
        (media_search_code,),
    )
    product_id = int(product["id"]) if product and product.get("id") else None
    ad_row = None
    if product_id:
        ad_row = query_one(
            "SELECT COALESCE(SUM(spend_usd), 0) AS ad_spend_usd, "
            "MAX(COALESCE(meta_business_date, report_date)) AS latest_activity_at "
            "FROM meta_ad_daily_campaign_metrics "
            "WHERE product_id=%s AND COALESCE(spend_usd, 0) > 0 "
            "AND COALESCE(meta_business_date, report_date) >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)",
            (product_id,),
        ) or {}
    campaign_codes = list(dict.fromkeys([_strip_rjc(media_search_code), media_search_code]))
    realtime_row = None
    if product_id and campaign_codes:
        placeholders = ",".join(["%s"] * len(campaign_codes))
        realtime_row = query_one(
            "SELECT COALESCE(MAX(spend_usd), 0) AS ad_spend_usd, "
            "MAX(snapshot_at) AS latest_activity_at "
            "FROM meta_ad_realtime_daily_campaign_metrics "
            f"WHERE normalized_campaign_code IN ({placeholders}) "
            "AND COALESCE(spend_usd, 0) > 0 "
            "AND business_date >= DATE_SUB(CURDATE(), INTERVAL 3 DAY)",
            tuple(campaign_codes),
        ) or {}
    daily_spend = _as_float((ad_row or {}).get("ad_spend_usd"))
    realtime_spend = _as_float((realtime_row or {}).get("ad_spend_usd"))
    ad_spend = max(daily_spend, realtime_spend)
    latest_activity_at = (realtime_row or {}).get("latest_activity_at") or (ad_row or {}).get("latest_activity_at")
    status = {
        "media_product_id": product_id,
        "product_code": (product or {}).get("product_code") or media_search_code,
        "has_local_match": bool(product_id),
        "has_running_ad": bool(product_id and ad_spend > 0),
        "ad_spend_usd": ad_spend,
        "latest_activity_at": latest_activity_at,
        "summary": {
            "source": "meta_ad_daily_campaign_metrics+meta_ad_realtime_daily_campaign_metrics",
            "daily_lookback_days": 30,
            "realtime_lookback_days": 3,
        },
    }
    _upsert_ad_status_cache(
        scope=_AD_STATUS_SCOPE_PRODUCT,
        lookup_key=media_search_code,
        product_code=status["product_code"],
        media_product_id=status["media_product_id"],
        has_local_match=status["has_local_match"],
        has_running_ad=status["has_running_ad"],
        ad_spend_usd=status["ad_spend_usd"],
        latest_activity_at=status["latest_activity_at"],
        summary=status["summary"],
    )
    return status


def _refresh_material_ad_status(video_path: str) -> dict[str, Any]:
    row = query_one(
        """
        SELECT i.id AS media_item_id, i.product_id AS media_product_id,
               p.product_code, i.pushed_at,
               (SELECT COUNT(*) FROM media_push_logs mpl
                WHERE mpl.item_id=i.id AND mpl.status='success') AS push_success_count
        FROM media_item_mk_bindings b
        JOIN media_items i ON i.id = b.media_item_id
        JOIN media_products p ON p.id = i.product_id
        WHERE b.mk_video_path=%s
          AND i.deleted_at IS NULL
          AND p.deleted_at IS NULL
        ORDER BY i.pushed_at DESC, i.id DESC
        LIMIT 1
        """,
        (video_path,),
    )
    item_id = int(row["media_item_id"]) if row and row.get("media_item_id") else None
    has_push = bool(row and (row.get("pushed_at") or _as_int(row.get("push_success_count")) > 0))
    status = {
        "media_product_id": row.get("media_product_id") if row else None,
        "media_item_id": item_id,
        "product_code": row.get("product_code") if row else "",
        "has_local_match": bool(item_id),
        "has_running_ad": bool(item_id and has_push),
        "ad_spend_usd": 0.0,
        "latest_activity_at": row.get("pushed_at") if row else None,
        "summary": {"source": "media_item_mk_bindings+media_push_logs"},
    }
    _upsert_ad_status_cache(
        scope=_AD_STATUS_SCOPE_MATERIAL,
        lookup_key=video_path,
        product_code=status["product_code"],
        media_product_id=status["media_product_id"],
        media_item_id=status["media_item_id"],
        has_local_match=status["has_local_match"],
        has_running_ad=status["has_running_ad"],
        ad_spend_usd=status["ad_spend_usd"],
        latest_activity_at=status["latest_activity_at"],
        summary=status["summary"],
    )
    return status


def refresh_ad_status_cache() -> dict[str, Any]:
    guard_against_windows_local_mysql()
    run_id = scheduled_tasks.start_run("mingkong_material_ad_status_refresh")
    try:
        product_codes = sorted({media_search_code_for(code) for code in _distinct_archive_product_codes()})
        product_codes = [code for code in product_codes if code]
        video_paths = _distinct_archive_video_paths()

        product_statuses = 0
        product_running = 0
        for code in product_codes:
            status = _refresh_product_ad_status(code)
            product_statuses += 1
            if status["has_running_ad"]:
                product_running += 1

        material_statuses = 0
        material_running = 0
        for path in video_paths:
            status = _refresh_material_ad_status(path)
            material_statuses += 1
            if status["has_running_ad"]:
                material_running += 1

        summary = {
            "product_statuses": product_statuses,
            "product_running_ad_statuses": product_running,
            "material_statuses": material_statuses,
            "material_running_ad_statuses": material_running,
        }
        scheduled_tasks.finish_run(run_id, status="success", summary=summary)
        return summary
    except Exception as exc:
        scheduled_tasks.finish_run(run_id, status="failed", error_message=str(exc))
        raise


def _page_bounds(page: int | str | None, page_size: int | str | None) -> tuple[int, int, int]:
    page_num = max(1, _as_int(page, 1))
    size = min(100, max(1, _as_int(page_size, 100)))
    return page_num, size, (page_num - 1) * size


def _material_keyword_condition(alias: str, keyword: str) -> tuple[str, list[Any]]:
    kw = str(keyword or "").strip()
    if not kw:
        return "", []

    product_terms = [kw]
    lowered = kw.lower()
    stripped = _strip_rjc(lowered)
    existing = {term.lower() for term in product_terms}
    if stripped and stripped not in existing:
        product_terms.append(stripped)
        existing.add(stripped)
    with_rjc = f"{stripped}-rjc" if stripped else ""
    if with_rjc and with_rjc not in existing:
        product_terms.append(with_rjc)

    columns = [
        f"{alias}.product_code",
        f"{alias}.product_name",
        f"{alias}.mk_product_name",
        f"{alias}.video_name",
        f"{alias}.video_path",
    ]
    clauses: list[str] = []
    args: list[Any] = []
    for column in columns:
        column_terms = product_terms if column.endswith(".product_code") else [kw]
        for term in column_terms:
            clauses.append(f"{column} LIKE %s")
            args.append(f"%{term}%")
    return "(" + " OR ".join(clauses) + ")", args


def material_key_for(product_code: str, mk_product_id: int | str | None, video_path: str) -> str:
    normalized_path = normalize_mk_media_path(video_path)
    raw = "|".join(
        [
            str(product_code or "").strip().lower(),
            str(mk_product_id or "").strip(),
            normalized_path,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def local_cover_object_key_for(row: dict[str, Any]) -> str:
    material_key = str(row.get("material_key") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", material_key):
        raw = "|".join(
            [
                str(row.get("product_code") or "").strip().lower(),
                str(row.get("mk_product_id") or "").strip(),
                normalize_mk_media_path(str(row.get("video_image_path") or "")),
            ]
        )
        material_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    cover_path = normalize_mk_media_path(str(row.get("video_image_path") or ""))
    ext = os.path.splitext(urlparse(cover_path).path)[1].lower()
    if ext not in _COVER_EXTENSIONS:
        ext = ".jpg"
    return f"{_COVER_CACHE_PREFIX}/{material_key[:2]}/{material_key}{ext}"


def cache_local_cover_for_material(
    row: dict[str, Any],
    *,
    session: requests.Session,
    base_url: str,
    headers: dict[str, str],
    timeout_seconds: int,
    storage_exists_fn=local_media_storage.exists,
    write_bytes_fn=local_media_storage.write_bytes,
) -> dict[str, Any]:
    out = dict(row)
    cover_path = normalize_mk_media_path(str(out.get("video_image_path") or ""))
    out.setdefault("local_cover_object_key", None)
    out.setdefault("cover_cached_at", None)
    out.setdefault("cover_cache_error", None)
    if not cover_path:
        return out

    object_key = str(out.get("local_cover_object_key") or "").strip() or local_cover_object_key_for(out)
    try:
        if storage_exists_fn(object_key):
            out["local_cover_object_key"] = object_key
            out["cover_cache_error"] = None
            return out

        image_headers = dict(headers)
        image_headers.pop("Content-Type", None)
        image_headers["Accept"] = "image/*,*/*;q=0.8"
        url = f"{base_url}/medias/{quote(cover_path, safe='/')}"
        resp = session.get(url, headers=image_headers, timeout=timeout_seconds)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith("image/"):
            raise ValueError(f"Mingkong cover returned non-image content: {content_type}")
        payload = bytes(resp.content or b"")
        if not payload:
            raise ValueError("Mingkong cover returned empty content")
        write_bytes_fn(object_key, payload)
        out["local_cover_object_key"] = object_key
        out["cover_cached_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        out["cover_cache_error"] = None
    except Exception as exc:
        out["local_cover_object_key"] = None
        out["cover_cached_at"] = None
        out["cover_cache_error"] = str(exc)[:1000]
    return out


def latest_top_products(*, limit: int = 300) -> tuple[str, list[dict[str, Any]]]:
    row = query_one("SELECT MAX(snapshot_date) AS snapshot_date FROM dianxiaomi_rankings") or {}
    snapshot_date = _coerce_date(row.get("snapshot_date"))
    if not snapshot_date:
        return "", []
    rows = query(
        """
        SELECT rank_position, product_id, product_name, product_url, store,
               sales_count, order_count, revenue_main
        FROM dianxiaomi_rankings
        WHERE snapshot_date = %s
        ORDER BY rank_position ASC
        LIMIT %s
        """,
        (snapshot_date, int(limit)),
    )
    products: list[dict[str, Any]] = []
    for item in rows or []:
        handle = _product_handle(item.get("product_url"))
        if not handle:
            continue
        products.append(
            {
                "ranking_snapshot_date": snapshot_date,
                "rank_position": _as_int(item.get("rank_position")),
                "shopify_product_id": str(item.get("product_id") or ""),
                "product_code": handle,
                "product_name": _trim(item.get("product_name"), 500),
                "product_url": str(item.get("product_url") or ""),
                "store": str(item.get("store") or ""),
                "sales_count": _as_int(item.get("sales_count")),
                "order_count": _as_int(item.get("order_count")),
                "revenue_main": str(item.get("revenue_main") or ""),
            }
        )
    return snapshot_date, products


def flatten_materials_for_product(
    *,
    source_product: dict[str, Any],
    mk_product: dict[str, Any],
) -> list[dict[str, Any]]:
    product_code = str(source_product.get("product_code") or "").strip().lower()
    mk_product_id = mk_product.get("id")
    product_links = [
        str(item or "").strip()
        for item in (mk_product.get("product_links") or [])
        if str(item or "").strip()
    ]
    mk_product_link = product_links[0] if product_links else str(source_product.get("product_url") or "")
    out: list[dict[str, Any]] = []
    for raw in mk_product.get("videos") or []:
        if not isinstance(raw, dict) or raw.get("hidden"):
            continue
        path = normalize_mk_media_path(str(raw.get("path") or ""))
        if not path:
            continue
        image_path = normalize_mk_media_path(str(raw.get("image_path") or ""))
        spends_text = str(raw.get("spends") or "").strip()
        spends = _as_float(spends_text)
        metadata = dict(raw)
        metadata.update(
            {
                "mk_id": mk_product_id,
                "product_name": mk_product.get("product_name") or "",
                "product_link": mk_product_link,
                "main_image": mk_product.get("main_image") or mk_product.get("image") or "",
                "product_code": product_code,
                "video_path": path,
                "cover_path": image_path,
            }
        )
        out.append(
            {
                "material_key": material_key_for(product_code, mk_product_id, path),
                "product_code": product_code,
                "rank_position": _as_int(source_product.get("rank_position")),
                "shopify_product_id": str(source_product.get("shopify_product_id") or ""),
                "product_name": _trim(source_product.get("product_name"), 500),
                "product_url": str(source_product.get("product_url") or ""),
                "mk_product_id": int(mk_product_id) if str(mk_product_id or "").isdigit() else mk_product_id,
                "mk_product_name": _trim(mk_product.get("product_name"), 500),
                "mk_product_link": mk_product_link,
                "main_image": str(mk_product.get("main_image") or mk_product.get("image") or ""),
                "video_name": _trim(raw.get("name"), 500),
                "video_path": path,
                "video_image_path": image_path,
                "cumulative_90_spend": spends,
                "video_spends": spends,
                "video_spends_text": spends_text,
                "video_ads_count": _as_int(raw.get("ads_count")),
                "video_author": _trim(raw.get("author"), 128),
                "video_upload_time": _trim(raw.get("upload_time"), 64),
                "video_duration_seconds": _as_float(
                    raw.get("duration_seconds") or raw.get("duration"),
                    0.0,
                ),
                "mk_video_metadata": metadata,
            }
        )
    out.sort(
        key=lambda row: (
            float(row.get("cumulative_90_spend") or 0),
            int(row.get("video_ads_count") or 0),
        ),
        reverse=True,
    )
    return out


def product_video_aggregate_stats(item: dict[str, Any]) -> dict[str, Any]:
    video_count = 0
    total_90_spend = 0.0
    total_ads = 0
    for raw in item.get("videos") or []:
        if not isinstance(raw, dict) or raw.get("hidden"):
            continue
        video_count += 1
        total_90_spend += _as_float(raw.get("spends"))
        total_ads += _as_int(raw.get("ads_count"))
    return {
        "video_count": video_count,
        "total_90_spend": round(total_90_spend, 2),
        "total_ads": total_ads,
    }


def choose_previous_snapshot_for_24h(
    current_snapshot_at: Any,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    current_dt = _parse_datetime(current_snapshot_at)
    if current_dt is None:
        return None
    best: tuple[tuple[float, float], dict[str, Any]] | None = None
    for raw in candidates:
        previous_dt = _parse_datetime(raw.get("snapshot_at"))
        if previous_dt is None or previous_dt >= current_dt:
            continue
        interval_seconds = int((current_dt - previous_dt).total_seconds())
        candidate = dict(raw)
        candidate["snapshot_date"] = _coerce_date(candidate.get("snapshot_date"))
        candidate["snapshot_at"] = _coerce_datetime(previous_dt)
        candidate["snapshot_slot"] = str(candidate.get("snapshot_slot") or _snapshot_slot_for(previous_dt))
        candidate["comparison_interval_seconds"] = interval_seconds
        score = (abs(interval_seconds - _SECONDS_PER_DAY), -previous_dt.timestamp())
        if best is None or score < best[0]:
            best = (score, candidate)
    return best[1] if best else None


def build_top100_rows(
    *,
    snapshot_date: str,
    snapshot_at: str | None = None,
    previous_snapshot_date: str | None,
    previous_snapshot_at: str | None = None,
    previous_snapshot_slot: str | None = None,
    comparison_interval_seconds: int | None = None,
    current_rows: list[dict[str, Any]],
    previous_by_key: dict[str, dict[str, Any]],
    previous_top100_keys: set[str],
    previous_product_codes: set[str] | None = None,
    limit: int = YESTERDAY_SPEND_TOP_LIMIT,
) -> list[dict[str, Any]]:
    resolved_snapshot_at = _coerce_datetime(snapshot_at) if snapshot_at else ""
    snapshot_slot = _snapshot_slot_for(resolved_snapshot_at) if resolved_snapshot_at else ""
    normalized_previous_product_codes = (
        {_normalized_product_code(item) for item in previous_product_codes if _normalized_product_code(item)}
        if previous_product_codes is not None
        else None
    )
    has_previous_snapshot = bool(previous_snapshot_date or previous_snapshot_at)
    ranked: list[dict[str, Any]] = []
    for current in current_rows:
        material_key = str(current.get("material_key") or "")
        if not material_key:
            continue
        current_metadata = _metadata_for_row(current)
        current_spend = _spend_from_row(current, "cumulative_90_spend", current_metadata)
        previous = previous_by_key.get(material_key)
        previous_spend = (
            None
            if previous is None
            else _spend_from_row(previous, "cumulative_90_spend", _metadata_for_row(previous))
        )
        delta = _delta_from_previous_baseline(
            current_spend=current_spend,
            previous_spend=previous_spend,
            product_code=current.get("product_code"),
            previous_product_codes=normalized_previous_product_codes,
            has_previous_snapshot=has_previous_snapshot,
        )
        row = dict(current)
        row["source_product_rank_position"] = _as_int(
            current.get("source_product_rank_position") or current.get("rank_position")
        )
        row.update(
            {
                "snapshot_date": snapshot_date,
                "snapshot_at": resolved_snapshot_at,
                "snapshot_slot": snapshot_slot,
                "previous_snapshot_date": previous_snapshot_date,
                "previous_snapshot_at": previous_snapshot_at,
                "previous_snapshot_slot": previous_snapshot_slot,
                "comparison_interval_seconds": comparison_interval_seconds,
                "previous_cumulative_90_spend": previous_spend,
                "current_cumulative_90_spend": current_spend,
                "yesterday_spend_delta": round(delta, 2),
                "is_new_material": previous is None,
                "is_new_top100_entry": material_key not in previous_top100_keys,
            }
        )
        ranked.append(row)

    ranked.sort(
        key=lambda row: (
            float(row.get("yesterday_spend_delta") or 0),
            float(row.get("current_cumulative_90_spend") or 0),
            int(row.get("video_ads_count") or 0),
            -int(row.get("rank_position") or 999999),
            str(row.get("material_key") or ""),
        ),
        reverse=True,
    )
    top_rows = ranked[: int(limit)]
    for index, row in enumerate(top_rows, start=1):
        row["rank_position"] = index

    top_rows.sort(
        key=lambda row: (
            1 if row.get("is_new_top100_entry") else 0,
            float(row.get("yesterday_spend_delta") or 0),
            float(row.get("current_cumulative_90_spend") or 0),
            int(row.get("video_ads_count") or 0),
            -int(row.get("rank_position") or 999999),
        ),
        reverse=True,
    )
    for index, row in enumerate(top_rows, start=1):
        row["display_position"] = index
    return top_rows


def _latest_snapshot_date(table: str) -> str:
    row = query_one(f"SELECT MAX(snapshot_date) AS snapshot_date FROM {table}") or {}
    return _coerce_date(row.get("snapshot_date"))


def _latest_snapshot_identity(
    table: str,
    *,
    snapshot_date: str | None = None,
    snapshot_at: str | None = None,
) -> dict[str, str]:
    if snapshot_at:
        at = _coerce_datetime(snapshot_at)
        return {"snapshot_date": _coerce_date(snapshot_date or at[:10]), "snapshot_at": at, "snapshot_slot": _snapshot_slot_for(at)}
    if snapshot_date:
        row = query_one(
            f"""
            SELECT snapshot_date, snapshot_at, snapshot_slot
            FROM {table}
            WHERE snapshot_date = %s
            GROUP BY snapshot_date, snapshot_at, snapshot_slot
            ORDER BY snapshot_at DESC, snapshot_date DESC
            LIMIT 1
            """,
            (_coerce_date(snapshot_date),),
        ) or {}
        if row:
            return {
                "snapshot_date": _coerce_date(row.get("snapshot_date")),
                "snapshot_at": _coerce_datetime(row.get("snapshot_at")),
                "snapshot_slot": str(row.get("snapshot_slot") or _snapshot_slot_for(row.get("snapshot_at"))),
            }
        return {"snapshot_date": _coerce_date(snapshot_date), "snapshot_at": "", "snapshot_slot": ""}
    row = query_one(
        f"""
        SELECT snapshot_date, snapshot_at, snapshot_slot
        FROM {table}
        GROUP BY snapshot_date, snapshot_at, snapshot_slot
        ORDER BY snapshot_at DESC, snapshot_date DESC
        LIMIT 1
        """
    ) or {}
    if row:
        return {
            "snapshot_date": _coerce_date(row.get("snapshot_date")),
            "snapshot_at": _coerce_datetime(row.get("snapshot_at")),
            "snapshot_slot": str(row.get("snapshot_slot") or _snapshot_slot_for(row.get("snapshot_at"))),
        }
    return {"snapshot_date": "", "snapshot_at": "", "snapshot_slot": ""}


def _material_range_bounds(range_key: str | None) -> tuple[str, str] | None:
    key = str(range_key or "").strip().lower().replace("-", "_")
    if not key:
        return None
    today = _today()
    week_start = today - timedelta(days=today.weekday())
    if key == "this_week":
        start = week_start
        end = week_start + timedelta(days=6)
    elif key == "last_week":
        start = week_start - timedelta(days=7)
        end = week_start - timedelta(days=1)
    elif key == "this_month":
        start = today.replace(day=1)
        end = today.replace(day=monthrange(today.year, today.month)[1])
    elif key == "last_month":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        start = last_month_end.replace(day=1)
        end = last_month_end
    else:
        return None
    return start.isoformat(), end.isoformat()


def _material_snapshot_identity(
    *,
    snapshot_date: str | None = None,
    snapshot_at: str | None = None,
) -> dict[str, str]:
    if snapshot_at:
        row = query_one(
            """
            SELECT snapshot_date, snapshot_at, snapshot_slot
            FROM mingkong_material_sync_runs
            WHERE status = 'success' AND snapshot_at = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (_coerce_datetime(snapshot_at),),
        ) or {}
    elif snapshot_date:
        row = query_one(
            """
            SELECT snapshot_date, snapshot_at, snapshot_slot
            FROM mingkong_material_sync_runs
            WHERE status = 'success' AND snapshot_date = %s
            ORDER BY snapshot_at DESC, id DESC
            LIMIT 1
            """,
            (_coerce_date(snapshot_date),),
        ) or {}
    else:
        row = query_one(
            """
            SELECT snapshot_date, snapshot_at, snapshot_slot
            FROM mingkong_material_sync_runs
            WHERE status = 'success'
            ORDER BY snapshot_at DESC, id DESC
            LIMIT 1
            """
        ) or {}
    if not row:
        return {"snapshot_date": "", "snapshot_at": "", "snapshot_slot": ""}
    return {
        "snapshot_date": _coerce_date(row.get("snapshot_date")),
        "snapshot_at": _coerce_datetime(row.get("snapshot_at")),
        "snapshot_slot": str(row.get("snapshot_slot") or _snapshot_slot_for(row.get("snapshot_at"))),
    }


def _run_summary(snapshot_date: str, snapshot_at: str | None = None) -> dict[str, Any] | None:
    if snapshot_at:
        row = query_one(
            """
            SELECT id, snapshot_date, snapshot_at, snapshot_slot, ranking_snapshot_date, status,
                   source_product_limit, source_product_count, processed_product_count, material_count,
                   failed_product_count, summary_json, error_message, started_at, finished_at
            FROM mingkong_material_sync_runs
            WHERE snapshot_at = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (_coerce_datetime(snapshot_at),),
        )
    else:
        row = None
    if row is None:
        row = query_one(
            """
            SELECT id, snapshot_date, snapshot_at, snapshot_slot, ranking_snapshot_date, status,
                   source_product_limit, source_product_count, processed_product_count, material_count,
                   failed_product_count, summary_json, error_message, started_at, finished_at
            FROM mingkong_material_sync_runs
            WHERE snapshot_date = %s
            ORDER BY snapshot_at DESC, id DESC
            LIMIT 1
            """,
            (snapshot_date,),
        )
    if not row:
        return None
    out = dict(row)
    out["snapshot_date"] = _coerce_date(out.get("snapshot_date"))
    out["snapshot_at"] = _coerce_datetime(out.get("snapshot_at"))
    out["snapshot_slot"] = str(out.get("snapshot_slot") or _snapshot_slot_for(out.get("snapshot_at")))
    out["ranking_snapshot_date"] = _coerce_date(out.get("ranking_snapshot_date"))
    out["summary"] = _json_loads(out.pop("summary_json", None), {})
    for key in ("started_at", "finished_at"):
        value = out.get(key)
        out[key] = value.isoformat(sep=" ") if hasattr(value, "isoformat") else value
    return out

def _serialize_material_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["snapshot_date"] = _coerce_date(out.get("snapshot_date"))
    out["snapshot_at"] = _coerce_datetime(out.get("snapshot_at"))
    out["snapshot_slot"] = str(out.get("snapshot_slot") or _snapshot_slot_for(out.get("snapshot_at")))
    out["ranking_snapshot_date"] = _coerce_date(out.get("ranking_snapshot_date"))
    metadata = _metadata_for_row(out)
    spend = _spend_from_row(out, "cumulative_90_spend", metadata)
    out["cumulative_90_spend"] = spend
    out["video_spends"] = spend
    out["video_spends_text"] = _raw_spend_text(out, metadata)
    out["video_ads_count"] = _as_int(out.get("video_ads_count"))
    out.pop("mk_video_metadata_json", None)
    out["mk_video_metadata"] = metadata
    out["local_cover_url"] = _local_media_url(out.get("local_cover_object_key"))
    out["cover_cached_at"] = _iso_datetime(out.get("cover_cached_at"))
    for key in ("created_at", "updated_at"):
        value = out.get(key)
        out[key] = _iso_datetime(value)
    return out


def _enrich_material_yesterday_delta(
    items: list[dict[str, Any]],
    *,
    snapshot_date: str,
    snapshot_at: str,
) -> list[dict[str, Any]]:
    if not items:
        return items

    resolved_snapshot_at = _coerce_datetime(snapshot_at)
    current_run = _snapshot_run_metadata(resolved_snapshot_at)
    previous_snapshot = (
        _previous_material_snapshot_for(
            snapshot_date=_coerce_date(snapshot_date),
            snapshot_at=resolved_snapshot_at,
            min_source_product_count=_as_int(current_run.get("source_product_count")),
            min_source_product_limit=_as_int(current_run.get("source_product_limit")),
        )
        if snapshot_date and resolved_snapshot_at
        else None
    )
    previous_by_key: dict[str, dict[str, Any]] = {}
    previous_product_codes: set[str] | None = None
    if previous_snapshot:
        keys = sorted(
            {str(item.get("material_key") or "") for item in items if item.get("material_key")}
        )
        if keys:
            placeholders = ",".join(["%s"] * len(keys))
            rows = query(
                f"""
                SELECT material_key, cumulative_90_spend, mk_video_metadata_json
                FROM mingkong_material_daily_snapshots
                WHERE snapshot_at = %s
                  AND material_key IN ({placeholders})
                """,
                tuple([previous_snapshot["snapshot_at"]] + keys),
            )
            previous_by_key = {
                str(row.get("material_key") or ""): row
                for row in rows or []
                if row.get("material_key")
            }
        product_codes = sorted(
            {_normalized_product_code(item.get("product_code")) for item in items if item.get("product_code")}
        )
        previous_product_codes = set()
        if product_codes:
            placeholders = ",".join(["%s"] * len(product_codes))
            rows = query(
                f"""
                SELECT DISTINCT product_code
                FROM mingkong_material_daily_snapshots
                WHERE snapshot_at = %s
                  AND product_code IN ({placeholders})
                """,
                tuple([previous_snapshot["snapshot_at"]] + product_codes),
            )
            previous_product_codes = {
                _normalized_product_code(row.get("product_code"))
                for row in rows or []
                if row.get("product_code")
            }

    for item in items:
        current_spend = _spend_from_row(item, "cumulative_90_spend", _metadata_for_row(item))
        previous_row = previous_by_key.get(str(item.get("material_key") or ""))
        previous_spend = (
            None
            if previous_row is None
            else _spend_from_row(previous_row, "cumulative_90_spend", _metadata_for_row(previous_row))
        )
        delta = _delta_from_previous_baseline(
            current_spend=current_spend,
            previous_spend=previous_spend,
            product_code=item.get("product_code"),
            previous_product_codes=previous_product_codes,
            has_previous_snapshot=bool(previous_snapshot),
        )
        item["current_cumulative_90_spend"] = current_spend
        item["previous_cumulative_90_spend"] = previous_spend
        item["yesterday_spend_delta"] = round(delta, 2)
        item["previous_snapshot_date"] = (
            _coerce_date(previous_snapshot.get("snapshot_date")) if previous_snapshot else ""
        )
        item["previous_snapshot_at"] = (
            _coerce_datetime(previous_snapshot.get("snapshot_at")) if previous_snapshot else ""
        )
        item["previous_snapshot_slot"] = (
            str(previous_snapshot.get("snapshot_slot") or "") if previous_snapshot else ""
        )
        item["comparison_interval_seconds"] = (
            _as_int(previous_snapshot.get("comparison_interval_seconds")) if previous_snapshot else None
        )
    return items


def _serialize_top100_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["snapshot_date"] = _coerce_date(out.get("snapshot_date"))
    out["snapshot_at"] = _coerce_datetime(out.get("snapshot_at"))
    out["snapshot_slot"] = str(out.get("snapshot_slot") or _snapshot_slot_for(out.get("snapshot_at")))
    out["previous_snapshot_date"] = _coerce_date(out.get("previous_snapshot_date"))
    out["previous_snapshot_at"] = _coerce_datetime(out.get("previous_snapshot_at"))
    out["previous_snapshot_slot"] = str(out.get("previous_snapshot_slot") or "")
    out["comparison_interval_seconds"] = (
        None if out.get("comparison_interval_seconds") is None else _as_int(out.get("comparison_interval_seconds"))
    )
    out["ranking_snapshot_date"] = _coerce_date(out.get("ranking_snapshot_date"))
    metadata = _metadata_for_row(out)
    current_spend = _spend_from_row(out, "current_cumulative_90_spend", metadata)
    out["current_cumulative_90_spend"] = current_spend
    out["video_spends"] = current_spend
    out["video_spends_text"] = _raw_spend_text(out, metadata)
    out["previous_cumulative_90_spend"] = (
        None
        if out.get("previous_cumulative_90_spend") is None
        else _as_float(out.get("previous_cumulative_90_spend"))
    )
    out["yesterday_spend_delta"] = _as_float(out.get("yesterday_spend_delta"))
    out["video_ads_count"] = _as_int(out.get("video_ads_count"))
    out["is_new_material"] = bool(out.get("is_new_material"))
    out["is_new_top100_entry"] = bool(out.get("is_new_top100_entry"))
    out.pop("mk_video_metadata_json", None)
    out["mk_video_metadata"] = metadata
    out["local_cover_url"] = _local_media_url(out.get("local_cover_object_key"))
    out["cover_cached_at"] = _iso_datetime(out.get("cover_cached_at"))
    value = out.get("created_at")
    out["created_at"] = _iso_datetime(value)
    return out


def upsert_snapshot_rows(
    *,
    run_id: int,
    snapshot_date: str,
    snapshot_at: str | None = None,
    snapshot_slot: str | None = None,
    ranking_snapshot_date: str,
    rows: list[dict[str, Any]],
) -> int:
    resolved_snapshot_at = _coerce_datetime(snapshot_at) if snapshot_at else f"{snapshot_date} 00:00:00"
    resolved_snapshot_slot = snapshot_slot or _snapshot_slot_for(resolved_snapshot_at)
    inserted = 0
    for row in rows:
        execute(
            """
            INSERT INTO mingkong_material_daily_snapshots
              (snapshot_date, ranking_snapshot_date, run_id, material_key,
               snapshot_at, snapshot_slot,
               product_code, rank_position, shopify_product_id, product_name, product_url,
               mk_product_id, mk_product_name, mk_product_link, main_image,
               video_name, video_path, video_image_path, local_cover_object_key,
               cover_cached_at, cover_cache_error, cumulative_90_spend, video_ads_count,
               video_author, video_upload_time, video_duration_seconds, mk_video_metadata_json)
            VALUES
              (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
               ranking_snapshot_date=VALUES(ranking_snapshot_date),
               run_id=VALUES(run_id),
               snapshot_date=VALUES(snapshot_date),
               snapshot_slot=VALUES(snapshot_slot),
               product_code=VALUES(product_code),
               rank_position=VALUES(rank_position),
               shopify_product_id=VALUES(shopify_product_id),
               product_name=VALUES(product_name),
               product_url=VALUES(product_url),
               mk_product_id=VALUES(mk_product_id),
               mk_product_name=VALUES(mk_product_name),
               mk_product_link=VALUES(mk_product_link),
               main_image=VALUES(main_image),
               video_name=VALUES(video_name),
               video_path=VALUES(video_path),
               video_image_path=VALUES(video_image_path),
               local_cover_object_key=VALUES(local_cover_object_key),
               cover_cached_at=VALUES(cover_cached_at),
               cover_cache_error=VALUES(cover_cache_error),
               cumulative_90_spend=VALUES(cumulative_90_spend),
               video_ads_count=VALUES(video_ads_count),
               video_author=VALUES(video_author),
               video_upload_time=VALUES(video_upload_time),
               video_duration_seconds=VALUES(video_duration_seconds),
               mk_video_metadata_json=VALUES(mk_video_metadata_json),
               updated_at=NOW()
            """,
            (
                snapshot_date,
                ranking_snapshot_date,
                int(run_id),
                row.get("material_key"),
                resolved_snapshot_at,
                resolved_snapshot_slot,
                row.get("product_code") or "",
                row.get("rank_position"),
                row.get("shopify_product_id") or None,
                row.get("product_name") or None,
                row.get("product_url") or None,
                row.get("mk_product_id"),
                row.get("mk_product_name") or None,
                row.get("mk_product_link") or None,
                row.get("main_image") or None,
                row.get("video_name") or None,
                row.get("video_path") or "",
                row.get("video_image_path") or None,
                row.get("local_cover_object_key") or None,
                row.get("cover_cached_at") or None,
                row.get("cover_cache_error") or None,
                _spend_from_row(row, "cumulative_90_spend"),
                _as_int(row.get("video_ads_count")),
                row.get("video_author") or None,
                row.get("video_upload_time") or None,
                row.get("video_duration_seconds"),
                _json_dumps(_metadata_for_write(row)),
            ),
        )
        inserted += 1
    return inserted


def list_material_library(
    *,
    snapshot_date: str | None = None,
    snapshot_at: str | None = None,
    range_key: str | None = None,
    keyword: str = "",
    page: int | str | None = 1,
    page_size: int | str | None = 100,
) -> dict[str, Any]:
    guard_against_windows_local_mysql()
    range_bounds = _material_range_bounds(range_key)
    identity = (
        {"snapshot_date": "", "snapshot_at": "", "snapshot_slot": ""}
        if range_bounds
        else _material_snapshot_identity(
            snapshot_date=_coerce_date(snapshot_date) if snapshot_date else None,
            snapshot_at=snapshot_at,
        )
    )
    snapshot = identity["snapshot_date"]
    selected_snapshot_at = identity["snapshot_at"]
    if not range_bounds and not snapshot:
        return {"items": [], "snapshot": "", "snapshot_at": "", "total": 0, "run_summary": None}
    page_num, size, offset = _page_bounds(page, page_size)
    kw = str(keyword or "").strip()

    if range_bounds:
        range_start, range_end = range_bounds
        where = ["r.status = 'success'", "s.snapshot_date BETWEEN %s AND %s"]
        args: list[Any] = [range_start, range_end]
        keyword_sql, keyword_args = _material_keyword_condition("s", kw)
        if keyword_sql:
            where.append(keyword_sql)
            args.extend(keyword_args)
        where_sql = " AND ".join(where)
        count_row = query_one(
            f"""
            SELECT COUNT(*) AS cnt
            FROM (
              SELECT s.material_key
              FROM mingkong_material_daily_snapshots s
              JOIN mingkong_material_sync_runs r ON r.id = s.run_id
              WHERE {where_sql}
              GROUP BY s.material_key
            ) deduped
            """,
            tuple(args),
        ) or {}
        rows = query(
            f"""
            SELECT s.*, COALESCE(mp.total_90_spend, pt.product_total_90_spend, s.cumulative_90_spend)
                     AS product_total_90_spend
            FROM mingkong_material_daily_snapshots s
            JOIN (
              SELECT s.material_key, MAX(s.snapshot_at) AS latest_snapshot_at
              FROM mingkong_material_daily_snapshots s
              JOIN mingkong_material_sync_runs r ON r.id = s.run_id
              WHERE {where_sql}
              GROUP BY s.material_key
            ) latest ON latest.material_key = s.material_key AND latest.latest_snapshot_at = s.snapshot_at
            LEFT JOIN mingkong_material_products mp
              ON mp.run_id = s.run_id AND mp.product_code = s.product_code
            LEFT JOIN (
              SELECT s2.snapshot_at, s2.product_code, SUM(s2.cumulative_90_spend) AS product_total_90_spend
              FROM mingkong_material_daily_snapshots s2
              JOIN mingkong_material_sync_runs r2 ON r2.id = s2.run_id
              WHERE r2.status = 'success' AND s2.snapshot_date BETWEEN %s AND %s
              GROUP BY s2.snapshot_at, s2.product_code
            ) pt ON pt.snapshot_at = s.snapshot_at AND pt.product_code = s.product_code
            ORDER BY s.cumulative_90_spend DESC, s.video_ads_count DESC, s.id ASC
            LIMIT %s OFFSET %s
            """,
            tuple(args + [range_start, range_end, size, offset]),
        )
        serialized = [_serialize_material_row(row) for row in rows or []]
        items = _enrich_cached_ad_statuses(
            _enrich_material_yesterday_delta(
                serialized,
                snapshot_date=range_end,
                snapshot_at=None,
            )
        )
        return {
            "items": items,
            "snapshot": "",
            "snapshot_at": "",
            "snapshot_slot": "",
            "range": str(range_key or "").strip().lower().replace("-", "_"),
            "range_start": range_start,
            "range_end": range_end,
            "total": _as_int(count_row.get("cnt")),
            "page": page_num,
            "page_size": size,
            "run_summary": None,
        }

    if selected_snapshot_at:
        where = ["s.snapshot_at = %s"]
        args = [selected_snapshot_at]
    else:
        where = ["s.snapshot_date = %s"]
        args = [snapshot]
    keyword_sql, keyword_args = _material_keyword_condition("s", kw)
    if keyword_sql:
        where.append(keyword_sql)
        args.extend(keyword_args)
    where_sql = " AND ".join(where)
    count_row = query_one(
        f"SELECT COUNT(*) AS cnt FROM mingkong_material_daily_snapshots s WHERE {where_sql}",
        tuple(args),
    ) or {}
    rows = query(
        f"""
        SELECT s.*, COALESCE(mp.total_90_spend, pt.product_total_90_spend, s.cumulative_90_spend)
                 AS product_total_90_spend
        FROM mingkong_material_daily_snapshots s
        LEFT JOIN mingkong_material_products mp
          ON mp.run_id = s.run_id AND mp.product_code = s.product_code
        LEFT JOIN (
          SELECT snapshot_at, product_code, SUM(cumulative_90_spend) AS product_total_90_spend
          FROM mingkong_material_daily_snapshots
          WHERE snapshot_at = %s
          GROUP BY snapshot_at, product_code
        ) pt ON pt.snapshot_at = s.snapshot_at AND pt.product_code = s.product_code
        WHERE {where_sql}
        ORDER BY s.cumulative_90_spend DESC, s.video_ads_count DESC, s.id ASC
        LIMIT %s OFFSET %s
        """,
        tuple([selected_snapshot_at] + args + [size, offset]),
    )
    serialized = [_serialize_material_row(row) for row in rows or []]
    items = _enrich_cached_ad_statuses(
        _enrich_material_yesterday_delta(
            serialized,
            snapshot_date=snapshot,
            snapshot_at=selected_snapshot_at,
        )
    )
    return {
        "items": items,
        "snapshot": snapshot,
        "snapshot_at": selected_snapshot_at,
        "snapshot_slot": identity.get("snapshot_slot") or "",
        "total": _as_int(count_row.get("cnt")),
        "page": page_num,
        "page_size": size,
        "run_summary": _run_summary(snapshot, selected_snapshot_at),
    }


def get_material_detail(material_key: str) -> dict[str, Any] | None:
    guard_against_windows_local_mysql()
    key = str(material_key or "").strip().lower()
    if not key:
        return None
    latest = query_one(
        """
        SELECT s.*, COALESCE(mp.total_90_spend, s.cumulative_90_spend)
                 AS product_total_90_spend
        FROM mingkong_material_daily_snapshots s
        LEFT JOIN mingkong_material_products mp
          ON mp.run_id = s.run_id AND mp.product_code = s.product_code
        WHERE s.material_key = %s
        ORDER BY s.snapshot_at DESC, s.id DESC
        LIMIT 1
        """,
        (key,),
    )
    if not latest:
        return None
    rows = query(
        """
        SELECT s.*
        FROM mingkong_material_daily_snapshots s
        WHERE s.material_key = %s
        ORDER BY snapshot_at ASC, id ASC
        """,
        (key,),
    )
    history: list[dict[str, Any]] = []
    previous_spend: float | None = None
    for raw in rows or []:
        item = _serialize_material_row(raw)
        spend = _as_float(item.get("cumulative_90_spend"))
        item["previous_cumulative_90_spend"] = previous_spend
        item["spend_delta"] = 0.0 if previous_spend is None else max(0.0, spend - previous_spend)
        history.append(item)
        previous_spend = spend
    spends = [_as_float(item.get("cumulative_90_spend")) for item in history]
    summary = {
        "history_count": len(history),
        "first_snapshot_at": history[0]["snapshot_at"] if history else "",
        "latest_snapshot_at": history[-1]["snapshot_at"] if history else "",
        "min_cumulative_90_spend": min(spends) if spends else 0.0,
        "max_cumulative_90_spend": max(spends) if spends else 0.0,
    }
    return {
        "material": _serialize_material_row(latest),
        "history": history,
        "summary": summary,
    }


def list_yesterday_top100(
    *,
    snapshot_date: str | None = None,
    snapshot_at: str | None = None,
    keyword: str = "",
    page: int | str | None = 1,
    page_size: int | str | None = 100,
    sort_order: str = "new_entry_first",
) -> dict[str, Any]:
    guard_against_windows_local_mysql()
    identity = _latest_snapshot_identity(
        "mingkong_material_daily_top100",
        snapshot_date=_coerce_date(snapshot_date) if snapshot_date else None,
        snapshot_at=snapshot_at,
    )
    snapshot = identity["snapshot_date"]
    selected_snapshot_at = identity["snapshot_at"]
    if not snapshot:
        return {
            "items": [],
            "snapshot": "",
            "snapshot_at": "",
            "previous_snapshot": "",
            "previous_snapshot_at": "",
            "total": 0,
            "run_summary": None,
        }
    if selected_snapshot_at:
        where = ["t.snapshot_at = %s"]
        base_args: list[Any] = [selected_snapshot_at]
    else:
        where = ["t.snapshot_date = %s"]
        base_args = [snapshot]
    keyword_sql, keyword_args = _material_keyword_condition("t", keyword)
    if keyword_sql:
        where.append(keyword_sql)
        base_args.extend(keyword_args)
    where_sql = " AND ".join(where)
    page_num, size, offset = _page_bounds(page, page_size)
    count_row = query_one(
        f"SELECT COUNT(*) AS cnt FROM mingkong_material_daily_top100 t WHERE {where_sql}",
        tuple(base_args),
    ) or {}

    order_clause = "is_new_top100_entry DESC, yesterday_spend_delta DESC"
    if sort_order == "normal":
        order_clause = "yesterday_spend_delta DESC"

    rows = query(
        f"""
        SELECT t.*
        FROM mingkong_material_daily_top100 t
        WHERE {where_sql}
        ORDER BY {order_clause},
                 current_cumulative_90_spend DESC, video_ads_count DESC,
                 rank_position ASC, id ASC
        LIMIT %s OFFSET %s
        """,
        tuple(base_args + [size, offset]),
    )
    items = _enrich_cached_ad_statuses([_serialize_top100_row(row) for row in rows or []])
    previous_snapshot = items[0].get("previous_snapshot_date") if items else ""
    previous_snapshot_at = items[0].get("previous_snapshot_at") if items else ""
    return {
        "items": items,
        "snapshot": snapshot,
        "snapshot_at": selected_snapshot_at,
        "snapshot_slot": identity.get("snapshot_slot") or "",
        "previous_snapshot": previous_snapshot or "",
        "previous_snapshot_at": previous_snapshot_at or "",
        "total": _as_int(count_row.get("cnt")),
        "page": page_num,
        "page_size": size,
        "run_summary": _run_summary(snapshot, selected_snapshot_at),
    }


def create_or_reuse_run(
    *,
    snapshot_date: str,
    snapshot_at: str,
    snapshot_slot: str,
    ranking_snapshot_date: str,
    source_product_count: int,
    source_product_limit: int,
) -> dict[str, Any]:
    existing = query_one(
        """
        SELECT * FROM mingkong_material_sync_runs
        WHERE snapshot_date = %s AND snapshot_slot = %s
        LIMIT 1
        """,
        (snapshot_date, snapshot_slot),
    )
    if existing:
        if str(existing.get("status") or "") != "success":
            execute(
                """
                UPDATE mingkong_material_sync_runs
                SET status='running', snapshot_at=%s, ranking_snapshot_date=%s, source_product_count=%s,
                    source_product_limit=%s, processed_product_count=0,
                    material_count=0, failed_product_count=0, error_message=NULL,
                    summary_json=NULL, started_at=NOW(), finished_at=NULL
                WHERE id=%s
                """,
                (
                    snapshot_at,
                    ranking_snapshot_date,
                    int(source_product_count),
                    int(source_product_limit),
                    int(existing["id"]),
                ),
            )
            existing = dict(existing)
            existing.update(
                {
                    "status": "running",
                    "snapshot_at": snapshot_at,
                    "snapshot_slot": snapshot_slot,
                    "ranking_snapshot_date": ranking_snapshot_date,
                    "source_product_count": source_product_count,
                    "source_product_limit": source_product_limit,
                }
            )
        return dict(existing)
    run_id = execute(
        """
        INSERT INTO mingkong_material_sync_runs
          (snapshot_date, snapshot_at, snapshot_slot, ranking_snapshot_date, status, source_product_limit,
           source_product_count)
        VALUES (%s,%s,%s,%s,'running',%s,%s)
        """,
        (
            snapshot_date,
            snapshot_at,
            snapshot_slot,
            ranking_snapshot_date,
            int(source_product_limit),
            int(source_product_count),
        ),
    )
    return {
        "id": run_id,
        "snapshot_date": snapshot_date,
        "snapshot_at": snapshot_at,
        "snapshot_slot": snapshot_slot,
        "ranking_snapshot_date": ranking_snapshot_date,
        "status": "running",
    }


def record_product_status(
    *,
    run_id: int,
    snapshot_date: str,
    snapshot_at: str,
    snapshot_slot: str,
    ranking_snapshot_date: str,
    source_product: dict[str, Any],
    status: str,
    material_count: int = 0,
    video_count: int = 0,
    path_video_count: int = 0,
    total_90_spend: float = 0.0,
    total_ads: int = 0,
    mk_product: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    product_code = str(source_product.get("product_code") or "")
    links = (mk_product or {}).get("product_links") or []
    execute(
        """
        INSERT INTO mingkong_material_products
          (run_id, snapshot_date, snapshot_at, snapshot_slot, ranking_snapshot_date, rank_position, product_code,
           shopify_product_id, product_name, product_url, store, sales_count, order_count,
           revenue_main, mk_product_id, mk_product_name, mk_product_link, status,
           material_count, video_count, path_video_count, total_90_spend, total_ads,
           error_message, processed_at)
        VALUES
          (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON DUPLICATE KEY UPDATE
           rank_position=VALUES(rank_position),
           snapshot_at=VALUES(snapshot_at),
           snapshot_slot=VALUES(snapshot_slot),
           shopify_product_id=VALUES(shopify_product_id),
           product_name=VALUES(product_name),
           product_url=VALUES(product_url),
           store=VALUES(store),
           sales_count=VALUES(sales_count),
           order_count=VALUES(order_count),
           revenue_main=VALUES(revenue_main),
           mk_product_id=VALUES(mk_product_id),
           mk_product_name=VALUES(mk_product_name),
           mk_product_link=VALUES(mk_product_link),
           status=VALUES(status),
           material_count=VALUES(material_count),
           video_count=VALUES(video_count),
           path_video_count=VALUES(path_video_count),
           total_90_spend=VALUES(total_90_spend),
           total_ads=VALUES(total_ads),
           error_message=VALUES(error_message),
           processed_at=NOW()
        """,
        (
            int(run_id),
            snapshot_date,
            snapshot_at,
            snapshot_slot,
            ranking_snapshot_date,
            source_product.get("rank_position"),
            product_code,
            source_product.get("shopify_product_id") or None,
            source_product.get("product_name") or None,
            source_product.get("product_url") or None,
            source_product.get("store") or None,
            source_product.get("sales_count"),
            source_product.get("order_count"),
            source_product.get("revenue_main") or None,
            (mk_product or {}).get("id"),
            (mk_product or {}).get("product_name") or None,
            str(links[0]) if links else None,
            status,
            int(material_count),
            int(video_count),
            int(path_video_count),
            float(total_90_spend or 0.0),
            int(total_ads),
            error_message,
        ),
    )


def _previous_material_snapshot(snapshot_date: str) -> str:
    selected = _previous_material_snapshot_for(snapshot_date=snapshot_date, snapshot_at=f"{snapshot_date} 00:00:00")
    return selected["snapshot_date"] if selected else ""


def _snapshot_run_metadata(snapshot_at: str | None) -> dict[str, Any]:
    resolved = _coerce_datetime(snapshot_at)
    if not resolved:
        return {}
    return query_one(
        """
        SELECT snapshot_at, snapshot_slot, ranking_snapshot_date, status,
               source_product_count, source_product_limit
        FROM mingkong_material_sync_runs
        WHERE snapshot_at = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (resolved,),
    ) or {}


def _filter_compatible_snapshot_candidates(
    rows: list[dict[str, Any]],
    *,
    min_source_product_count: int | None = None,
    min_source_product_limit: int | None = None,
) -> list[dict[str, Any]]:
    min_count = _as_int(min_source_product_count)
    min_limit = _as_int(min_source_product_limit)
    if min_count <= 0 and min_limit <= 0:
        return rows
    compatible = []
    for row in rows:
        count_ok = min_count <= 0 or _as_int(row.get("source_product_count")) >= min_count
        limit_ok = min_limit <= 0 or _as_int(row.get("source_product_limit")) >= min_limit
        if count_ok and limit_ok:
            compatible.append(row)
    return compatible or rows


def _previous_material_snapshot_for(
    *,
    snapshot_date: str,
    snapshot_at: str,
    min_source_product_count: int | None = None,
    min_source_product_limit: int | None = None,
) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT s.snapshot_date, s.snapshot_at, s.snapshot_slot,
               r.source_product_count, r.source_product_limit, r.ranking_snapshot_date
        FROM (
            SELECT snapshot_date, snapshot_at, snapshot_slot
            FROM mingkong_material_daily_snapshots
            WHERE snapshot_date < %s
              AND snapshot_at < %s
            GROUP BY snapshot_date, snapshot_at, snapshot_slot
        ) s
        JOIN mingkong_material_sync_runs r
          ON r.snapshot_at = s.snapshot_at AND r.status = 'success'
        ORDER BY s.snapshot_at DESC
        LIMIT 14
        """,
        (snapshot_date, snapshot_at),
    )
    if rows:
        candidates = _filter_compatible_snapshot_candidates(
            rows,
            min_source_product_count=min_source_product_count,
            min_source_product_limit=min_source_product_limit,
        )
        return choose_previous_snapshot_for_24h(snapshot_at, candidates)

    fallback_rows = query(
        """
        SELECT snapshot_date, snapshot_at, snapshot_slot
        FROM mingkong_material_daily_snapshots
        WHERE snapshot_date < %s
          AND snapshot_at < %s
        GROUP BY snapshot_date, snapshot_at, snapshot_slot
        ORDER BY snapshot_at DESC
        LIMIT 14
        """,
        (snapshot_date, snapshot_at),
    )
    return choose_previous_snapshot_for_24h(snapshot_at, fallback_rows or [])


def _snapshot_rows_by_date(snapshot_date: str, snapshot_at: str | None = None) -> list[dict[str, Any]]:
    if snapshot_at:
        return query(
            "SELECT * FROM mingkong_material_daily_snapshots WHERE snapshot_at = %s",
            (_coerce_datetime(snapshot_at),),
        )
    return query(
        "SELECT * FROM mingkong_material_daily_snapshots WHERE snapshot_date = %s",
        (snapshot_date,),
    )


def _previous_top100_keys(snapshot_date: str, snapshot_at: str | None = None) -> set[str]:
    if not snapshot_date and not snapshot_at:
        return set()
    if snapshot_at:
        rows = query(
            "SELECT material_key FROM mingkong_material_daily_top100 WHERE snapshot_at = %s",
            (_coerce_datetime(snapshot_at),),
        )
        return {str(row.get("material_key") or "") for row in rows if row.get("material_key")}
    rows = query(
        "SELECT material_key FROM mingkong_material_daily_top100 WHERE snapshot_date = %s",
        (snapshot_date,),
    )
    return {str(row.get("material_key") or "") for row in rows if row.get("material_key")}


def _replace_top100_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    snapshot_date = rows[0]["snapshot_date"]
    snapshot_at = _coerce_datetime(rows[0].get("snapshot_at"))
    if snapshot_at:
        execute("DELETE FROM mingkong_material_daily_top100 WHERE snapshot_at = %s", (snapshot_at,))
    else:
        execute("DELETE FROM mingkong_material_daily_top100 WHERE snapshot_date = %s", (snapshot_date,))
    inserted = 0
    for row in rows:
        execute(
            """
            INSERT INTO mingkong_material_daily_top100
              (snapshot_date, snapshot_at, snapshot_slot, previous_snapshot_date,
               previous_snapshot_at, previous_snapshot_slot, comparison_interval_seconds,
               ranking_snapshot_date, rank_position, display_position, material_key,
               product_code, source_product_rank_position,
               shopify_product_id, product_name, product_url, mk_product_id, mk_product_name,
               mk_product_link, main_image, video_name, video_path, video_image_path,
               local_cover_object_key, cover_cached_at, cover_cache_error,
               previous_cumulative_90_spend, current_cumulative_90_spend,
               yesterday_spend_delta, video_ads_count, video_author, video_upload_time,
               video_duration_seconds, mk_video_metadata_json, is_new_material,
               is_new_top100_entry)
            VALUES
              (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
               %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
               snapshot_date=VALUES(snapshot_date),
               snapshot_slot=VALUES(snapshot_slot),
               previous_snapshot_date=VALUES(previous_snapshot_date),
               previous_snapshot_at=VALUES(previous_snapshot_at),
               previous_snapshot_slot=VALUES(previous_snapshot_slot),
               comparison_interval_seconds=VALUES(comparison_interval_seconds),
               ranking_snapshot_date=VALUES(ranking_snapshot_date),
               rank_position=VALUES(rank_position),
               display_position=VALUES(display_position),
               source_product_rank_position=VALUES(source_product_rank_position),
               local_cover_object_key=VALUES(local_cover_object_key),
               cover_cached_at=VALUES(cover_cached_at),
               cover_cache_error=VALUES(cover_cache_error),
               previous_cumulative_90_spend=VALUES(previous_cumulative_90_spend),
               current_cumulative_90_spend=VALUES(current_cumulative_90_spend),
               yesterday_spend_delta=VALUES(yesterday_spend_delta),
               is_new_material=VALUES(is_new_material),
               is_new_top100_entry=VALUES(is_new_top100_entry)
            """,
            (
                row.get("snapshot_date"),
                _coerce_datetime(row.get("snapshot_at")) or None,
                row.get("snapshot_slot") or None,
                row.get("previous_snapshot_date"),
                _coerce_datetime(row.get("previous_snapshot_at")) or None,
                row.get("previous_snapshot_slot") or None,
                row.get("comparison_interval_seconds"),
                row.get("ranking_snapshot_date"),
                row.get("rank_position"),
                row.get("display_position"),
                row.get("material_key"),
                row.get("product_code") or "",
                row.get("source_product_rank_position"),
                row.get("shopify_product_id") or None,
                row.get("product_name") or None,
                row.get("product_url") or None,
                row.get("mk_product_id"),
                row.get("mk_product_name") or None,
                row.get("mk_product_link") or None,
                row.get("main_image") or None,
                row.get("video_name") or None,
                row.get("video_path") or "",
                row.get("video_image_path") or None,
                row.get("local_cover_object_key") or None,
                row.get("cover_cached_at") or None,
                row.get("cover_cache_error") or None,
                row.get("previous_cumulative_90_spend"),
                _spend_from_row(row, "current_cumulative_90_spend"),
                _as_float(row.get("yesterday_spend_delta")),
                _as_int(row.get("video_ads_count")),
                row.get("video_author") or None,
                row.get("video_upload_time") or None,
                row.get("video_duration_seconds"),
                _json_dumps(_metadata_for_write(row)),
                1 if row.get("is_new_material") else 0,
                1 if row.get("is_new_top100_entry") else 0,
            ),
        )
        inserted += 1
    return inserted


def generate_daily_top100(snapshot_date: str, snapshot_at: str | None = None) -> dict[str, Any]:
    if snapshot_at:
        identity = {
            "snapshot_date": _coerce_date(snapshot_date),
            "snapshot_at": _coerce_datetime(snapshot_at),
            "snapshot_slot": _snapshot_slot_for(snapshot_at),
        }
    else:
        identity = _latest_snapshot_identity(
            "mingkong_material_daily_snapshots",
            snapshot_date=snapshot_date,
        )
    selected_snapshot_at = identity.get("snapshot_at") or ""
    current_run = _snapshot_run_metadata(selected_snapshot_at)
    previous_snapshot = _previous_material_snapshot_for(
        snapshot_date=_coerce_date(snapshot_date),
        snapshot_at=selected_snapshot_at or f"{snapshot_date} 00:00:00",
        min_source_product_count=_as_int(current_run.get("source_product_count")),
        min_source_product_limit=_as_int(current_run.get("source_product_limit")),
    )
    previous_snapshot_date = previous_snapshot["snapshot_date"] if previous_snapshot else ""
    previous_snapshot_at = previous_snapshot["snapshot_at"] if previous_snapshot else ""
    current_rows = _snapshot_rows_by_date(snapshot_date, selected_snapshot_at or None)
    previous_rows = (
        _snapshot_rows_by_date(previous_snapshot_date, previous_snapshot_at)
        if previous_snapshot_date and previous_snapshot_at
        else []
    )
    previous_by_key = {
        str(row.get("material_key") or ""): row
        for row in previous_rows
        if row.get("material_key")
    }
    previous_product_codes = {
        _normalized_product_code(row.get("product_code"))
        for row in previous_rows
        if row.get("product_code")
    }
    top100_rows = build_top100_rows(
        snapshot_date=snapshot_date,
        snapshot_at=selected_snapshot_at,
        previous_snapshot_date=previous_snapshot_date or None,
        previous_snapshot_at=previous_snapshot_at or None,
        previous_snapshot_slot=(previous_snapshot or {}).get("snapshot_slot") if previous_snapshot else None,
        comparison_interval_seconds=(
            (previous_snapshot or {}).get("comparison_interval_seconds") if previous_snapshot else None
        ),
        current_rows=current_rows,
        previous_by_key=previous_by_key,
        previous_top100_keys=_previous_top100_keys(previous_snapshot_date, previous_snapshot_at or None),
        previous_product_codes=previous_product_codes if previous_snapshot else None,
        limit=YESTERDAY_SPEND_TOP_LIMIT,
    )
    inserted = _replace_top100_rows(top100_rows)
    return {
        "snapshot_date": snapshot_date,
        "snapshot_at": selected_snapshot_at,
        "previous_snapshot_date": previous_snapshot_date,
        "previous_snapshot_at": previous_snapshot_at,
        "comparison_interval_seconds": (
            (previous_snapshot or {}).get("comparison_interval_seconds") if previous_snapshot else None
        ),
        "top100_count": inserted,
        "top300_count": inserted,
    }


def _mk_headers() -> dict[str, str]:
    headers = pushes.build_localized_texts_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        raise RuntimeError("Mingkong credentials missing")
    return headers


def _mk_base_url() -> str:
    return (pushes.get_localized_texts_base_url() or "https://os.wedev.vip").rstrip("/")


def _is_mingkong_login_expired(data: dict[str, Any]) -> bool:
    return data.get("is_guest") is True or str(data.get("message") or "").startswith("登录")


def _refresh_mingkong_headers_after_login(product_code: str, timeout_seconds: int) -> dict[str, str]:
    result = mingkong_login_autofill.refresh_wedev_credentials_via_cdp(
        base_url=_mk_base_url(),
        verify_product_code=product_code or "__credential_probe__",
        timeout_seconds=timeout_seconds,
    )
    if str(result.get("status") or "") != "success":
        raise RuntimeError(f"Mingkong auto login failed: {result.get('error') or result.get('status')}")
    refreshed = _mk_headers()
    refreshed.pop("Content-Type", None)
    return refreshed


def _search_mingkong_items(
    session: requests.Session,
    *,
    base_url: str,
    headers: dict[str, str],
    product_code: str,
    timeout_seconds: int,
    allow_login_refresh: bool = True,
) -> list[dict[str, Any]]:
    resp = session.get(
        f"{base_url}/api/marketing/medias",
        params={"page": 1, "q": product_code, "source": "", "level": "", "show_attention": 0},
        headers=headers,
        timeout=timeout_seconds,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    if _is_mingkong_login_expired(data):
        if allow_login_refresh:
            refreshed = _refresh_mingkong_headers_after_login(product_code, timeout_seconds)
            headers.clear()
            headers.update(refreshed)
            return _search_mingkong_items(
                session,
                base_url=base_url,
                headers=headers,
                product_code=product_code,
                timeout_seconds=timeout_seconds,
                allow_login_refresh=False,
            )
        raise RuntimeError("Mingkong credentials expired")
    return [item for item in ((data.get("data") or {}).get("items") or []) if isinstance(item, dict)]


def _fetch_mingkong_product_detail(
    session: requests.Session,
    *,
    base_url: str,
    headers: dict[str, str],
    mk_product: dict[str, Any],
    timeout_seconds: int,
    allow_login_refresh: bool = True,
) -> dict[str, Any]:
    mk_product_id = _as_int(mk_product.get("id"))
    if mk_product_id <= 0:
        return mk_product
    resp = session.get(
        f"{base_url}/api/marketing/medias/{mk_product_id}",
        headers=headers,
        timeout=timeout_seconds,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    if _is_mingkong_login_expired(data):
        if allow_login_refresh:
            product_code = str(mk_product.get("product_code") or mk_product.get("code") or "").strip()
            refreshed = _refresh_mingkong_headers_after_login(product_code, timeout_seconds)
            headers.clear()
            headers.update(refreshed)
            return _fetch_mingkong_product_detail(
                session,
                base_url=base_url,
                headers=headers,
                mk_product=mk_product,
                timeout_seconds=timeout_seconds,
                allow_login_refresh=False,
            )
        raise RuntimeError("Mingkong credentials expired")
    item = ((data.get("data") or {}).get("item") or {})
    if not isinstance(item, dict) or not item:
        return mk_product
    merged = dict(mk_product)
    merged.update(item)
    if not merged.get("product_links") and mk_product.get("product_links"):
        merged["product_links"] = mk_product.get("product_links")
    return merged


def _visible_video_stats(item: dict[str, Any]) -> tuple[int, float, int]:
    stats = product_video_aggregate_stats(item)
    return (
        int(stats["video_count"]),
        float(stats["total_90_spend"]),
        int(stats["total_ads"]),
    )


def _mingkong_result_product_codes(item: dict[str, Any]) -> set[str]:
    codes = {
        str(item.get(key) or "").strip().lower()
        for key in ("product_code", "code", "handle")
        if str(item.get(key) or "").strip()
    }
    for link in item.get("product_links") or []:
        code = _raw_product_handle(link)
        if code:
            codes.add(code)
    return codes


def _select_mingkong_product(items: list[dict[str, Any]], product_code: str) -> dict[str, Any] | None:
    target_code = str(product_code or "").strip().lower()
    if not target_code:
        return None
    best: tuple[tuple[float, int, int, int], dict[str, Any]] | None = None
    for item in items:
        if target_code not in _mingkong_result_product_codes(item):
            continue
        video_count, spend, ads = _visible_video_stats(item)
        if video_count <= 0:
            continue
        score = (spend, ads, video_count, _as_int(item.get("id")))
        if best is None or score > best[0]:
            best = (score, item)
    return best[1] if best else None


def run_daily_snapshot(
    *,
    source_limit: int = 500,
    batch_size: int = 10,
    sleep_after_products: int = 2,
    sleep_seconds: float = 30,
    timeout_seconds: int = 20,
    snapshot_date: str | None = None,
    snapshot_at: str | None = None,
) -> dict[str, Any]:
    guard_against_windows_local_mysql()
    scheduled_run_id = scheduled_tasks.start_run("mingkong_material_daily_snapshot")
    target_snapshot, target_snapshot_at, target_snapshot_slot = _snapshot_identity(
        snapshot_date=snapshot_date,
        snapshot_at=snapshot_at,
    )
    run_id: int | None = None
    processed = 0
    failed = 0
    material_count = 0
    try:
        ranking_snapshot, products = latest_top_products(limit=source_limit)
        run_row = create_or_reuse_run(
            snapshot_date=target_snapshot,
            snapshot_at=target_snapshot_at,
            snapshot_slot=target_snapshot_slot,
            ranking_snapshot_date=ranking_snapshot,
            source_product_count=len(products),
            source_product_limit=source_limit,
        )
        if str(run_row.get("status") or "") == "success":
            summary = {
                "snapshot_date": target_snapshot,
                "snapshot_at": target_snapshot_at,
                "snapshot_slot": target_snapshot_slot,
                "ranking_snapshot_date": ranking_snapshot,
                "skipped": True,
                "reason": "snapshot already completed",
            }
            scheduled_tasks.finish_run(scheduled_run_id, status="success", summary=summary)
            return summary

        run_id = int(run_row["id"])
        headers = _mk_headers()
        base_url = _mk_base_url()
        session = requests.Session()
        consecutive_failures = 0
        for index, product in enumerate(products, start=1):
            try:
                items = _search_mingkong_items(
                    session,
                    base_url=base_url,
                    headers=headers,
                    product_code=str(product["product_code"]),
                    timeout_seconds=timeout_seconds,
                )
                mk_product = _select_mingkong_product(items, str(product["product_code"]))
                if not mk_product:
                    record_product_status(
                        run_id=run_id,
                        snapshot_date=target_snapshot,
                        snapshot_at=target_snapshot_at,
                        snapshot_slot=target_snapshot_slot,
                        ranking_snapshot_date=ranking_snapshot,
                        source_product=product,
                        status="no_match",
                    )
                    processed += 1
                    consecutive_failures = 0
                else:
                    mk_product = _fetch_mingkong_product_detail(
                        session,
                        base_url=base_url,
                        headers=headers,
                        mk_product=mk_product,
                        timeout_seconds=timeout_seconds,
                    )
                    rows = flatten_materials_for_product(
                        source_product=product,
                        mk_product=mk_product,
                    )
                    product_stats = product_video_aggregate_stats(mk_product)
                    rows = [
                        cache_local_cover_for_material(
                            row,
                            session=session,
                            base_url=base_url,
                            headers=headers,
                            timeout_seconds=timeout_seconds,
                        )
                        for row in rows
                    ]
                    material_count += upsert_snapshot_rows(
                        run_id=run_id,
                        snapshot_date=target_snapshot,
                        snapshot_at=target_snapshot_at,
                        snapshot_slot=target_snapshot_slot,
                        ranking_snapshot_date=ranking_snapshot,
                        rows=rows,
                    )
                    record_product_status(
                        run_id=run_id,
                        snapshot_date=target_snapshot,
                        snapshot_at=target_snapshot_at,
                        snapshot_slot=target_snapshot_slot,
                        ranking_snapshot_date=ranking_snapshot,
                        source_product=product,
                        status="success",
                        material_count=len(rows),
                        video_count=int(product_stats["video_count"]),
                        path_video_count=len(rows),
                        total_90_spend=float(product_stats["total_90_spend"]),
                        total_ads=int(product_stats["total_ads"]),
                        mk_product=mk_product,
                    )
                    processed += 1
                    consecutive_failures = 0
            except Exception as exc:
                failed += 1
                consecutive_failures += 1
                record_product_status(
                    run_id=run_id,
                    snapshot_date=target_snapshot,
                    snapshot_at=target_snapshot_at,
                    snapshot_slot=target_snapshot_slot,
                    ranking_snapshot_date=ranking_snapshot,
                    source_product=product,
                    status="failed",
                    error_message=str(exc)[:1000],
                )
                if consecutive_failures >= 50:
                    raise RuntimeError("too many consecutive Mingkong product failures") from exc
            if sleep_seconds and index < len(products) and index % max(1, int(sleep_after_products)) == 0:
                time.sleep(float(sleep_seconds))
            if batch_size and index % max(1, int(batch_size)) == 0:
                execute(
                    """
                    UPDATE mingkong_material_sync_runs
                    SET processed_product_count=%s, material_count=%s,
                        failed_product_count=%s, summary_json=%s
                    WHERE id=%s
                    """,
                    (
                        processed,
                        material_count,
                        failed,
                        _json_dumps({"last_batch_product_index": index}),
                        run_id,
                    ),
                )

        top100 = generate_daily_top100(target_snapshot, target_snapshot_at)
        summary = {
            "snapshot_date": target_snapshot,
            "snapshot_at": target_snapshot_at,
            "snapshot_slot": target_snapshot_slot,
            "ranking_snapshot_date": ranking_snapshot,
            "source_product_count": len(products),
            "processed_product_count": processed,
            "material_count": material_count,
            "failed_product_count": failed,
            "top100": top100,
        }
        execute(
            """
            UPDATE mingkong_material_sync_runs
            SET status='success', processed_product_count=%s, material_count=%s,
                failed_product_count=%s, summary_json=%s, finished_at=NOW()
            WHERE id=%s
            """,
            (processed, material_count, failed, _json_dumps(summary), run_id),
        )
        scheduled_tasks.finish_run(scheduled_run_id, status="success", summary=summary)
        return summary
    except Exception as exc:
        if run_id is not None:
            execute(
                """
                UPDATE mingkong_material_sync_runs
                SET status='failed', processed_product_count=%s, material_count=%s,
                    failed_product_count=%s, error_message=%s, finished_at=NOW()
                WHERE id=%s
                """,
                (processed, material_count, failed, str(exc)[:1000], run_id),
            )
        scheduled_tasks.finish_run(scheduled_run_id, status="failed", error_message=str(exc))
        raise
