"""订单分析 DAO 层：Shopify 订单导入、产品匹配、数据分析查询。"""
from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
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

_META_AD_REQUIRED_COLS = [
    "报告开始日期",
    "报告结束日期",
    "广告系列名称",
    "已花费金额 (USD)",
]

_META_AD_NUMERIC_FIELDS: dict[str, tuple[str, str]] = {
    "成效": ("result_count", "int"),
    "已花费金额 (USD)": ("spend_usd", "float"),
    "购物转化价值": ("purchase_value_usd", "float"),
    "广告花费回报 (ROAS) - 购物": ("roas_purchase", "float"),
    "CPM（千次展示费用） (USD)": ("cpm_usd", "float"),
    "单次链接点击费用 - 独立用户 (USD)": ("unique_link_click_cost_usd", "float"),
    "链接点击率": ("link_ctr", "float"),
    "链接点击量": ("link_clicks", "int"),
    "加入购物车次数": ("add_to_cart_count", "int"),
    "结账发起次数": ("initiate_checkout_count", "int"),
    "单次加入购物车费用 (USD)": ("add_to_cart_cost_usd", "float"),
    "单次发起结账费用 (USD)": ("initiate_checkout_cost_usd", "float"),
    "单次成效费用": ("cost_per_result_usd", "float"),
    "平均购物转化价值": ("average_purchase_value_usd", "float"),
    "展示次数": ("impressions", "int"),
    "视频平均播放时长": ("video_avg_play_time", "float"),
}


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


def _safe_float_default(val: str, default: float = 0.0) -> float:
    parsed = _safe_float(val)
    return default if parsed is None else parsed


def _parse_meta_date(value: str) -> date:
    value = (value or "").strip()
    if not value:
        raise ValueError("Meta 广告报表日期不能为空")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"无法解析 Meta 广告报表日期：{value}")


def product_code_candidates_for_ad_campaign(campaign_name: str) -> list[str]:
    code = (campaign_name or "").strip().lower()
    if not code:
        return []
    candidates = [code]
    if code.endswith("-rjc"):
        candidates.append(code[:-4])
    else:
        candidates.append(f"{code}-rjc")
    return list(dict.fromkeys(candidates))


def resolve_ad_product_match(campaign_name: str) -> dict | None:
    for code in product_code_candidates_for_ad_campaign(campaign_name):
        product = query_one(
            "SELECT id, product_code, name FROM media_products "
            "WHERE product_code=%s AND deleted_at IS NULL",
            (code,),
        )
        if product:
            return product
    return None


def _normalize_meta_ad_row(row: dict) -> dict:
    campaign_name = (row.get("广告系列名称") or "").strip()
    normalized = campaign_name.lower()
    out = {
        "report_start_date": _parse_meta_date(row.get("报告开始日期", "")),
        "report_end_date": _parse_meta_date(row.get("报告结束日期", "")),
        "campaign_name": campaign_name,
        "normalized_campaign_code": normalized,
        "result_metric": (row.get("成效指标") or "").strip()[:128] or None,
        "campaign_delivery": (row.get("广告系列投放") or "").strip()[:32] or None,
        "raw": dict(row),
    }
    for source_col, (target_col, kind) in _META_AD_NUMERIC_FIELDS.items():
        if kind == "int":
            out[target_col] = _safe_int(row.get(source_col, "0"), 0)
        else:
            out[target_col] = _safe_float_default(row.get(source_col, ""), 0.0)
    return out


def parse_meta_ad_file(file_stream, filename: str) -> list[dict]:
    """解析 Meta 广告后台导出的 CSV/Excel，返回标准化后的周期广告行。"""
    rows = parse_shopify_file(file_stream, filename)
    if not rows:
        return []
    headers = set(rows[0].keys())
    missing = [col for col in _META_AD_REQUIRED_COLS if col not in headers]
    if missing:
        raise ValueError("Meta 广告报表缺少列：" + "、".join(missing))
    return [
        _normalize_meta_ad_row(row)
        for row in rows
        if (row.get("广告系列名称") or "").strip()
    ]


