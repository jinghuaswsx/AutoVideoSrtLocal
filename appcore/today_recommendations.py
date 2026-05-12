"""Today recommendation library for Xuanpin.

The generator is intentionally operator-driven. This module owns only storage,
listing, and adoption into the task center.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any

from appcore import local_media_storage, mk_import, pushes, tasks
from appcore.db import execute, get_conn, query, query_one
from appcore.medias import create_item
from web.services.media_mk_selection import (
    build_mk_video_cache_object_key,
    cache_mk_video,
    normalize_mk_media_path,
)


STATUS_PENDING = "pending"
STATUS_ADOPTED = "adopted"
STATUS_ADOPT_FAILED = "adopt_failed"
MK_VIDEO_CACHE_PREFIX = "mk-selection/videos"


def guard_against_windows_local_mysql() -> None:
    if os.name != "nt":
        return
    from config import DB_HOST, DB_PORT

    host = str(DB_HOST or "").strip().lower()
    if host in {"127.0.0.1", "localhost", "::1"} and int(DB_PORT) == 3306:
        raise RuntimeError(
            "项目规则禁止在 Windows 本机连接 127.0.0.1:3306 MySQL；"
            "今日推荐生成和验证请在服务器环境运行。"
        )


def candidate_key_for(product_key: str, video_path: str | None, video_name: str | None) -> str:
    raw = "|".join((product_key or "", video_path or "", video_name or ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dump_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _load_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _coerce_date(value: str | date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def latest_recommendation_date() -> str | None:
    row = query_one(
        "SELECT MAX(recommendation_date) AS recommendation_date "
        "FROM xuanpin_today_recommendations"
    )
    return _coerce_date((row or {}).get("recommendation_date"))


def latest_run_summary() -> dict | None:
    row = query_one(
        "SELECT * FROM xuanpin_today_recommendation_runs "
        "ORDER BY id DESC LIMIT 1"
    )
    if not row:
        return None
    row = dict(row)
    row["recommendation_date"] = _coerce_date(row.get("recommendation_date"))
    row["ranking_snapshot_date"] = _coerce_date(row.get("ranking_snapshot_date"))
    row["summary_json"] = _load_json(row.get("summary_json"), {})
    for key in ("created_at", "finished_at"):
        value = row.get(key)
        row[key] = value.isoformat(sep=" ") if hasattr(value, "isoformat") else value
    return row


def create_run(
    *,
    recommendation_date: str | date,
    ranking_snapshot_date: str | date | None,
    source_limit: int,
    target_products: int,
    target_materials: int,
    ai_provider: str,
    ai_model: str,
) -> int:
    guard_against_windows_local_mysql()
    return execute(
        "INSERT INTO xuanpin_today_recommendation_runs "
        "(recommendation_date, ranking_snapshot_date, source_limit, target_products, "
        " target_materials, status, ai_provider, ai_model) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            _coerce_date(recommendation_date),
            _coerce_date(ranking_snapshot_date),
            int(source_limit),
            int(target_products),
            int(target_materials),
            "running",
            ai_provider,
            ai_model,
        ),
    )


def finish_run(
    run_id: int,
    *,
    status: str,
    summary: dict | None = None,
    output_file: str | None = None,
    error_message: str | None = None,
) -> None:
    execute(
        "UPDATE xuanpin_today_recommendation_runs "
        "SET status=%s, summary_json=%s, output_file=%s, error_message=%s, finished_at=NOW() "
        "WHERE id=%s",
        (
            status,
            _dump_json(summary or {}),
            output_file,
            error_message,
            int(run_id),
        ),
    )


def adopted_candidate_keys() -> set[str]:
    rows = query(
        "SELECT DISTINCT candidate_key FROM xuanpin_today_recommendations "
        "WHERE status=%s",
        (STATUS_ADOPTED,),
    )
    return {str(row["candidate_key"]) for row in rows if row.get("candidate_key")}


def replace_recommendations(
    *,
    run_id: int,
    recommendation_date: str | date,
    ranking_snapshot_date: str | date,
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    guard_against_windows_local_mysql()
    reco_date = _coerce_date(recommendation_date)
    snapshot_date = _coerce_date(ranking_snapshot_date)
    adopted = adopted_candidate_keys()
    inserted = 0
    skipped_adopted = 0
    conn = get_conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM xuanpin_today_recommendations "
                "WHERE recommendation_date=%s AND status<>%s",
                (reco_date, STATUS_ADOPTED),
            )
            for item in rows:
                candidate_key = str(item.get("candidate_key") or "")
                if candidate_key in adopted:
                    skipped_adopted += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO xuanpin_today_recommendations
                      (run_id, recommendation_date, ranking_snapshot_date, candidate_key,
                       product_recommendation_rank, material_rank, overall_score,
                       product_key, product_handle, shopify_product_id, product_name, product_url,
                       sales_count, order_count, revenue_main, rank_position,
                       mk_product_id, mk_product_name, mk_total_spends, mk_total_ads, mk_video_count,
                       video_name, video_path, video_image_path, video_spends, video_ads_count,
                       video_author, video_upload_time, video_duration_seconds,
                       recommended_countries, ai_reason, ai_detail_json, mk_video_metadata_json, status)
                    VALUES
                      (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                       run_id=VALUES(run_id),
                       ranking_snapshot_date=VALUES(ranking_snapshot_date),
                       product_recommendation_rank=VALUES(product_recommendation_rank),
                       material_rank=VALUES(material_rank),
                       overall_score=VALUES(overall_score),
                       ai_reason=VALUES(ai_reason),
                       ai_detail_json=VALUES(ai_detail_json),
                       mk_video_metadata_json=VALUES(mk_video_metadata_json),
                       status=IF(status=%s, status, VALUES(status)),
                       updated_at=NOW()
                    """,
                    (
                        int(run_id),
                        reco_date,
                        snapshot_date,
                        candidate_key,
                        int(item.get("product_recommendation_rank") or 0),
                        int(item.get("material_rank") or 0),
                        float(item.get("overall_score") or 0),
                        item.get("product_key") or "",
                        item.get("product_handle") or None,
                        item.get("shopify_product_id") or None,
                        item.get("product_name") or "",
                        item.get("product_url") or None,
                        item.get("sales_count"),
                        item.get("order_count"),
                        item.get("revenue_main") or None,
                        item.get("rank_position"),
                        item.get("mk_product_id"),
                        item.get("mk_product_name") or None,
                        float(item.get("mk_total_spends") or 0),
                        int(item.get("mk_total_ads") or 0),
                        int(item.get("mk_video_count") or 0),
                        item.get("video_name") or None,
                        item.get("video_path") or None,
                        item.get("video_image_path") or None,
                        float(item.get("video_spends") or 0),
                        int(item.get("video_ads_count") or 0),
                        item.get("video_author") or None,
                        item.get("video_upload_time") or None,
                        item.get("video_duration_seconds"),
                        _dump_json(item.get("recommended_countries") or []),
                        item.get("ai_reason") or None,
                        _dump_json(item.get("ai_detail") or {}),
                        _dump_json(item.get("mk_video_metadata") or {}),
                        STATUS_PENDING,
                        STATUS_ADOPTED,
                    ),
                )
                inserted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"inserted": inserted, "skipped_adopted": skipped_adopted}


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["recommendation_date"] = _coerce_date(out.get("recommendation_date"))
    out["ranking_snapshot_date"] = _coerce_date(out.get("ranking_snapshot_date"))
    out["recommended_countries"] = _load_json(out.get("recommended_countries"), [])
    out["ai_detail"] = _load_json(out.pop("ai_detail_json", None), {})
    out["mk_video_metadata"] = _load_json(out.get("mk_video_metadata_json"), {})
    for key in ("created_at", "updated_at", "adopted_at"):
        value = out.get(key)
        out[key] = value.isoformat(sep=" ") if hasattr(value, "isoformat") else value
    for key, value in list(out.items()):
        if isinstance(value, Decimal):
            out[key] = float(value)
    return out


