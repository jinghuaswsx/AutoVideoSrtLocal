"""订单分析 DAO 层：Shopify 订单导入、产品匹配、数据分析查询。"""
from __future__ import annotations

import csv
import io
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any

import requests

from appcore.db import query, query_one, execute, get_conn

log = logging.getLogger(__name__)

# Shopify CSV 列名映射
_SHOPIFY_COLS = {
    "Id":                   "shopify_order_id",
    "Name":                 "order_name",
    "Created at":           "created_at_order",
    "Lineitem name":        "lineitem_name",
    "Lineitem sku":         "lineitem_sku",
    "Lineitem quantity":    "lineitem_quantity",
    "Lineitem price":       "lineitem_price",
    "Billing Country":      "billing_country",
    "Total":                "total",
    "Subtotal":             "subtotal",
    "Shipping":             "shipping",
    "Currency":             "currency",
    "Financial Status":     "financial_status",
    "Fulfillment Status":   "fulfillment_status",
    "Vendor":               "vendor",
}

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SHOP_TS_FMT = "%Y-%m-%d %H:%M:%S %z"  # "2026-04-22 23:00:14 -0700"


# ── 解析 ───────────────────────────────────────────────

def parse_shopify_file(file_stream, filename: str) -> list[dict]:
    """解析 CSV 或 Excel 文件，返回原始行 dict 列表。"""
    filename = filename.lower()
    if filename.endswith(".csv"):
        text = file_stream.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))
    elif filename.endswith((".xls", ".xlsx")):
        return _parse_excel(file_stream)
    else:
        raise ValueError("仅支持 CSV / Excel (.xlsx) 文件")


def _parse_excel(stream) -> list[dict]:
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("服务器未安装 openpyxl，无法解析 Excel 文件")
    wb = openpyxl.load_workbook(stream, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers = next(rows_iter)
    if not headers:
        return []
    headers = [str(h).strip() if h else "" for h in headers]
    result = []
    for row in rows_iter:
        d = {}
        for i, val in enumerate(row):
            if i < len(headers) and headers[i]:
                d[headers[i]] = str(val) if val is not None else ""
        result.append(d)
    wb.close()
    return result


def _parse_shopify_ts(ts_str: str) -> datetime | None:
    """解析 Shopify 时间戳 '2026-04-22 23:00:14 -0700' 为 naive UTC-ish datetime。"""
    ts_str = (ts_str or "").strip()
    if not ts_str:
        return None
    try:
        dt = datetime.strptime(ts_str, _SHOP_TS_FMT)
        # 转为 UTC（去掉时区信息）
        return dt.replace(tzinfo=None) - dt.utcoffset()
    except Exception:
        pass
    # fallback: 只取日期时间部分
    try:
        return datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _safe_int(val: str, default: int = 0) -> int:
    try:
        return int(float((val or "").strip()))
    except (ValueError, TypeError):
        return default


def _safe_float(val: str) -> float | None:
    try:
        return float((val or "").strip())
    except (ValueError, TypeError):
        return None


# ── 导入 ───────────────────────────────────────────────

def import_orders(rows: list[dict]) -> dict:
    """将原始行批量写入 shopify_orders，去重。返回 {imported, skipped}。"""
    if not rows:
        return {"imported": 0, "skipped": 0}

    BATCH = 500
    imported = 0
    skipped = 0

    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        values = []
        for row in batch:
            oid_str = (row.get("Id") or "").strip()
            if not oid_str:
                skipped += 1
                continue
            try:
                oid = int(oid_str)
            except ValueError:
                skipped += 1
                continue

            name = (row.get("Lineitem name") or "").strip()
            if not name:
                skipped += 1
                continue

            values.append((
                oid,
                (row.get("Name") or "").strip()[:32] or None,
                _parse_shopify_ts(row.get("Created at", "")),
                name[:500],
                (row.get("Lineitem sku") or "").strip()[:128] or None,
                _safe_int(row.get("Lineitem quantity", "1"), 1),
                _safe_float(row.get("Lineitem price")),
                (row.get("Billing Country") or "").strip()[:8] or None,
                _safe_float(row.get("Total")),
                _safe_float(row.get("Subtotal")),
                _safe_float(row.get("Shipping")),
                (row.get("Currency") or "").strip()[:8] or None,
                (row.get("Financial Status") or "").strip()[:32] or None,
                (row.get("Fulfillment Status") or "").strip()[:32] or None,
                (row.get("Vendor") or "").strip()[:128] or None,
            ))

        if not values:
            continue

        placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(values))
        flat = []
        for v in values:
            flat.extend(v)

        sql = (
            "INSERT IGNORE INTO shopify_orders "
            "(shopify_order_id, order_name, created_at_order, lineitem_name, "
            "lineitem_sku, lineitem_quantity, lineitem_price, billing_country, "
            "total, subtotal, shipping, currency, financial_status, "
            "fulfillment_status, vendor) VALUES " + placeholders
        )
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(flat))
                affected = cur.rowcount
        finally:
            conn.close()
        imported += affected
        skipped += len(values) - affected

    return {"imported": imported, "skipped": skipped}


