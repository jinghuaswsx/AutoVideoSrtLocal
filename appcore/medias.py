"""素材管理 DAO：产品/文案/素材三张表的增删改查。"""
from __future__ import annotations
from typing import Any
from appcore.db import query, query_one, execute


# ---------- 语种 ----------

def list_languages() -> list[dict]:
    return query(
        "SELECT code, name_zh, sort_order, enabled FROM media_languages "
        "WHERE enabled=1 ORDER BY sort_order ASC, code ASC"
    )


def is_valid_language(code: str) -> bool:
    if not code:
        return False
    row = query_one(
        "SELECT 1 AS ok FROM media_languages WHERE code=%s AND enabled=1",
        (code,),
    )
    return bool(row)


# ---------- 产品 ----------

def create_product(user_id: int, name: str, color_people: str | None = None,
                   source: str | None = None, product_code: str | None = None,
                   cover_object_key: str | None = None) -> int:
    return execute(
        "INSERT INTO media_products "
        "(user_id, name, product_code, color_people, source, cover_object_key) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (user_id, name, product_code, color_people, source, cover_object_key),
    )


def get_product(product_id: int) -> dict | None:
    return query_one(
        "SELECT * FROM media_products WHERE id=%s AND deleted_at IS NULL",
        (product_id,),
    )


def get_product_by_code(code: str) -> dict | None:
    return query_one(
        "SELECT * FROM media_products WHERE product_code=%s AND deleted_at IS NULL",
        (code,),
    )


def list_products(user_id: int | None, keyword: str = "", archived: bool = False,
                  offset: int = 0, limit: int = 20) -> tuple[list[dict], int]:
    where = ["deleted_at IS NULL"]
    args: list[Any] = []
    if user_id is not None:
        where.append("user_id=%s")
        args.append(user_id)
    where.append("archived=%s")
    args.append(1 if archived else 0)
    if keyword:
        where.append("(name LIKE %s OR product_code LIKE %s)")
        like = f"%{keyword}%"
        args.extend([like, like])
    where_sql = " AND ".join(where)

    total_row = query_one(f"SELECT COUNT(*) AS c FROM media_products WHERE {where_sql}", tuple(args))
    total = int((total_row or {}).get("c") or 0)

    rows = query(
        f"SELECT * FROM media_products WHERE {where_sql} "
        "ORDER BY updated_at DESC LIMIT %s OFFSET %s",
        tuple(args + [limit, offset]),
    )
    return rows, total


def update_product(product_id: int, **fields) -> int:
    allowed = {"name", "color_people", "source", "archived",
               "importance", "trend_score", "selling_points",
               "product_code", "cover_object_key"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return 0
    set_sql = ", ".join(f"{k}=%s" for k in keys)
    args = tuple(fields[k] for k in keys) + (product_id,)
    return execute(f"UPDATE media_products SET {set_sql} WHERE id=%s", args)


def soft_delete_product(product_id: int) -> int:
    execute("UPDATE media_items SET deleted_at=NOW() WHERE product_id=%s AND deleted_at IS NULL",
            (product_id,))
    return execute("UPDATE media_products SET deleted_at=NOW() WHERE id=%s", (product_id,))


# ---------- 文案 ----------

def list_copywritings(product_id: int, lang: str | None = None) -> list[dict]:
    if lang:
        return query(
            "SELECT * FROM media_copywritings "
            "WHERE product_id=%s AND lang=%s ORDER BY idx ASC, id ASC",
            (product_id, lang),
        )
    return query(
        "SELECT * FROM media_copywritings WHERE product_id=%s "
        "ORDER BY lang ASC, idx ASC, id ASC",
        (product_id,),
    )


def replace_copywritings(product_id: int, items: list[dict], lang: str = "en") -> None:
    """整体替换某语种的文案列表。"""
    execute(
        "DELETE FROM media_copywritings WHERE product_id=%s AND lang=%s",
        (product_id, lang),
    )
    for idx, item in enumerate(items, start=1):
        execute(
            "INSERT INTO media_copywritings "
            "(product_id, lang, idx, title, body, description, ad_carrier, ad_copy, ad_keywords) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (product_id, lang, idx,
             item.get("title"), item.get("body"), item.get("description"),
             item.get("ad_carrier"), item.get("ad_copy"), item.get("ad_keywords")),
        )


# ---------- 素材 ----------

def create_item(product_id: int, user_id: int, filename: str, object_key: str,
                display_name: str | None = None, file_url: str | None = None,
                thumbnail_path: str | None = None, duration_seconds: float | None = None,
                file_size: int | None = None,
                cover_object_key: str | None = None,
                lang: str = "en") -> int:
    return execute(
        "INSERT INTO media_items "
        "(product_id, lang, user_id, filename, display_name, object_key, file_url, "
        " thumbnail_path, cover_object_key, duration_seconds, file_size) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (product_id, lang, user_id, filename, display_name or filename, object_key,
         file_url, thumbnail_path, cover_object_key, duration_seconds, file_size),
    )


def update_item_cover(item_id: int, cover_object_key: str | None) -> int:
    return execute(
        "UPDATE media_items SET cover_object_key=%s WHERE id=%s",
        (cover_object_key, item_id),
    )


def list_items(product_id: int, lang: str | None = None) -> list[dict]:
    if lang:
        return query(
            "SELECT * FROM media_items "
            "WHERE product_id=%s AND lang=%s AND deleted_at IS NULL "
            "ORDER BY sort_order ASC, id ASC",
            (product_id, lang),
        )
    return query(
        "SELECT * FROM media_items WHERE product_id=%s AND deleted_at IS NULL "
        "ORDER BY sort_order ASC, id ASC",
        (product_id,),
    )


