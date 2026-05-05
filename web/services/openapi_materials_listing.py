"""Listing aggregation helpers for OpenAPI materials routes."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from appcore.db import query as db_query


QueryFn = Callable[[str, tuple], list[dict]]


def parse_archived_filter(raw: str) -> int | None:
    """Return 0/1 to filter, None for 'all'."""
    value = (raw or "").strip().lower()
    if value == "all":
        return None
    if value == "1":
        return 1
    # 默认只看未归档
    return 0


def batch_cover_langs(product_ids: list[int], *, query_fn: QueryFn = db_query) -> dict[int, list[str]]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query_fn(
        f"SELECT product_id, lang, object_key FROM media_product_covers "
        f"WHERE product_id IN ({placeholders})",
        tuple(product_ids),
    )
    out: dict[int, list[str]] = defaultdict(list)
    for row in rows or []:
        if row.get("object_key"):
            out[int(row["product_id"])].append(row.get("lang") or "en")
    return dict(out)


def batch_copywriting_langs(product_ids: list[int], *, query_fn: QueryFn = db_query) -> dict[int, list[str]]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query_fn(
        f"SELECT DISTINCT product_id, lang FROM media_copywritings "
        f"WHERE product_id IN ({placeholders})",
        tuple(product_ids),
    )
    out: dict[int, list[str]] = defaultdict(list)
    for row in rows or []:
        out[int(row["product_id"])].append(row.get("lang") or "en")
    return dict(out)


def batch_item_lang_counts(
    product_ids: list[int],
    *,
    query_fn: QueryFn = db_query,
) -> tuple[dict[int, dict[str, int]], dict[int, int]]:
    if not product_ids:
        return {}, {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query_fn(
        f"SELECT product_id, lang, COUNT(*) AS c FROM media_items "
        f"WHERE deleted_at IS NULL AND product_id IN ({placeholders}) "
        f"GROUP BY product_id, lang",
        tuple(product_ids),
    )
    per_lang: dict[int, dict[str, int]] = defaultdict(dict)
    totals: dict[int, int] = defaultdict(int)
    for row in rows or []:
        pid = int(row["product_id"])
        lang = row.get("lang") or "en"
        cnt = int(row.get("c") or 0)
        per_lang[pid][lang] = cnt
        totals[pid] += cnt
    return dict(per_lang), dict(totals)
