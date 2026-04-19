"""素材管理 DAO：产品/文案/素材三张表的增删改查。"""
from __future__ import annotations
import re
from typing import Any
from appcore.db import query, query_one, execute


# ---------- 语种 ----------

_LANG_CODE_RE = re.compile(r"^[a-z0-9-]{2,8}$")


def normalize_language_code(code: str) -> str:
    normalized = (code or "").strip().lower()
    if not _LANG_CODE_RE.match(normalized):
        raise ValueError("语言编码格式不合法")
    return normalized


def get_language(code: str) -> dict | None:
    return query_one(
        "SELECT code, name_zh, sort_order, enabled FROM media_languages WHERE code=%s",
        (code,),
    )


def list_languages() -> list[dict]:
    return query(
        "SELECT code, name_zh, sort_order, enabled FROM media_languages "
        "WHERE enabled=1 ORDER BY sort_order ASC, code ASC"
    )


def list_enabled_language_codes() -> list[str]:
    """返回所有启用语种的 code 列表，按 sort_order ASC, code ASC 排序。"""
    rows = query(
        "SELECT code FROM media_languages "
        "WHERE enabled=1 ORDER BY sort_order ASC, code ASC"
    )
    return [row["code"] for row in rows]


def list_enabled_languages_kv() -> list[tuple[str, str]]:
    """返回所有启用语种的 (code, name_zh) 二元组列表，供前端下拉选项使用。"""
    rows = query(
        "SELECT code, name_zh FROM media_languages "
        "WHERE enabled=1 ORDER BY sort_order ASC, code ASC"
    )
    return [(r["code"], r["name_zh"]) for r in rows]


def get_language_usage(code: str) -> dict:
    item_row = query_one(
        "SELECT COUNT(*) AS c FROM media_items WHERE lang=%s AND deleted_at IS NULL",
        (code,),
    ) or {}
    copy_row = query_one(
        "SELECT COUNT(*) AS c FROM media_copywritings WHERE lang=%s",
        (code,),
    ) or {}
    cover_row = query_one(
        "SELECT COUNT(*) AS c FROM media_product_covers WHERE lang=%s",
        (code,),
    ) or {}
    items_count = int(item_row.get("c") or 0)
    copy_count = int(copy_row.get("c") or 0)
    cover_count = int(cover_row.get("c") or 0)
    return {
        "items_count": items_count,
        "copy_count": copy_count,
        "cover_count": cover_count,
        "in_use": any((items_count, copy_count, cover_count)),
    }


def list_languages_for_admin() -> list[dict]:
    rows = query(
        "SELECT code, name_zh, sort_order, enabled FROM media_languages "
        "ORDER BY sort_order ASC, code ASC"
    )
    return [{**row, **get_language_usage(row["code"])} for row in rows]


def is_valid_language(code: str) -> bool:
    if not code:
        return False
    row = query_one(
        "SELECT 1 AS ok FROM media_languages WHERE code=%s AND enabled=1",
        (code,),
    )
    return bool(row)


def create_language(code: str, name_zh: str, sort_order: int, enabled: bool) -> None:
    normalized = normalize_language_code(code)
    if get_language(normalized):
        raise ValueError("语言编码已存在")
    display_name = (name_zh or "").strip()
    if not display_name:
        raise ValueError("语言名称不能为空")
    execute(
        "INSERT INTO media_languages (code, name_zh, sort_order, enabled) "
        "VALUES (%s,%s,%s,%s)",
        (normalized, display_name, int(sort_order), 1 if enabled else 0),
    )


def validate_language_update(code: str, enabled: bool | None = None) -> None:
    normalized = normalize_language_code(code)
    if normalized == "en" and enabled is False:
        raise ValueError("默认语种 en 不能停用")


def update_language(code: str, name_zh: str, sort_order: int, enabled: bool) -> None:
    normalized = normalize_language_code(code)
    validate_language_update(normalized, enabled=enabled)
    display_name = (name_zh or "").strip()
    if not display_name:
        raise ValueError("语言名称不能为空")
    execute(
        "UPDATE media_languages SET name_zh=%s, sort_order=%s, enabled=%s WHERE code=%s",
        (display_name, int(sort_order), 1 if enabled else 0, normalized),
    )