def _coerce_ad_frequency(value: str | None) -> str:
    normalized = (value or "custom").strip().lower()
    if normalized not in {"weekly", "monthly", "custom"}:
        return "custom"
    return normalized


def import_meta_ad_rows(
    rows: list[dict],
    filename: str,
    file_bytes: bytes,
    import_frequency: str = "custom",
) -> dict:
    """将 Meta 广告周期报表 upsert 到长期表。"""
    frequency = _coerce_ad_frequency(import_frequency)
    file_hash = hashlib.sha256(file_bytes or b"").hexdigest()
    starts = [row["report_start_date"] for row in rows if row.get("report_start_date")]
    ends = [row["report_end_date"] for row in rows if row.get("report_end_date")]
    report_start = min(starts) if starts else None
    report_end = max(ends) if ends else None

    conn = get_conn()
    imported = 0
    updated = 0
    skipped = 0
    matched = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meta_ad_import_batches "
                "(source_filename, file_sha256, import_frequency, report_start_date, report_end_date, raw_row_count) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (filename, file_hash, frequency, report_start, report_end, len(rows)),
            )
            batch_id = cur.lastrowid

            for row in rows:
                product = resolve_ad_product_match(row["campaign_name"])
                product_id = product.get("id") if product else None
                matched_product_code = product.get("product_code") if product else None
                if product_id:
                    matched += 1
                args = (
                    batch_id,
                    row["report_start_date"],
                    row["report_end_date"],
                    frequency,
                    row["campaign_name"],
                    row["normalized_campaign_code"],
                    matched_product_code,
                    product_id,
                    row.get("result_count") or 0,
                    row.get("result_metric"),
                    row.get("spend_usd") or 0,
                    row.get("purchase_value_usd") or 0,
                    row.get("roas_purchase"),
                    row.get("cpm_usd"),
                    row.get("unique_link_click_cost_usd"),
                    row.get("link_ctr"),
                    row.get("campaign_delivery"),
                    row.get("link_clicks") or 0,
                    row.get("add_to_cart_count") or 0,
                    row.get("initiate_checkout_count") or 0,
                    row.get("add_to_cart_cost_usd"),
                    row.get("initiate_checkout_cost_usd"),
                    row.get("cost_per_result_usd"),
                    row.get("average_purchase_value_usd"),
                    row.get("impressions") or 0,
                    row.get("video_avg_play_time"),
                    json.dumps(row.get("raw") or {}, ensure_ascii=False),
                )
                cur.execute(
                    "INSERT INTO meta_ad_campaign_metrics "
                    "(import_batch_id, report_start_date, report_end_date, import_frequency, "
                    "campaign_name, normalized_campaign_code, matched_product_code, product_id, "
                    "result_count, result_metric, spend_usd, purchase_value_usd, roas_purchase, "
                    "cpm_usd, unique_link_click_cost_usd, link_ctr, campaign_delivery, link_clicks, "
                    "add_to_cart_count, initiate_checkout_count, add_to_cart_cost_usd, "
                    "initiate_checkout_cost_usd, cost_per_result_usd, average_purchase_value_usd, "
                    "impressions, video_avg_play_time, raw_json) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE "
                    "import_batch_id=VALUES(import_batch_id), import_frequency=VALUES(import_frequency), "
                    "normalized_campaign_code=VALUES(normalized_campaign_code), "
                    "matched_product_code=VALUES(matched_product_code), product_id=VALUES(product_id), "
                    "result_count=VALUES(result_count), result_metric=VALUES(result_metric), "
                    "spend_usd=VALUES(spend_usd), purchase_value_usd=VALUES(purchase_value_usd), "
                    "roas_purchase=VALUES(roas_purchase), cpm_usd=VALUES(cpm_usd), "
                    "unique_link_click_cost_usd=VALUES(unique_link_click_cost_usd), "
                    "link_ctr=VALUES(link_ctr), campaign_delivery=VALUES(campaign_delivery), "
                    "link_clicks=VALUES(link_clicks), add_to_cart_count=VALUES(add_to_cart_count), "
                    "initiate_checkout_count=VALUES(initiate_checkout_count), "
                    "add_to_cart_cost_usd=VALUES(add_to_cart_cost_usd), "
                    "initiate_checkout_cost_usd=VALUES(initiate_checkout_cost_usd), "
                    "cost_per_result_usd=VALUES(cost_per_result_usd), "
                    "average_purchase_value_usd=VALUES(average_purchase_value_usd), "
                    "impressions=VALUES(impressions), video_avg_play_time=VALUES(video_avg_play_time), "
                    "raw_json=VALUES(raw_json)",
                    args,
                )
                if cur.rowcount == 1:
                    imported += 1
                elif cur.rowcount == 2:
                    updated += 1
                else:
                    skipped += 1

            cur.execute(
                "UPDATE meta_ad_import_batches SET imported_rows=%s, updated_rows=%s, "
                "skipped_rows=%s, matched_rows=%s WHERE id=%s",
                (imported, updated, skipped, matched, batch_id),
            )
    finally:
        conn.close()

    return {
        "batch_id": batch_id,
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "matched": matched,
    }


