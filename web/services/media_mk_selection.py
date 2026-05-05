from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import requests


@dataclass(frozen=True)
class MkSelectionResponse:
    payload: dict
    status_code: int


@dataclass(frozen=True)
class MkDetailResponse:
    payload: dict
    status_code: int


def _parse_bounded_int(
    args: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw_value = args.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(name) from exc
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def build_mk_selection_response(
    args: Mapping[str, str],
    *,
    ranking_columns_fn: Callable[[], Sequence[str] | set[str]],
    db_query_fn: Callable[[str, list], list[dict]],
) -> MkSelectionResponse:
    snapshot = (args.get("snapshot") or "2026-04-23").strip()
    keyword = (args.get("keyword") or "").strip()
    try:
        page_num = _parse_bounded_int(args, "page", default=1, minimum=1)
        page_size = _parse_bounded_int(args, "page_size", default=50, minimum=10, maximum=100)
    except ValueError as exc:
        return MkSelectionResponse(
            {
                "error": "invalid_pagination",
                "message": f"{exc.args[0]} must be an integer",
            },
            400,
        )
    offset = (page_num - 1) * page_size

    ranking_columns = ranking_columns_fn()
    has_mk_product_id = "mk_product_id" in ranking_columns
    has_mk_product_name = "mk_product_name" in ranking_columns
    has_mk_total_spends = "mk_total_spends" in ranking_columns
    has_mk_video_count = "mk_video_count" in ranking_columns
    has_mk_total_ads = "mk_total_ads" in ranking_columns

    where = "dr.snapshot_date = %s"
    params: list = [snapshot]

    if keyword:
        keyword_clauses = ["dr.product_name LIKE %s"]
        params.append(f"%{keyword}%")
        if has_mk_product_name:
            keyword_clauses.append("dr.mk_product_name LIKE %s")
            params.append(f"%{keyword}%")
        where += " AND (" + " OR ".join(keyword_clauses) + ")"

    mk_product_id_select = "dr.mk_product_id" if has_mk_product_id else "NULL AS mk_product_id"
    mk_product_name_select = "dr.mk_product_name" if has_mk_product_name else "NULL AS mk_product_name"
    mk_total_spends_select = "dr.mk_total_spends" if has_mk_total_spends else "0 AS mk_total_spends"
    mk_video_count_select = "dr.mk_video_count" if has_mk_video_count else "0 AS mk_video_count"
    mk_total_ads_select = "dr.mk_total_ads" if has_mk_total_ads else "0 AS mk_total_ads"
    order_by = "dr.mk_total_spends DESC, dr.rank_position ASC" if has_mk_total_spends else "dr.rank_position ASC"

    count_row = db_query_fn(
        f"SELECT COUNT(*) AS cnt FROM dianxiaomi_rankings dr WHERE {where}",
        params,
    )
    total = count_row[0]["cnt"] if count_row else 0

    rows = db_query_fn(
        f"""
        SELECT
            dr.rank_position, dr.product_id AS shopify_id,
            dr.product_name, dr.product_url,
            dr.store, dr.sales_count, dr.order_count,
            dr.revenue_main, dr.revenue_split,
            {mk_product_id_select}, {mk_product_name_select},
            {mk_total_spends_select}, {mk_video_count_select}, {mk_total_ads_select},
            dr.media_product_id,
            mp.name AS mp_name, mp.product_code AS mp_code
        FROM dianxiaomi_rankings dr
        LEFT JOIN media_products mp ON dr.media_product_id = mp.id
        WHERE {where}
        ORDER BY {order_by}
        LIMIT %s OFFSET %s
        """,
        params + [page_size, offset],
    )

    items = []
    for row in rows:
        items.append({
            "rank": row["rank_position"],
            "shopify_id": row["shopify_id"],
            "product_name": row["product_name"],
            "product_url": row["product_url"],
            "store": row["store"],
            "sales_count": row["sales_count"],
            "order_count": row["order_count"],
            "revenue_main": row["revenue_main"],
            "revenue_split": row["revenue_split"],
            "mk_product_id": row["mk_product_id"],
            "mk_product_name": row["mk_product_name"],
            "mk_total_spends": float(row["mk_total_spends"] or 0),
            "mk_video_count": row["mk_video_count"] or 0,
            "mk_total_ads": row["mk_total_ads"] or 0,
            "media_product_id": row["media_product_id"],
            "mp_name": row["mp_name"],
            "mp_code": row["mp_code"],
        })

    return MkSelectionResponse(
        {"items": items, "total": total, "page": page_num, "page_size": page_size},
        200,
    )


def build_mk_detail_response(
    mk_id: int,
    *,
    build_headers_fn: Callable[[], dict],
    get_base_url_fn: Callable[[], str],
    is_login_expired_fn: Callable[[dict], bool],
    http_get_fn=requests.get,
) -> MkDetailResponse:
    headers = build_headers_fn()
    if "Authorization" not in headers and "Cookie" not in headers:
        return MkDetailResponse(
            {"error": "明空凭据未配置，请先在设置页同步 wedev 凭据"},
            500,
        )
    base_url = get_base_url_fn()
    try:
        resp = http_get_fn(
            f"{base_url}/api/marketing/medias/{mk_id}",
            headers=headers,
            timeout=15,
        )
        data = resp.json()
    except Exception as exc:
        return MkDetailResponse({"error": str(exc)}, 502)

    if is_login_expired_fn(data):
        return MkDetailResponse(
            {"error": "明空登录已失效，请重新同步 wedev 凭据"},
            401,
        )
    return MkDetailResponse(data, resp.status_code)
