from __future__ import annotations

from collections.abc import Callable

from appcore.db import query, query_one

QueryFunc = Callable[[str, tuple], list[dict]]
QueryOneFunc = Callable[[str, tuple], dict | None]


def _placeholders(values: list[int]) -> str:
    return ",".join(["%s"] * len(values))


def list_product_cover_lang_rows(
    product_ids: list[int],
    *,
    query_func: QueryFunc = query,
) -> list[dict]:
    if not product_ids:
        return []
    placeholders = _placeholders(product_ids)
    return query_func(
        f"SELECT product_id, lang, object_key FROM media_product_covers "
        f"WHERE product_id IN ({placeholders})",
        tuple(product_ids),
    )


def list_product_copywriting_lang_rows(
    product_ids: list[int],
    *,
    query_func: QueryFunc = query,
) -> list[dict]:
    if not product_ids:
        return []
    placeholders = _placeholders(product_ids)
    return query_func(
        f"SELECT DISTINCT product_id, lang FROM media_copywritings "
        f"WHERE product_id IN ({placeholders})",
        tuple(product_ids),
    )


def list_product_item_lang_count_rows(
    product_ids: list[int],
    *,
    query_func: QueryFunc = query,
) -> list[dict]:
    if not product_ids:
        return []
    placeholders = _placeholders(product_ids)
    return query_func(
        f"SELECT product_id, lang, COUNT(*) AS c FROM media_items "
        f"WHERE deleted_at IS NULL AND product_id IN ({placeholders}) "
        f"GROUP BY product_id, lang",
        tuple(product_ids),
    )


def _material_products_filter(keyword: str, archived: int | None) -> tuple[str, list[object]]:
    where = ["deleted_at IS NULL"]
    args: list[object] = []
    if archived is not None:
        where.append("archived=%s")
        args.append(archived)
    if keyword:
        where.append("(name LIKE %s OR product_code LIKE %s)")
        like = f"%{keyword}%"
        args.extend([like, like])
    return " AND ".join(where), args


def count_material_products(
    *,
    keyword: str,
    archived: int | None,
    query_func: QueryFunc = query,
) -> int:
    where_sql, args = _material_products_filter(keyword, archived)
    rows = query_func(
        f"SELECT COUNT(*) AS c FROM media_products WHERE {where_sql}",
        tuple(args),
    )
    return int((rows[0] if rows else {}).get("c") or 0)


def list_material_products(
    *,
    keyword: str,
    archived: int | None,
    limit: int,
    offset: int,
    query_func: QueryFunc = query,
) -> list[dict]:
    where_sql, args = _material_products_filter(keyword, archived)
    return query_func(
        f"SELECT id, product_code, name, archived, ad_supported_langs, "
        f"       created_at, updated_at "
        f"FROM media_products WHERE {where_sql} "
        f"ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
        tuple(args + [limit, offset]),
    )


def get_push_log_summary(
    log_id: int,
    *,
    query_one_func: QueryOneFunc = query_one,
) -> dict | None:
    return query_one_func(
        "SELECT status, error_message, created_at FROM media_push_logs WHERE id=%s",
        (log_id,),
    )