def match_meta_ads_to_products() -> int:
    rows = query(
        "SELECT id, campaign_name FROM meta_ad_campaign_metrics "
        "WHERE product_id IS NULL OR matched_product_code IS NULL",
    )
    affected = 0
    for row in rows:
        product = resolve_ad_product_match(row.get("campaign_name") or "")
        if not product:
            continue
        affected += execute(
            "UPDATE meta_ad_campaign_metrics SET product_id=%s, matched_product_code=%s WHERE id=%s",
            (product["id"], product["product_code"], row["id"]),
        )
    return affected


def get_meta_ad_stats() -> dict:
    row = query_one(
        "SELECT COUNT(*) AS total_rows, "
        "COUNT(DISTINCT CONCAT(report_start_date, '|', report_end_date)) AS period_count, "
        "MIN(report_start_date) AS min_date, MAX(report_end_date) AS max_date, "
        "SUM(CASE WHEN product_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_rows, "
        "SUM(spend_usd) AS total_spend_usd, "
        "SUM(purchase_value_usd) AS total_purchase_value_usd "
        "FROM meta_ad_campaign_metrics"
    )
    return row or {}


def get_meta_ad_periods() -> list[dict]:
    return query(
        "SELECT MAX(import_batch_id) AS batch_id, report_start_date, report_end_date, "
        "MAX(import_frequency) AS import_frequency, COUNT(*) AS row_count, "
        "SUM(spend_usd) AS total_spend_usd, SUM(purchase_value_usd) AS total_purchase_value_usd "
        "FROM meta_ad_campaign_metrics "
        "GROUP BY report_start_date, report_end_date "
        "ORDER BY report_end_date DESC, report_start_date DESC"
    )