def delete_language(code: str) -> None:
    normalized = normalize_language_code(code)
    if normalized == "en":
        raise ValueError("默认语种 en 不能删除")
    usage = get_language_usage(normalized)
    if usage["in_use"]:
        raise ValueError("该语种已有关联数据，只能停用")
    execute("DELETE FROM media_languages WHERE code=%s", (normalized,))


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
    import json as _json
    allowed = {"name", "color_people", "source", "archived",
               "importance", "trend_score", "selling_points",
               "product_code", "cover_object_key",
               "localized_links_json", "ad_supported_langs",
               "link_check_tasks_json"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return 0
    # localized_links_json：支持 dict 输入，自动序列化为 JSON 字符串
    def _val(k):
        v = fields[k]
        if k in {"localized_links_json", "link_check_tasks_json"} and isinstance(v, dict):
            return _json.dumps(v, ensure_ascii=False)
        return v
    set_sql = ", ".join(f"{k}=%s" for k in keys)
    args = tuple(_val(k) for k in keys) + (product_id,)
    return execute(f"UPDATE media_products SET {set_sql} WHERE id=%s", args)


def soft_delete_product(product_id: int) -> int:
    execute("UPDATE media_items SET deleted_at=NOW() WHERE product_id=%s AND deleted_at IS NULL",
            (product_id,))
    return execute("UPDATE media_products SET deleted_at=NOW() WHERE id=%s", (product_id,))


def parse_ad_supported_langs(value: str | None) -> list[str]:
    """将 'de,fr,ja' 类逗号字符串规范化为 ['de','fr','ja']。空串 / None 返回 []。"""
    if not value:
        return []
    return [p.strip().lower() for p in value.split(",") if p.strip()]


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
    """整体替换某语种的文案列表。

    如果传入 item 只带 body(前端编辑弹窗的行为),按 idx 匹配现有记录保留:
      - 其他文本字段(title/description/ad_*)
      - 自动翻译关联(source_ref_id/bulk_task_id/auto_translated)

    如果用户修改了 body 且该行原本是 auto_translated=1,
    则把 manually_edited_at 设为 NOW() 标记为"已人工修改"。
    """
    existing = {
        row["idx"]: row for row in query(
            "SELECT * FROM media_copywritings WHERE product_id=%s AND lang=%s",
            (product_id, lang),
        )
    }

    execute(
        "DELETE FROM media_copywritings WHERE product_id=%s AND lang=%s",
        (product_id, lang),
    )
    for idx, item in enumerate(items, start=1):
        prev = existing.get(idx)
        # 优先用新 item 的字段,缺字段时回退到旧记录
        def pick(field: str):
            return item.get(field) if field in item else (prev.get(field) if prev else None)

        new_body = item.get("body") if "body" in item else (prev.get("body") if prev else None)
        source_ref_id = prev.get("source_ref_id") if prev else None
        bulk_task_id = prev.get("bulk_task_id") if prev else None
        auto_translated = prev.get("auto_translated") if prev else 0
        manually_edited_at = prev.get("manually_edited_at") if prev else None

        # 如果 body 被用户改了,且原记录是自动翻译的 → 打上"已人工修改"
        body_changed = prev is not None and (prev.get("body") or "") != (new_body or "")
        if body_changed and auto_translated:
            execute(
                "INSERT INTO media_copywritings "
                "(product_id, lang, idx, title, body, description, ad_carrier, ad_copy, ad_keywords, "
                " source_ref_id, bulk_task_id, auto_translated, manually_edited_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s, NOW())",
                (product_id, lang, idx,
                 pick("title"), new_body, pick("description"),
                 pick("ad_carrier"), pick("ad_copy"), pick("ad_keywords"),
                 source_ref_id, bulk_task_id, auto_translated),
            )
        else:
            execute(
                "INSERT INTO media_copywritings "
                "(product_id, lang, idx, title, body, description, ad_carrier, ad_copy, ad_keywords, "
                " source_ref_id, bulk_task_id, auto_translated, manually_edited_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s)",
                (product_id, lang, idx,
                 pick("title"), new_body, pick("description"),
                 pick("ad_carrier"), pick("ad_copy"), pick("ad_keywords"),
                 source_ref_id, bulk_task_id, auto_translated, manually_edited_at),
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


def collect_media_object_references() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    rows.extend(query(
        "SELECT source, object_key FROM ("
        " SELECT 'item' AS source, object_key AS object_key"
        " FROM media_items WHERE deleted_at IS NULL"
        " UNION ALL"
        " SELECT 'item_cover' AS source, cover_object_key AS object_key"
        " FROM media_items WHERE deleted_at IS NULL"
        ") refs"
    ))
    rows.extend(query(
        "SELECT 'product_cover' AS source, object_key "
        "FROM media_product_covers"
    ))
    rows.extend(query(
        "SELECT 'legacy_product_cover' AS source, cover_object_key AS object_key "
        "FROM media_products WHERE deleted_at IS NULL"
    ))
    rows.extend(query(
        "SELECT 'product_detail_image' AS source, object_key "
        "FROM media_product_detail_images WHERE deleted_at IS NULL"
    ))

    grouped: dict[str, set[str]] = {}
    for row in rows:
        key = str((row or {}).get("object_key") or "").strip()
        if not key:
            continue
        grouped.setdefault(key, set()).add(str((row or {}).get("source") or "unknown"))

    return [
        {"object_key": key, "sources": sorted(sources)}
        for key, sources in sorted(grouped.items())
    ]


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


# ---------- 商品详情图 ----------

def _next_detail_image_sort_order(product_id: int, lang: str) -> int:
    row = query_one(
        "SELECT COALESCE(MAX(sort_order), -1) AS m "
        "FROM media_product_detail_images "
        "WHERE product_id=%s AND lang=%s AND deleted_at IS NULL",
        (product_id, lang),
    ) or {}
    return int(row.get("m") or -1) + 1


def add_detail_image(
    product_id: int,
    lang: str,
    object_key: str,
    *,
    content_type: str | None = None,
    file_size: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> int:
    sort_order = _next_detail_image_sort_order(product_id, lang)
    return execute(
        "INSERT INTO media_product_detail_images "
        "(product_id, lang, sort_order, object_key, content_type, file_size, width, height) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (product_id, lang, sort_order, object_key, content_type, file_size, width, height),
    )


def list_detail_images(product_id: int, lang: str) -> list[dict]:
    return query(
        "SELECT id, product_id, lang, sort_order, object_key, "
        "  content_type, file_size, width, height, created_at "
        "FROM media_product_detail_images "
        "WHERE product_id=%s AND lang=%s AND deleted_at IS NULL "
        "ORDER BY sort_order ASC, id ASC",
        (product_id, lang),
    )


def get_detail_image(image_id: int) -> dict | None:
    return query_one(
        "SELECT id, product_id, lang, sort_order, object_key, "
        "  content_type, file_size, width, height, created_at, deleted_at "
        "FROM media_product_detail_images WHERE id=%s",
        (image_id,),
    )


def soft_delete_detail_image(image_id: int) -> int:
    return execute(
        "UPDATE media_product_detail_images "
        "SET deleted_at=NOW() WHERE id=%s AND deleted_at IS NULL",
        (image_id,),
    )


def reorder_detail_images(product_id: int, lang: str, ids: list[int]) -> int:
    """按传入顺序更新 sort_order（0 起）。返回更新行数。"""
    if not ids:
        return 0
    updated = 0
    for idx, img_id in enumerate(ids):
        updated += execute(
            "UPDATE media_product_detail_images "
            "SET sort_order=%s WHERE id=%s AND product_id=%s AND lang=%s",
            (idx, int(img_id), product_id, lang),
        )
    return updated


def parse_link_check_tasks_json(value: str | dict | None) -> dict:
    """Normalize link_check_tasks_json to a dict."""
    import json as _json

    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = _json.loads(value)
    except (_json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_product_link_check_tasks(product_id: int) -> dict:
    row = get_product(product_id) or {}
    return parse_link_check_tasks_json(row.get("link_check_tasks_json"))


def set_product_link_check_task(product_id: int, lang: str, payload: dict | None) -> int:
    tasks = get_product_link_check_tasks(product_id)
    if payload:
        tasks[lang] = payload
    else:
        tasks.pop(lang, None)
    return update_product(product_id, link_check_tasks_json=(tasks or None))