def list_recommendations(
    *,
    recommendation_date: str | date | None = None,
    include_adopted: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    guard_against_windows_local_mysql()
    reco_date = _coerce_date(recommendation_date) or latest_recommendation_date()
    if not reco_date:
        return []
    where = ["recommendation_date=%s"]
    args: list[Any] = [reco_date]
    if not include_adopted:
        where.append("status<>%s")
        args.append(STATUS_ADOPTED)
    rows = query(
        "SELECT * FROM xuanpin_today_recommendations "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY product_recommendation_rank ASC, material_rank ASC, id ASC "
        "LIMIT %s",
        tuple(args + [int(limit)]),
    )
    return [_serialize_row(row) for row in rows]


_FILENAME_UNSAFE_RE = re.compile(r"[\s\\/:*?\"<>|]+")


def _safe_video_filename(name: str | None, *, fallback: str) -> str:
    raw = PurePosixPath(str(name or "").replace("\\", "/")).name.strip()
    if not raw:
        raw = fallback
    safe = _FILENAME_UNSAFE_RE.sub("_", raw).strip("._")
    if not safe:
        safe = fallback
    suffix = PurePosixPath(safe).suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
        safe = f"{safe}.mp4"
    return safe[:240]


def _build_headers() -> dict[str, str]:
    headers = pushes.build_localized_texts_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        raise RuntimeError("MK credentials missing")
    return headers


def _cache_object_key(media_path: str) -> str:
    return build_mk_video_cache_object_key(
        media_path,
        cache_prefix=MK_VIDEO_CACHE_PREFIX,
    )


def _cache_recommended_video(video_path: str) -> str:
    normalized_path = normalize_mk_media_path(video_path)
    if not normalized_path:
        raise ValueError("video_path missing")
    base_url = pushes.get_localized_texts_base_url() or "https://os.wedev.vip"
    return cache_mk_video(
        normalized_path,
        cache_object_key_fn=_cache_object_key,
        storage_exists_fn=local_media_storage.exists,
        build_headers_fn=_build_headers,
        get_base_url_fn=lambda: base_url.rstrip("/"),
        safe_local_path_for_fn=local_media_storage.safe_local_path_for,
    )


def _find_existing_item(filename: str) -> dict | None:
    return query_one(
        "SELECT id, product_id, object_key FROM media_items "
        "WHERE filename=%s AND deleted_at IS NULL ORDER BY id DESC LIMIT 1",
        (filename,),
    )


def _ensure_product(row: dict[str, Any], translator_id: int) -> int:
    meta = _load_json(row.get("mk_video_metadata_json"), {})
    product_code = mk_import._normalize_product_code(
        meta.get("product_code") or row.get("product_handle") or row.get("product_key")
    )
    existing = mk_import._find_existing_product(product_code)
    if existing:
        return int(existing["id"])
    return execute(
        "INSERT INTO media_products "
        "(user_id, name, product_code, product_link, main_image, mk_id) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (
            int(translator_id),
            (row.get("mk_product_name") or row.get("product_name") or "")[:255],
            product_code,
            row.get("product_url"),
            meta.get("main_image") or None,
            row.get("mk_product_id"),
        ),
    )