def get_item(item_id: int) -> dict | None:
    return query_one(
        "SELECT * FROM media_items WHERE id=%s AND deleted_at IS NULL",
        (item_id,),
    )


def soft_delete_item(item_id: int) -> int:
    return execute("UPDATE media_items SET deleted_at=NOW() WHERE id=%s", (item_id,))


def count_items_by_product(product_ids: list[int]) -> dict[int, int]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT product_id, COUNT(*) AS c FROM media_items "
        f"WHERE product_id IN ({placeholders}) AND deleted_at IS NULL "
        f"GROUP BY product_id",
        tuple(product_ids),
    )
    return {int(r["product_id"]): int(r["c"]) for r in rows}


def first_thumb_item_by_product(product_ids: list[int]) -> dict[int, int]:
    """每个产品下最早一张有缩略图的素材 id。"""
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT product_id, MIN(id) AS item_id FROM media_items "
        f"WHERE product_id IN ({placeholders}) AND deleted_at IS NULL "
        f"  AND thumbnail_path IS NOT NULL AND thumbnail_path <> '' "
        f"GROUP BY product_id",
        tuple(product_ids),
    )
    return {int(r["product_id"]): int(r["item_id"]) for r in rows}


def list_item_filenames_by_product(product_ids: list[int], limit_per: int = 5) -> dict[int, list[str]]:
    """每个产品下前 limit_per 条素材文件名（用于列表行展示）。"""
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT product_id, filename, display_name FROM media_items "
        f"WHERE product_id IN ({placeholders}) AND deleted_at IS NULL "
        f"ORDER BY product_id, sort_order ASC, id ASC",
        tuple(product_ids),
    )
    out: dict[int, list[str]] = {}
    for r in rows:
        pid = int(r["product_id"])
        bucket = out.setdefault(pid, [])
        if len(bucket) < limit_per:
            bucket.append(r.get("display_name") or r["filename"])
    return out


# ---------- 产品主图（per-lang） ----------

def set_product_cover(product_id: int, lang: str, object_key: str) -> None:
    execute(
        "INSERT INTO media_product_covers (product_id, lang, object_key) "
        "VALUES (%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE object_key=VALUES(object_key)",
        (product_id, lang, object_key),
    )


def delete_product_cover(product_id: int, lang: str) -> int:
    return execute(
        "DELETE FROM media_product_covers WHERE product_id=%s AND lang=%s",
        (product_id, lang),
    )


def get_product_covers(product_id: int) -> dict[str, str]:
    rows = query(
        "SELECT lang, object_key FROM media_product_covers WHERE product_id=%s",
        (product_id,),
    )
    return {r["lang"]: r["object_key"] for r in rows}


def resolve_cover(product_id: int, lang: str) -> str | None:
    """返回该语种主图；缺失时回退到 en；都没有返回 None。"""
    covers = get_product_covers(product_id)
    return covers.get(lang) or covers.get("en")


def has_english_cover(product_id: int) -> bool:
    row = query_one(
        "SELECT 1 AS ok FROM media_product_covers WHERE product_id=%s AND lang='en'",
        (product_id,),
    )
    return bool(row)


def get_product_covers_batch(product_ids: list[int]) -> dict[int, dict[str, str]]:
    """批量返回 { product_id: {lang: object_key} }。"""
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT product_id, lang, object_key FROM media_product_covers "
        f"WHERE product_id IN ({placeholders})",
        tuple(product_ids),
    )
    out: dict[int, dict[str, str]] = {pid: {} for pid in product_ids}
    for r in rows:
        out[int(r["product_id"])][r["lang"]] = r["object_key"]
    return out


# ---------- 覆盖度统计 ----------

def lang_coverage_by_product(product_ids: list[int]) -> dict[int, dict[str, dict]]:
    """返回 { pid: { lang: {items, copy, cover} } }，仅包含当前启用的语种。

    已禁用语种（media_languages.enabled=0）下的存量 items/copywritings/covers
    会被忽略，不计入任何语种桶。
    """
    if not product_ids:
        return {}
    langs = [l["code"] for l in list_languages()]
    placeholders = ",".join(["%s"] * len(product_ids))

    item_rows = query(
        f"SELECT product_id, lang, COUNT(*) AS c FROM media_items "
        f"WHERE product_id IN ({placeholders}) AND deleted_at IS NULL "
        f"GROUP BY product_id, lang",
        tuple(product_ids),
    )
    copy_rows = query(
        f"SELECT product_id, lang, COUNT(*) AS c FROM media_copywritings "
        f"WHERE product_id IN ({placeholders}) "
        f"GROUP BY product_id, lang",
        tuple(product_ids),
    )
    cover_rows = query(
        f"SELECT product_id, lang FROM media_product_covers "
        f"WHERE product_id IN ({placeholders})",
        tuple(product_ids),
    )

    out: dict[int, dict[str, dict]] = {
        pid: {lang: {"items": 0, "copy": 0, "cover": False} for lang in langs}
        for pid in product_ids
    }
    for r in item_rows:
        pid = int(r["product_id"])
        lang = r["lang"]
        if pid in out and lang in out[pid]:
            out[pid][lang]["items"] = int(r["c"])
    for r in copy_rows:
        pid = int(r["product_id"])
        lang = r["lang"]
        if pid in out and lang in out[pid]:
            out[pid][lang]["copy"] = int(r["c"])
    for r in cover_rows:
        pid = int(r["product_id"])
        lang = r["lang"]
        if pid in out and lang in out[pid]:
            out[pid][lang]["cover"] = True
    return out
