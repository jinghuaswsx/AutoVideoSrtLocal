"""Shopify 月/周/日维度报表 + 国家列配置 + 产品搜索/可用月份。

由 ``appcore.order_analytics`` package 在 PR 1.6 抽出；函数体逐字符
保留，行为不变。``__init__.py`` 通过显式 re-export 把这里的公开符号
带回 ``appcore.order_analytics`` 命名空间。

跨子模块依赖：``get_monthly_summary`` 调用 ``_count_media_items_by_product``，
当前仍在 ``__init__.py``，PR 1.8 拆出 ``dashboard.py`` 后移过去。本模块通过
``_facade()`` 间接调用，确保拆分顺序不影响 import 链。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any

from ._constants import COUNTRY_TO_LANG, LANG_PRIORITY_COUNTRIES


# DB 入口走 module-level wrapper（与其他 sub-module 同样原理）。
def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def get_conn(*args, **kwargs):
    return _facade().get_conn(*args, **kwargs)


def _load_enabled_lang_codes() -> list[str]:
    """读取 media_languages.enabled=1 的语种 code，按 sort_order 升序。

    与 appcore.medias.list_enabled_language_codes() 等价；放在本模块里独立维护，
    便于单测通过 monkeypatch.setattr(oa, "_load_enabled_lang_codes", …) 替换实现，
    而不必污染 appcore.medias。
    """
    rows = query(
        "SELECT code FROM media_languages "
        "WHERE enabled=1 ORDER BY sort_order ASC, code ASC"
    )
    return [r["code"] for r in rows]


def get_enabled_country_columns() -> list[dict]:
    """根据 media_languages 启用语种推导出"国家列"序列。

    返回列表如 [{"country": "US", "lang": "en"}, …]，按
    sort_order(语种) → LANG_PRIORITY_COUNTRIES(同语种内部顺序) 双重排序。
    未在 COUNTRY_TO_LANG 里出现的启用语种被静默跳过（不报错）。
    """
    # 走 facade 让 monkeypatch.setattr(oa, "_load_enabled_lang_codes", fake) 透传
    enabled_langs = _facade()._load_enabled_lang_codes()
    columns: list[dict] = []
    seen: set[str] = set()

    # 反向构建：lang → [country, ...]，对未在优先表里的语种走 dict 插入序
    lang_to_countries: dict[str, list[str]] = {}
    for country, lang in COUNTRY_TO_LANG.items():
        lang_to_countries.setdefault(lang, []).append(country)
    # 优先表覆盖默认顺序
    for lang, ordered in LANG_PRIORITY_COUNTRIES.items():
        if lang in lang_to_countries:
            lang_to_countries[lang] = ordered

    for lang in enabled_langs:
        countries = lang_to_countries.get(lang)
        if not countries:
            continue
        for country in countries:
            if country in seen:
                continue
            seen.add(country)
            columns.append({"country": country, "lang": lang})

    return columns


# ── 分析查询 ───────────────────────────────────────────

def _month_range(year: int, month: int) -> tuple[str, str]:
    """返回 (start, end) 字符串用于 WHERE created_at_order >= start AND < end。"""
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    return start, end


def get_monthly_summary(year: int, month: int, product_id: int | None = None) -> dict:
    """月度汇总：按产品 × 国家。"""
    start, end = _month_range(year, month)
    extra_filter = ""
    args: list[Any] = [start, end]
    if product_id is not None:
        extra_filter = "AND so.product_id = %s"
        args.append(product_id)

    # 按产品汇总
    products = query(
        f"SELECT so.product_id, "
        f"COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
        f"mp.product_code, "
        f"SUM(so.lineitem_quantity) AS total_qty, "
        f"COUNT(DISTINCT so.shopify_order_id) AS order_count, "
        f"SUM(COALESCE(so.lineitem_price,0) * so.lineitem_quantity) AS total_revenue "
        f"FROM shopify_orders so "
        f"LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
        f"LEFT JOIN media_products mp ON mp.id = so.product_id "
        f"WHERE so.created_at_order >= %s AND so.created_at_order < %s {extra_filter} "
        f"GROUP BY so.product_id, display_name, mp.product_code "
        f"ORDER BY total_qty DESC",
        tuple(args),
    )

    # 按国家汇总
    countries = query(
        f"SELECT billing_country, "
        f"SUM(lineitem_quantity) AS total_qty, "
        f"COUNT(DISTINCT shopify_order_id) AS order_count "
        f"FROM shopify_orders "
        f"WHERE created_at_order >= %s AND created_at_order < %s {extra_filter} "
        f"GROUP BY billing_country ORDER BY total_qty DESC",
        tuple(args),
    )

    # 产品 × 国家矩阵
    matrix_rows = query(
        f"SELECT so.product_id, "
        f"COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
        f"so.billing_country, "
        f"SUM(so.lineitem_quantity) AS total_qty "
        f"FROM shopify_orders so "
        f"LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
        f"LEFT JOIN media_products mp ON mp.id = so.product_id "
        f"WHERE so.created_at_order >= %s AND so.created_at_order < %s {extra_filter} "
        f"GROUP BY so.product_id, display_name, so.billing_country "
        f"ORDER BY display_name, total_qty DESC",
        tuple(args),
    )

    # 组装矩阵
    country_list = [c["billing_country"] or "未知" for c in countries]
    matrix: dict[str, dict[str, int]] = {}
    product_order: list[str] = []
    for mr in matrix_rows:
        dn = mr["display_name"] or "未知"
        if dn not in matrix:
            matrix[dn] = {}
            product_order.append(dn)
        matrix[dn][mr["billing_country"] or "未知"] = mr["total_qty"]

    # 素材数量：按 product × lang，复用 dashboard 已有的统计逻辑
    # 走 facade 让 PR 1.8 把 _count_media_items_by_product 搬到 dashboard.py 后仍可用
    media_counts_all = _facade()._count_media_items_by_product()
    if product_id is not None:
        media_counts = (
            {product_id: media_counts_all[product_id]}
            if product_id in media_counts_all
            else {}
        )
    else:
        # 仅保留本次查询里出现的产品，避免响应膨胀
        active_pids = {p["product_id"] for p in products if p.get("product_id") is not None}
        media_counts = {
            pid: counts for pid, counts in media_counts_all.items() if pid in active_pids
        }

    country_columns = get_enabled_country_columns()

    return {
        "products": products,
        "countries": countries,
        "country_list": country_list,
        "matrix": matrix,
        "product_order": product_order,
        "country_columns": country_columns,
        "media_counts": media_counts,
    }


def get_product_country_detail(product_id: int, year: int, month: int) -> list[dict]:
    """单个产品在指定月份的"国家×素材×订单"明细。

    覆盖所有启用国家，即使该国当月 0 单 0 素材，也会输出一行（值全 0）。

    返回每行字段：country / lang / qty / orders / revenue / media_count
    """
    start, end = _month_range(year, month)

    # 该产品在月份内的国家汇总
    rows = query(
        "SELECT so.billing_country, "
        "SUM(so.lineitem_quantity) AS qty, "
        "COUNT(DISTINCT so.shopify_order_id) AS orders, "
        "SUM(COALESCE(so.lineitem_price, 0) * so.lineitem_quantity) AS revenue "
        "FROM shopify_orders so "
        "WHERE so.product_id = %s "
        "AND so.created_at_order >= %s AND so.created_at_order < %s "
        "GROUP BY so.billing_country",
        (product_id, start, end),
    )
    by_country: dict[str, dict] = {}
    for r in rows:
        country = r.get("billing_country") or ""
        by_country[country] = {
            "qty": int(r.get("qty") or 0),
            "orders": int(r.get("orders") or 0),
            "revenue": float(r.get("revenue") or 0),
        }

    # 该产品的素材语种分布
    media_rows = query(
        "SELECT lang, COUNT(*) AS n FROM media_items "
        "WHERE product_id = %s AND deleted_at IS NULL "
        "GROUP BY lang",
        (product_id,),
    )
    media_by_lang: dict[str, int] = {}
    for r in media_rows:
        lang = r.get("lang") or ""
        media_by_lang[lang] = int(r.get("n") or 0)

    out: list[dict] = []
    for col in get_enabled_country_columns():
        country = col["country"]
        lang = col["lang"]
        order_data = by_country.get(country, {})
        out.append({
            "country": country,
            "lang": lang,
            "qty": order_data.get("qty", 0),
            "orders": order_data.get("orders", 0),
            "revenue": round(order_data.get("revenue", 0.0), 2),
            "media_count": media_by_lang.get(lang, 0),
        })
    return out


def get_daily_detail(year: int, month: int, product_id: int | None = None) -> list[dict]:
    """每日明细：按日期 × 产品 × 国家。"""
    start, end = _month_range(year, month)
    extra_filter = ""
    args: list[Any] = [start, end]
    if product_id is not None:
        extra_filter = "AND so.product_id = %s"
        args.append(product_id)

    return query(
        f"SELECT DATE(so.created_at_order) AS sale_date, "
        f"so.product_id, "
        f"COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
        f"so.billing_country, "
        f"SUM(so.lineitem_quantity) AS total_qty, "
        f"COUNT(DISTINCT so.shopify_order_id) AS order_count "
        f"FROM shopify_orders so "
        f"LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
        f"LEFT JOIN media_products mp ON mp.id = so.product_id "
        f"WHERE so.created_at_order >= %s AND so.created_at_order < %s {extra_filter} "
        f"GROUP BY sale_date, so.product_id, display_name, so.billing_country "
        f"ORDER BY sale_date ASC, total_qty DESC",
        tuple(args),
    )


def get_weekly_summary(year: int, week: int) -> dict:
    """周汇总：按 ISO 周。"""
    target = f"{year:04d}{week:02d}"
    products = query(
        "SELECT so.product_id, "
        "COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
        "SUM(so.lineitem_quantity) AS total_qty, "
        "COUNT(DISTINCT so.shopify_order_id) AS order_count "
        "FROM shopify_orders so "
        "LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
        "LEFT JOIN media_products mp ON mp.id = so.product_id "
        "WHERE YEARWEEK(so.created_at_order, 1) = %s "
        "GROUP BY so.product_id, display_name ORDER BY total_qty DESC",
        (target,),
    )
    countries = query(
        "SELECT billing_country, SUM(lineitem_quantity) AS total_qty "
        "FROM shopify_orders "
        "WHERE YEARWEEK(created_at_order, 1) = %s "
        "GROUP BY billing_country ORDER BY total_qty DESC",
        (target,),
    )
    return {"products": products, "countries": countries}


def search_products(q: str) -> list[dict]:
    """按产品 ID 或标题搜索。"""
    like = f"%{q}%"
    # 尝试将 q 解析为数字（product_id）
    try:
        pid = int(q)
    except ValueError:
        pid = None

    if pid is not None:
        return query(
            "SELECT DISTINCT so.product_id, "
            "COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
            "mp.product_code "
            "FROM shopify_orders so "
            "LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
            "LEFT JOIN media_products mp ON mp.id = so.product_id "
            "WHERE so.product_id = %s OR so.lineitem_name LIKE %s "
            "LIMIT 50",
            (pid, like),
        )
    return query(
        "SELECT DISTINCT so.product_id, "
        "COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
        "mp.product_code "
        "FROM shopify_orders so "
        "LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
        "LEFT JOIN media_products mp ON mp.id = so.product_id "
        "WHERE so.lineitem_name LIKE %s OR ptc.page_title LIKE %s "
        "LIMIT 50",
        (like, like),
    )


def get_available_months() -> list[dict]:
    """返回有数据的年月列表。"""
    return query(
        "SELECT YEAR(created_at_order) AS y, MONTH(created_at_order) AS m, "
        "COUNT(*) AS row_count "
        "FROM shopify_orders "
        "GROUP BY YEAR(created_at_order), MONTH(created_at_order) "
        "ORDER BY y DESC, m DESC"
    )