def _ensure_media_item(row: dict[str, Any], *, product_id: int, translator_id: int) -> int:
    fallback = f"today-reco-{int(row['id'])}"
    filename = _safe_video_filename(row.get("video_name"), fallback=fallback)
    existing = _find_existing_item(filename)
    if existing:
        return int(existing["id"])
    object_key = _cache_recommended_video(str(row.get("video_path") or ""))
    return int(
        create_item(
            product_id=int(product_id),
            user_id=int(translator_id),
            filename=filename,
            object_key=object_key,
            display_name=filename,
            duration_seconds=row.get("video_duration_seconds"),
            lang="en",
        )
    )


def _normalize_countries(value: Any) -> list[str]:
    raw = _load_json(value, []) if not isinstance(value, list) else value
    countries = []
    for item in raw or []:
        code = str(item or "").strip().upper()
        if code and code not in countries:
            countries.append(code)
    return countries


def adopt_recommendations(
    *,
    recommendation_ids: list[int],
    translator_id: int,
    actor_user_id: int,
) -> dict[str, Any]:
    guard_against_windows_local_mysql()
    ids = [int(item) for item in recommendation_ids if int(item)]
    if not ids:
        raise ValueError("recommendation_ids required")

    from appcore.new_product_review import _resolve_translator

    _resolve_translator(int(translator_id))
    placeholders = ",".join(["%s"] * len(ids))
    rows = query(
        f"SELECT * FROM xuanpin_today_recommendations WHERE id IN ({placeholders}) "
        "ORDER BY product_recommendation_rank ASC, material_rank ASC",
        tuple(ids),
    )
    by_id = {int(row["id"]): row for row in rows}
    adopted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for rid in ids:
        row = by_id.get(rid)
        if not row:
            skipped.append({"id": rid, "reason": "not_found"})
            continue
        if row.get("status") == STATUS_ADOPTED:
            skipped.append({"id": rid, "reason": "already_adopted"})
            continue
        countries = _normalize_countries(row.get("recommended_countries"))
        if not countries:
            failed.append({"id": rid, "error": "recommended_countries empty"})
            continue
        try:
            product_id = _ensure_product(row, int(translator_id))
            item_id = _ensure_media_item(row, product_id=product_id, translator_id=int(translator_id))
            task_id = tasks.create_parent_task(
                media_product_id=product_id,
                media_item_id=item_id,
                countries=countries,
                translator_id=int(translator_id),
                created_by=int(actor_user_id),
            )
            execute(
                "UPDATE xuanpin_today_recommendations "
                "SET status=%s, media_product_id=%s, media_item_id=%s, "
                "adopted_task_id=%s, adopted_by=%s, adopted_at=NOW(), error_message=NULL "
                "WHERE id=%s",
                (
                    STATUS_ADOPTED,
                    product_id,
                    item_id,
                    int(task_id),
                    int(actor_user_id),
                    rid,
                ),
            )
            adopted.append({
                "id": rid,
                "media_product_id": product_id,
                "media_item_id": item_id,
                "task_id": int(task_id),
                "countries": countries,
            })
        except Exception as exc:
            message = str(exc)[:1000]
            execute(
                "UPDATE xuanpin_today_recommendations "
                "SET status=%s, error_message=%s WHERE id=%s",
                (STATUS_ADOPT_FAILED, message, rid),
            )
            failed.append({"id": rid, "error": message})

    return {"adopted": adopted, "skipped": skipped, "failed": failed}
