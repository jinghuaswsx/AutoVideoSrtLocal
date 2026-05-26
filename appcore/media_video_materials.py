"""Video material listing and Mingkong binding helpers."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlencode

import requests

from appcore import pushes
from appcore.db import execute, query, query_one
from web.services.media_mk_selection import normalize_mk_media_path


AD_PLAN_ALL = "all"
AD_PLAN_HAS = "has"
AD_PLAN_NONE = "none"
AD_PLAN_FILTERS = {AD_PLAN_ALL, AD_PLAN_HAS, AD_PLAN_NONE}

_SPACE_RE = re.compile(r"\s+")


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value if value else None


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


def normalize_material_name(value: str | None) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    name = PurePosixPath(raw).name.strip().lower()
    return _SPACE_RE.sub(" ", name)


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _decimal_to_float(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key, value in list(out.items()):
        if isinstance(value, Decimal):
            out[key] = float(value)
    return out


def _page_bounds(page: int | str | None, page_size: int | str | None) -> tuple[int, int, int]:
    page_num = max(1, _int_value(page, 1))
    size = min(100, max(10, _int_value(page_size, 100)))
    return page_num, size, (page_num - 1) * size


def _where_for_video_materials(
    *,
    keyword: str = "",
    lang: str = "",
    ad_plan_status: str = AD_PLAN_ALL,
) -> tuple[list[str], list[Any]]:
    where = ["i.deleted_at IS NULL", "p.deleted_at IS NULL"]
    args: list[Any] = []
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        clauses = [
            "i.filename LIKE %s",
            "i.display_name LIKE %s",
            "p.name LIKE %s",
            "p.product_code LIKE %s",
        ]
        args.extend([like, like, like, like])
        if kw.isdigit():
            clauses.extend(["i.id=%s", "p.id=%s", "p.mk_id=%s"])
            args.extend([int(kw), int(kw), int(kw)])
        where.append(f"({' OR '.join(clauses)})")
    normalized_lang = str(lang or "").strip().lower()
    if normalized_lang:
        where.append("i.lang=%s")
        args.append(normalized_lang)
    status = str(ad_plan_status or AD_PLAN_ALL).strip().lower()
    if status not in AD_PLAN_FILTERS:
        status = AD_PLAN_ALL
    has_ad_plan_clause = (
        "EXISTS ("
        "SELECT 1 FROM meta_ad_daily_ad_metrics madm "
        "WHERE madm.product_id = i.product_id "
        "AND COALESCE(madm.spend_usd, 0) > 0 "
        "AND ("
        "madm.ad_name LIKE CONCAT('%%', i.filename, '%%') "
        "OR madm.ad_name LIKE CONCAT('%%', i.display_name, '%%')"
        ")"
        ")"
    )
    if status == AD_PLAN_HAS:
        where.append(has_ad_plan_clause)
    elif status == AD_PLAN_NONE:
        where.append(f"NOT {has_ad_plan_clause}")
    return where, args


def list_video_materials(
    *,
    keyword: str = "",
    lang: str = "",
    ad_plan_status: str = AD_PLAN_ALL,
    page: int | str | None = 1,
    page_size: int | str | None = 100,
) -> dict[str, Any]:
    page_num, size, offset = _page_bounds(page, page_size)
    where, args = _where_for_video_materials(
        keyword=keyword,
        lang=lang,
        ad_plan_status=ad_plan_status,
    )
    where_sql = " AND ".join(where)
    total_row = query_one(
        "SELECT COUNT(*) AS c "
        "FROM media_items i JOIN media_products p ON p.id=i.product_id "
        f"WHERE {where_sql}",
        tuple(args),
    ) or {}
    rows = query(
        "SELECT i.id, i.product_id, i.lang, i.filename, i.display_name, "
        "       i.object_key, i.thumbnail_path, i.cover_object_key, "
        "       i.duration_seconds, i.file_size, i.pushed_at, i.latest_push_id, i.created_at, "
        "       p.name AS product_name, p.product_code, p.mk_id AS product_mk_id, "
        "       u.username AS owner_username, "
        "       b.id AS binding_id, b.mk_product_id, b.mk_product_name, "
        "       b.mk_video_path, b.mk_video_name, b.mk_video_image_path, "
        "       b.mk_video_metadata_json, b.bound_by, b.bound_at, "
        "       (SELECT COUNT(*) FROM media_push_logs mpl "
        "        WHERE mpl.item_id=i.id AND mpl.status='success') AS push_success_count "
        "FROM media_items i "
        "JOIN media_products p ON p.id=i.product_id "
        "LEFT JOIN users u ON u.id=i.user_id "
        "LEFT JOIN media_item_mk_bindings b ON b.media_item_id=i.id "
        f"WHERE {where_sql} "
        "ORDER BY i.created_at DESC, i.id DESC "
        "LIMIT %s OFFSET %s",
        tuple(args + [size, offset]),
    )
    _attach_ad_plan_details(rows)
    return {
        "items": [serialize_video_material(row) for row in rows],
        "total": int(total_row.get("c") or 0),
        "page": page_num,
        "page_size": size,
    }


def _attach_ad_plan_details(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    product_ids = sorted({
        int(row["product_id"])
        for row in rows
        if _int_value(row.get("product_id")) > 0
    })
    if not product_ids:
        return

    ad_candidates = _load_ad_candidates_for_materials(product_ids)
    if not ad_candidates:
        return

    ads_by_product: dict[int, list[dict[str, Any]]] = {}
    for candidate in ad_candidates:
        pid = _int_value(candidate.get("product_id"))
        if pid > 0:
            ads_by_product.setdefault(pid, []).append(candidate)

    for row in rows:
        product_id = _int_value(row.get("product_id"))
        if product_id not in ads_by_product:
            continue

        filename = str(row.get("filename") or "").strip().lower()
        display_name = str(row.get("display_name") or "").strip().lower()

        filename_norm = normalize_material_name(filename)
        display_name_norm = normalize_material_name(display_name)

        filename_no_ext = filename_norm.rsplit(".", 1)[0] if "." in filename_norm else filename_norm
        display_name_no_ext = display_name_norm.rsplit(".", 1)[0] if "." in display_name_norm else display_name_norm

        candidates = ads_by_product[product_id]
        matched_ad = None
        for candidate in candidates:
            ad_name_lower = str(candidate.get("ad_name") or "").strip().lower()
            ad_code_lower = str(candidate.get("normalized_ad_code") or "").strip().lower()

            matches = False
            if filename and (filename in ad_name_lower or filename in ad_code_lower):
                matches = True
            elif display_name and (display_name in ad_name_lower or display_name in ad_code_lower):
                matches = True
            elif filename_norm and (filename_norm in ad_name_lower or filename_norm in ad_code_lower):
                matches = True
            elif display_name_norm and (display_name_norm in ad_name_lower or display_name_norm in ad_code_lower):
                matches = True
            elif filename_no_ext and len(filename_no_ext) > 5 and (filename_no_ext in ad_name_lower or filename_no_ext in ad_code_lower):
                matches = True
            elif display_name_no_ext and len(display_name_no_ext) > 5 and (display_name_no_ext in ad_name_lower or display_name_no_ext in ad_code_lower):
                matches = True

            if matches:
                matched_ad = candidate
                break

        if matched_ad:
            row["ad_campaign_code"] = matched_ad.get("normalized_campaign_code")
            row["ad_campaign_name"] = matched_ad.get("campaign_name")
            row["ad_account_id"] = matched_ad.get("ad_account_id")
            row["ad_account_name"] = matched_ad.get("ad_account_name")
            row["ad_plan_activity_date"] = matched_ad.get("activity_date")


def _load_ad_candidates_for_materials(product_ids: list[int]) -> list[dict[str, Any]]:
    if not product_ids:
        return []
    placeholders = ",".join(["%s"] * len(product_ids))
    return query(
        "SELECT m.product_id, m.normalized_ad_code, m.ad_name, "
        "       m.normalized_campaign_code, m.campaign_name, "
        "       m.ad_account_id, m.ad_account_name, "
        "       COALESCE(m.meta_business_date, m.report_date) AS activity_date, "
        "       m.spend_usd, m.id "
        "FROM meta_ad_daily_ad_metrics m "
        f"WHERE m.product_id IN ({placeholders}) AND COALESCE(m.spend_usd, 0) > 0 "
        "ORDER BY activity_date DESC, COALESCE(spend_usd, 0) DESC, id DESC",
        tuple(product_ids),
    )


def serialize_video_material(row: dict[str, Any]) -> dict[str, Any]:
    item = _decimal_to_float(row)
    push_success_count = int(item.get("push_success_count") or 0)
    binding_id = item.get("binding_id")
    binding = None
    if binding_id:
        binding = {
            "id": int(binding_id),
            "mk_product_id": item.get("mk_product_id"),
            "mk_product_name": item.get("mk_product_name") or "",
            "mk_video_path": item.get("mk_video_path") or "",
            "mk_video_name": item.get("mk_video_name") or "",
            "mk_video_image_path": item.get("mk_video_image_path") or "",
            "mk_video_metadata": _json_loads(item.get("mk_video_metadata_json"), {}),
            "bound_by": item.get("bound_by"),
            "bound_at": _iso(item.get("bound_at")),
        }
    has_ad_plan = bool(item.get("ad_campaign_code"))
    pushed_at_val = item.get("ad_plan_activity_date") if has_ad_plan else None
    return {
        "id": int(item["id"]),
        "product_id": int(item["product_id"]),
        "product_name": item.get("product_name") or "",
        "product_code": item.get("product_code") or "",
        "product_mk_id": item.get("product_mk_id"),
        "lang": item.get("lang") or "en",
        "filename": item.get("filename") or "",
        "display_name": item.get("display_name") or item.get("filename") or "",
        "object_key": item.get("object_key") or "",
        "thumbnail_url": f"/medias/thumb/{int(item['id'])}" if item.get("thumbnail_path") else "",
        "cover_url": f"/medias/item-cover/{int(item['id'])}" if item.get("cover_object_key") else "",
        "video_url": f"/medias/object?object_key={quote(str(item.get('object_key') or ''), safe='')}",
        "duration_seconds": item.get("duration_seconds"),
        "file_size": item.get("file_size"),
        "owner_username": item.get("owner_username") or "",
        "created_at": _iso(item.get("created_at")),
        "pushed_at": _iso(pushed_at_val),
        "latest_push_id": item.get("latest_push_id"),
        "push_success_count": push_success_count,
        "has_ad_plan": has_ad_plan,
        "ad_plan_status": "has" if has_ad_plan else "none",
        "ad_plan_detail": _ad_plan_detail(item, has_ad_plan),
        "mk_binding": binding,
    }


def get_video_material(item_id: int) -> dict[str, Any] | None:
    return query_one(
        "SELECT i.id, i.product_id FROM media_items i "
        "JOIN media_products p ON p.id=i.product_id "
        "WHERE i.id=%s AND i.deleted_at IS NULL AND p.deleted_at IS NULL",
        (int(item_id),),
    )


def bind_mk_material(
    *,
    media_item_id: int,
    mk_product_id: int | None,
    mk_product_name: str | None,
    mk_video_path: str,
    mk_video_name: str | None = None,
    mk_video_image_path: str | None = None,
    mk_video_metadata: dict[str, Any] | None = None,
    bound_by: int | None = None,
) -> dict[str, Any]:
    if not get_video_material(int(media_item_id)):
        raise ValueError("media_item not found")
    normalized_path = normalize_mk_media_path(mk_video_path)
    if not normalized_path:
        raise ValueError("mk_video_path required")
    execute(
        "INSERT INTO media_item_mk_bindings "
        "(media_item_id, mk_product_id, mk_product_name, mk_video_path, mk_video_name, "
        " mk_video_image_path, mk_video_metadata_json, bound_by, bound_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW()) "
        "ON DUPLICATE KEY UPDATE "
        "mk_product_id=VALUES(mk_product_id), "
        "mk_product_name=VALUES(mk_product_name), "
        "mk_video_path=VALUES(mk_video_path), "
        "mk_video_name=VALUES(mk_video_name), "
        "mk_video_image_path=VALUES(mk_video_image_path), "
        "mk_video_metadata_json=VALUES(mk_video_metadata_json), "
        "bound_by=VALUES(bound_by), bound_at=NOW(), updated_at=NOW()",
        (
            int(media_item_id),
            int(mk_product_id) if mk_product_id else None,
            (mk_product_name or "")[:500] or None,
            normalized_path,
            (mk_video_name or "")[:500] or None,
            normalize_mk_media_path(mk_video_image_path or "") or None,
            _json_dumps(mk_video_metadata or {}),
            int(bound_by) if bound_by else None,
        ),
    )
    row = query_one(
        "SELECT i.*, p.name AS product_name, p.product_code, p.mk_id AS product_mk_id, "
        "       b.id AS binding_id, b.mk_product_id, b.mk_product_name, "
        "       b.mk_video_path, b.mk_video_name, b.mk_video_image_path, "
        "       b.mk_video_metadata_json, b.bound_by, b.bound_at, "
        "       NULL AS ad_campaign_code, NULL AS ad_campaign_name, "
        "       NULL AS ad_account_id, NULL AS ad_account_name, "
        "       0 AS push_success_count "
        "FROM media_items i "
        "JOIN media_products p ON p.id=i.product_id "
        "LEFT JOIN media_item_mk_bindings b ON b.media_item_id=i.id "
        "WHERE i.id=%s",
        (int(media_item_id),),
    )
    return serialize_video_material(row or {"id": media_item_id, "product_id": 0})


def _normalize_ad_account_id(value: Any) -> str:
    return str(value or "").strip().removeprefix("act_")


def _ad_plan_detail(item: dict[str, Any], has_ad_plan: bool) -> dict[str, Any] | None:
    if not has_ad_plan:
        return None
    code = str(item.get("ad_campaign_code") or "").strip().lower()
    if not code:
        return None
    name = str(item.get("ad_campaign_name") or code).strip()
    account_id = _normalize_ad_account_id(item.get("ad_account_id"))
    params = {
        "tab": "ads",
        "ads_level": "campaign",
        "ads_code": code,
    }
    if name:
        params["ads_name"] = name
    if account_id:
        params["ad_account_id"] = account_id
    return {
        "level": "campaign",
        "code": code,
        "name": name,
        "ad_account_id": account_id,
        "ad_account_name": item.get("ad_account_name") or "",
        "url": "/order-analytics?" + urlencode(params),
    }


def _mk_headers() -> dict[str, str]:
    headers = pushes.build_localized_texts_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        raise RuntimeError("MK credentials missing")
    return headers


def _mk_base_url() -> str:
    return (pushes.get_localized_texts_base_url() or "https://os.wedev.vip").rstrip("/")


def _visible_mk_videos(item: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in item.get("videos") or []:
        if not isinstance(raw, dict) or raw.get("hidden"):
            continue
        path = normalize_mk_media_path(str(raw.get("path") or ""))
        if not path:
            continue
        out.append({
            "name": str(raw.get("name") or "").strip(),
            "path": path,
            "image_path": normalize_mk_media_path(str(raw.get("image_path") or "")),
            "spends": _float_value(raw.get("spends")),
            "ads_count": _int_value(raw.get("ads_count")),
            "author": str(raw.get("author") or "").strip(),
            "upload_time": str(raw.get("upload_time") or "").strip(),
            "duration_seconds": _float_value(raw.get("duration_seconds") or raw.get("duration"), 0.0),
        })
    out.sort(key=lambda row: (float(row.get("spends") or 0), int(row.get("ads_count") or 0)), reverse=True)
    return out


def search_mk_materials(
    *,
    keyword: str,
    limit: int = 50,
    page: int = 1,
    timeout: int = 20,
) -> list[dict[str, Any]]:
    kw = str(keyword or "").strip()
    if not kw:
        return []
    resp = requests.get(
        f"{_mk_base_url()}/api/marketing/medias",
        params={"page": max(1, int(page)), "q": kw, "source": "", "level": "", "show_attention": 0},
        headers=_mk_headers(),
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    if data.get("is_guest") is True or str(data.get("message") or "").startswith("登录"):
        raise RuntimeError("MK credentials expired")
    out: list[dict[str, Any]] = []
    kw_norm = normalize_material_name(kw)
    for product in ((data.get("data") or {}).get("items") or []):
        if not isinstance(product, dict):
            continue
        for video in _visible_mk_videos(product):
            video_name = str(video.get("name") or "")
            if kw_norm and kw_norm not in normalize_material_name(video_name):
                searchable = " ".join([
                    str(product.get("product_name") or ""),
                    str(product.get("id") or ""),
                    str(video.get("path") or ""),
                ])
                if kw_norm not in normalize_material_name(searchable):
                    continue
            out.append({
                "mk_product_id": product.get("id"),
                "mk_product_name": product.get("product_name") or "",
                "product_links": product.get("product_links") or [],
                "main_image": product.get("main_image") or product.get("image") or "",
                "video_name": video.get("name") or "",
                "video_path": video.get("path") or "",
                "video_image_path": video.get("image_path") or "",
                "video_metadata": video,
            })
            if len(out) >= int(limit):
                return out
    return out


def existing_english_material_identity() -> dict[str, set[str]]:
    rows = query(
        "SELECT filename, display_name, object_key "
        "FROM media_items "
        "WHERE lang='en' AND deleted_at IS NULL",
        (),
    )
    bindings = query(
        "SELECT b.mk_video_path, b.mk_video_name "
        "FROM media_item_mk_bindings b "
        "JOIN media_items i ON i.id=b.media_item_id "
        "WHERE i.lang='en' AND i.deleted_at IS NULL",
        (),
    )
    names: set[str] = set()
    paths: set[str] = set()
    for row in rows:
        for key in ("filename", "display_name", "object_key"):
            name = normalize_material_name(row.get(key))
            if name:
                names.add(name)
    for row in bindings:
        path = normalize_mk_media_path(str(row.get("mk_video_path") or ""))
        if path:
            paths.add(path)
        name = normalize_material_name(row.get("mk_video_name"))
        if name:
            names.add(name)
    return {"names": names, "paths": paths}


def is_existing_english_material(
    *,
    video_path: str | None,
    video_name: str | None,
    identity: dict[str, set[str]] | None = None,
) -> bool:
    existing = identity or existing_english_material_identity()
    path = normalize_mk_media_path(str(video_path or ""))
    if path and path in existing.get("paths", set()):
        return True
    name = normalize_material_name(video_name)
    return bool(name and name in existing.get("names", set()))
