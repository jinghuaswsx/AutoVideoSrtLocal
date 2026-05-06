"""Listing aggregation helpers for OpenAPI materials routes."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from appcore.openapi_materials import (
    count_material_products,
    list_material_products,
    list_product_copywriting_lang_rows,
    list_product_cover_lang_rows,
    list_product_item_lang_count_rows,
)
from web.services.openapi_materials_serializers import iso_or_none


QueryFn = Callable[[str, tuple], list[dict]]
LIST_PAGE_SIZE_MAX = 100


def parse_archived_filter(raw: str) -> int | None:
    """Return 0/1 to filter, None for 'all'."""
    value = (raw or "").strip().lower()
    if value == "all":
        return None
    if value == "1":
        return 1
    # 默认只看未归档
    return 0


def batch_cover_langs(
    product_ids: list[int],
    *,
    query_fn: QueryFn | None = None,
    list_rows_fn=list_product_cover_lang_rows,
) -> dict[int, list[str]]:
    if not product_ids:
        return {}
    if query_fn is not None and list_rows_fn is list_product_cover_lang_rows:
        rows = list_rows_fn(product_ids, query_func=query_fn)
    else:
        rows = list_rows_fn(product_ids)
    out: dict[int, list[str]] = defaultdict(list)
    for row in rows or []:
        if row.get("object_key"):
            out[int(row["product_id"])].append(row.get("lang") or "en")
    return dict(out)


def batch_copywriting_langs(
    product_ids: list[int],
    *,
    query_fn: QueryFn | None = None,
    list_rows_fn=list_product_copywriting_lang_rows,
) -> dict[int, list[str]]:
    if not product_ids:
        return {}
    if query_fn is not None and list_rows_fn is list_product_copywriting_lang_rows:
        rows = list_rows_fn(product_ids, query_func=query_fn)
    else:
        rows = list_rows_fn(product_ids)
    out: dict[int, list[str]] = defaultdict(list)
    for row in rows or []:
        out[int(row["product_id"])].append(row.get("lang") or "en")
    return dict(out)


def batch_item_lang_counts(
    product_ids: list[int],
    *,
    query_fn: QueryFn | None = None,
    list_rows_fn=list_product_item_lang_count_rows,
) -> tuple[dict[int, dict[str, int]], dict[int, int]]:
    if not product_ids:
        return {}, {}
    if query_fn is not None and list_rows_fn is list_product_item_lang_count_rows:
        rows = list_rows_fn(product_ids, query_func=query_fn)
    else:
        rows = list_rows_fn(product_ids)
    per_lang: dict[int, dict[str, int]] = defaultdict(dict)
    totals: dict[int, int] = defaultdict(int)
    for row in rows or []:
        pid = int(row["product_id"])
        lang = row.get("lang") or "en"
        cnt = int(row.get("c") or 0)
        per_lang[pid][lang] = cnt
        totals[pid] += cnt
    return dict(per_lang), dict(totals)


def _parse_positive_int(raw: str | None, *, default: int, upper_bound: int | None = None) -> int:
    try:
        value = max(1, int(raw or default))
    except (TypeError, ValueError):
        value = default
    if upper_bound is not None:
        value = min(upper_bound, value)
    return value


def build_materials_list_response(
    *,
    page_raw: str | None,
    page_size_raw: str | None,
    q: str | None,
    archived_raw: str | None,
    query_fn: QueryFn | None = None,
) -> dict:
    page = _parse_positive_int(page_raw, default=1)
    page_size = _parse_positive_int(
        page_size_raw,
        default=20,
        upper_bound=LIST_PAGE_SIZE_MAX,
    )
    keyword = (q or "").strip()
    archived = parse_archived_filter(archived_raw or "0")

    offset = (page - 1) * page_size
    if query_fn is not None:
        total = count_material_products(keyword=keyword, archived=archived, query_func=query_fn)
        rows = list_material_products(
            keyword=keyword,
            archived=archived,
            limit=page_size,
            offset=offset,
            query_func=query_fn,
        )
    else:
        total = count_material_products(keyword=keyword, archived=archived)
        rows = list_material_products(
            keyword=keyword,
            archived=archived,
            limit=page_size,
            offset=offset,
        )

    product_ids = [int(row["id"]) for row in rows or []]
    cover_map = batch_cover_langs(product_ids, query_fn=query_fn)
    copy_map = batch_copywriting_langs(product_ids, query_fn=query_fn)
    item_lang_map, item_total_map = batch_item_lang_counts(product_ids, query_fn=query_fn)

    items = []
    for row in rows or []:
        product_id = int(row["id"])
        items.append({
            "id": product_id,
            "product_code": row.get("product_code"),
            "name": row.get("name"),
            "archived": bool(row.get("archived")),
            "ad_supported_langs": row.get("ad_supported_langs") or "",
            "created_at": iso_or_none(row.get("created_at")),
            "updated_at": iso_or_none(row.get("updated_at")),
            "cover_langs": sorted(cover_map.get(product_id, [])),
            "copywriting_langs": sorted(copy_map.get(product_id, [])),
            "item_langs": item_lang_map.get(product_id, {}),
            "total_items": item_total_map.get(product_id, 0),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