def _resolve_meta_ad_period(
    batch_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[date | None, date | None, int | None]:
    if batch_id:
        row = query_one(
            "SELECT id, report_start_date, report_end_date FROM meta_ad_import_batches WHERE id=%s",
            (batch_id,),
        )
        if not row:
            return None, None, None
        return row.get("report_start_date"), row.get("report_end_date"), row.get("id")
    if start_date and end_date:
        return _parse_meta_date(start_date), _parse_meta_date(end_date), None
    latest = query_one(
        "SELECT MAX(import_batch_id) AS batch_id, report_start_date, report_end_date "
        "FROM meta_ad_campaign_metrics "
        "GROUP BY report_start_date, report_end_date "
        "ORDER BY report_end_date DESC, report_start_date DESC LIMIT 1"
    )
    if not latest:
        return None, None, None
    return latest.get("report_start_date"), latest.get("report_end_date"), latest.get("batch_id")


_META_AD_SUMMARY_NUMERIC_FIELDS = (
    "result_count",
    "spend_usd",
    "purchase_value_usd",
    "link_clicks",
    "add_to_cart_count",
    "initiate_checkout_count",
    "impressions",
)


def _coerce_meta_product_id(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _aggregate_meta_ad_summary_rows(metric_rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, int | str], dict] = {}

    for metric in metric_rows:
        product_id = _coerce_meta_product_id(metric.get("product_id"))
        campaign_name = (metric.get("campaign_name") or "").strip()
        if product_id is not None:
            group_key: tuple[str, int | str] = ("product", product_id)
            display_name = metric.get("product_name") or campaign_name
            product_code = metric.get("matched_product_code") or metric.get("media_product_code")
        else:
            group_key = ("campaign", campaign_name or str(metric.get("id") or ""))
            display_name = campaign_name
            product_code = None

        if group_key not in grouped:
            grouped[group_key] = {
                "product_id": product_id,
                "display_name": display_name,
                "product_code": product_code,
                "campaign_count": 0,
                "_campaign_names": [],
                **{field: 0 for field in _META_AD_SUMMARY_NUMERIC_FIELDS},
            }

        row = grouped[group_key]
        if product_id is not None:
            row["display_name"] = row.get("display_name") or display_name
            row["product_code"] = row.get("product_code") or product_code
        row["campaign_count"] += 1
        if campaign_name:
            row["_campaign_names"].append(campaign_name)
        for field in _META_AD_SUMMARY_NUMERIC_FIELDS:
            row[field] += metric.get(field) or 0

    rows: list[dict] = []
    for row in grouped.values():
        campaign_names = sorted(row.pop("_campaign_names"))
        row["campaign_names"] = ", ".join(campaign_names)
        spend = row.get("spend_usd") or 0
        purchase_value = row.get("purchase_value_usd") or 0
        result_count = row.get("result_count") or 0
        row["roas_purchase"] = purchase_value / spend if spend else None
        row["cost_per_result_usd"] = spend / result_count if result_count else None
        rows.append(row)

    rows.sort(key=lambda row: row.get("spend_usd") or 0, reverse=True)
    return rows


def get_meta_ad_summary(
    batch_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    report_start, report_end, resolved_batch_id = _resolve_meta_ad_period(batch_id, start_date, end_date)
    if not report_start or not report_end:
        return {"period": None, "rows": [], "unmatched": []}

    metric_rows = query(
        "SELECT m.id, m.product_id, mp.name AS product_name, mp.product_code AS media_product_code, "
        "m.matched_product_code, m.campaign_name, m.result_count, m.spend_usd, "
        "m.purchase_value_usd, m.link_clicks, m.add_to_cart_count, "
        "m.initiate_checkout_count, m.impressions "
        "FROM meta_ad_campaign_metrics m "
        "LEFT JOIN media_products mp ON mp.id = m.product_id "
        "WHERE m.report_start_date=%s AND m.report_end_date=%s "
        "ORDER BY m.spend_usd DESC",
        (report_start, report_end),
    )
    rows = _aggregate_meta_ad_summary_rows(metric_rows)

    product_ids = [int(row["product_id"]) for row in rows if row.get("product_id")]
    orders_by_product: dict[int, dict] = {}
    if product_ids:
        placeholders = ",".join(["%s"] * len(product_ids))
        order_rows = query(
            "SELECT product_id, COUNT(DISTINCT shopify_order_id) AS shopify_order_count, "
            "SUM(lineitem_quantity) AS shopify_quantity, "
            "SUM(COALESCE(lineitem_price, 0) * lineitem_quantity) AS shopify_revenue "
            "FROM shopify_orders "
            f"WHERE product_id IN ({placeholders}) AND created_at_order >= %s AND created_at_order < DATE_ADD(%s, INTERVAL 1 DAY) "
            "GROUP BY product_id",
            tuple(product_ids + [report_start, report_end]),
        )
        orders_by_product = {int(row["product_id"]): row for row in order_rows}

    for row in rows:
        order_metrics = orders_by_product.get(int(row["product_id"] or 0), {})
        row["shopify_order_count"] = order_metrics.get("shopify_order_count") or 0
        row["shopify_quantity"] = order_metrics.get("shopify_quantity") or 0
        row["shopify_revenue"] = order_metrics.get("shopify_revenue") or 0

    unmatched = query(
        "SELECT id, campaign_name, normalized_campaign_code, spend_usd, result_count, purchase_value_usd "
        "FROM meta_ad_campaign_metrics "
        "WHERE report_start_date=%s AND report_end_date=%s AND product_id IS NULL "
        "ORDER BY spend_usd DESC",
        (report_start, report_end),
    )
    return {
        "period": {
            "batch_id": resolved_batch_id,
            "report_start_date": report_start,
            "report_end_date": report_end,
        },
        "rows": rows,
        "unmatched": unmatched,
    }


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
            # 先解码 HTML 实体
            title = (title
                     .replace("&ndash;", "–")
                     .replace("&mdash;", "—")
                     .replace("&amp;", "&")
                     .replace("&lt;", "<")
                     .replace("&gt;", ">")
                     .replace("&quot;", '"'))
            # 再去掉 " | Store Name" / " – Store Name" / " - Store Name" 后缀
            for sep in (" | ", " – ", " — ", " - "):
                if sep in title:
                    title = title.rsplit(sep, 1)[0].strip()
                    break
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
    # 前缀匹配：订单商品名以产品标题开头（后面可能有变体信息如 "- 1 Pack"）
    affected = execute(
        "UPDATE shopify_orders so "
        "JOIN product_title_cache ptc ON so.lineitem_name LIKE CONCAT(ptc.page_title, '%') "
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


# ── 产品看板 V1 ───────────────────────────────────────────

def _compute_pct_change(now, prev) -> float | None:
    """环比百分比。返回 None 表示无法计算（prev=0 且 now>0）。"""
    now_v = float(now or 0)
    prev_v = float(prev or 0)
    if prev_v == 0 and now_v == 0:
        return 0.0
    if prev_v == 0:
        return None
    return round((now_v - prev_v) / prev_v * 100, 2)


def _resolve_period_range(
    period: str,
    *,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    date_str: str | None = None,
    today: date | None = None,
) -> tuple[date, date]:
    """返回 (start, end) 闭区间。

    - month: 该月 1 日 ~ 月末；若为当月，end = 昨日（不含今天）
    - week: ISO 周一 ~ 周日；若为当周，end = 昨日
    - day: date_str ~ date_str
    """
    today = today or date.today()
    yesterday = today - timedelta(days=1)

    if period == "month":
        if year is None or month is None:
            raise ValueError("year and month required for period=month")
        start = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        end = date(year, month, last_day)
        if start <= today <= end:
            end = yesterday if yesterday >= start else start
        return start, end

    if period == "week":
        if year is None or week is None:
            raise ValueError("year and week required for period=week")
        # ISO week: %G-%V-%u; %u=1 = Monday
        start = datetime.strptime(f"{year}-{week:02d}-1", "%G-%V-%u").date()
        end = start + timedelta(days=6)
        if start <= today <= end:
            end = yesterday if yesterday >= start else start
        return start, end

    if period == "day":
        if not date_str:
            raise ValueError("date required for period=day")
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return d, d

    raise ValueError(f"invalid period: {period}")


def _resolve_compare_range(start: date, end: date, period: str) -> tuple[date, date]:
    """返回上一个同长度切片。"""
    if period == "month":
        # 减一个月：直接调整 month 字段
        prev_year = start.year - (1 if start.month == 1 else 0)
        prev_month = 12 if start.month == 1 else start.month - 1
        prev_start = date(prev_year, prev_month, start.day)
        # end 取上月同一天（截断到上月末尾）
        prev_month_last = calendar.monthrange(prev_year, prev_month)[1]
        prev_end_day = min(end.day, prev_month_last)
        prev_end = date(prev_year, prev_month, prev_end_day)
        return prev_start, prev_end

    if period == "week":
        prev_start = start - timedelta(days=7)
        return prev_start, prev_start + (end - start)

    if period == "day":
        prev = start - timedelta(days=1)
        return prev, prev

    raise ValueError(f"invalid period: {period}")