def get_import_stats() -> dict:
    """返回数据库统计概览。"""
    row = query_one(
        "SELECT COUNT(*) AS total_rows, "
        "MIN(created_at_order) AS min_date, MAX(created_at_order) AS max_date, "
        "COUNT(DISTINCT lineitem_name) AS product_count, "
        "COUNT(DISTINCT billing_country) AS country_count, "
        "SUM(CASE WHEN product_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_rows "
        "FROM shopify_orders"
    )
    return row or {}


# ── 产品标题缓存 ──────────────────────────────────────

def fetch_product_page_title(product_code: str) -> str | None:
    """从英文产品页抓取 <title>。"""
    url = f"https://newjoyloo.com/products/{product_code}"
    try:
        resp = requests.get(url, timeout=8, allow_redirects=True,
                            headers={"User-Agent": "Mozilla/5.0 AutoVideoSrt/1.0"})
        if resp.status_code != 200:
            return None
        m = _TITLE_RE.search(resp.text)
        if m:
            title = m.group(1).strip()
            # 去掉 " | Store Name" / " – Store Name" / " - Store Name" 后缀
            for sep in (" | ", " – ", " — ", " - "):
                if sep in title:
                    title = title.rsplit(sep, 1)[0].strip()
                    break
            # 解码 HTML 实体
            title = (title
                     .replace("&ndash;", "–")
                     .replace("&mdash;", "—")
                     .replace("&amp;", "&")
                     .replace("&lt;", "<")
                     .replace("&gt;", ">")
                     .replace("&quot;", '"'))
            return title[:500] if title else None
    except requests.RequestException as exc:
        log.debug("fetch title failed for %s: %s", product_code, exc)
    return None


def refresh_product_titles(product_ids: list[int] | None = None) -> dict:
    """批量刷新产品标题缓存。返回 {fetched, skipped, errors}。"""
    if product_ids:
        placeholders = ",".join(["%s"] * len(product_ids))
        products = query(
            f"SELECT id, product_code FROM media_products "
            f"WHERE id IN ({placeholders}) AND product_code IS NOT NULL AND deleted_at IS NULL",
            tuple(product_ids),
        )
    else:
        products = query(
            "SELECT mp.id, mp.product_code FROM media_products mp "
            "LEFT JOIN product_title_cache ptc ON ptc.product_id = mp.id "
            "WHERE mp.product_code IS NOT NULL AND mp.deleted_at IS NULL "
            "AND (ptc.id IS NULL OR ptc.fetched_at < DATE_SUB(NOW(), INTERVAL 7 DAY))",
        )

    fetched = 0
    errors = 0
    for p in products:
        code = p["product_code"]
        if not code:
            continue
        title = fetch_product_page_title(code)
        if title:
            execute(
                "INSERT INTO product_title_cache (product_id, product_code, page_title, fetched_at) "
                "VALUES (%s, %s, %s, NOW()) "
                "ON DUPLICATE KEY UPDATE page_title=VALUES(page_title), fetched_at=NOW()",
                (p["id"], code, title),
            )
            fetched += 1
        else:
            errors += 1
        time.sleep(0.5)  # 限速

    return {"fetched": fetched, "skipped": len(products) - fetched - errors, "errors": errors}


def match_orders_to_products() -> int:
    """将 lineitem_name 匹配到 product_id。返回新匹配行数。"""
    # 精确匹配
    affected = execute(
        "UPDATE shopify_orders so "
        "JOIN product_title_cache ptc ON ptc.page_title = so.lineitem_name "
        "SET so.product_id = ptc.product_id "
        "WHERE so.product_id IS NULL"
    )
    return affected


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
        f"COALESCE(ptc.page_title, so.lineitem_name) AS display_name, "
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
        f"COALESCE(ptc.page_title, so.lineitem_name) AS display_name, "
        f"so.billing_country, "
        f"SUM(so.lineitem_quantity) AS total_qty "
        f"FROM shopify_orders so "
        f"LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
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

    return {
        "products": products,
        "countries": countries,
        "country_list": country_list,
        "matrix": matrix,
        "product_order": product_order,
    }


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
        f"COALESCE(ptc.page_title, so.lineitem_name) AS display_name, "
        f"so.billing_country, "
        f"SUM(so.lineitem_quantity) AS total_qty, "
        f"COUNT(DISTINCT so.shopify_order_id) AS order_count "
        f"FROM shopify_orders so "
        f"LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
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
        "COALESCE(ptc.page_title, so.lineitem_name) AS display_name, "
        "SUM(so.lineitem_quantity) AS total_qty, "
        "COUNT(DISTINCT so.shopify_order_id) AS order_count "
        "FROM shopify_orders so "
        "LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
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
            "COALESCE(ptc.page_title, so.lineitem_name) AS display_name, "
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
        "COALESCE(ptc.page_title, so.lineitem_name) AS display_name, "
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
