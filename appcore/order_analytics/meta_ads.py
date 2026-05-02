"""Meta 广告报表解析、导入、匹配、统计与汇总查询。

由 ``appcore.order_analytics`` package 在 PR 1.4 抽出；函数体逐字符
保留，行为不变。``__init__.py`` 通过显式 re-export 把这里的公开符号
带回 ``appcore.order_analytics`` 命名空间。
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from typing import Any

from ._constants import (
    META_ATTRIBUTION_TIMEZONE,
    _META_AD_NUMERIC_FIELDS,
    _META_AD_REQUIRED_COLS,
    _META_AD_SUMMARY_NUMERIC_FIELDS,
)
from ._helpers import _money, _parse_meta_date, _roas, _safe_float_default, _safe_int
from .dianxiaomi import compute_meta_business_window_bj
from .shopify_orders import parse_shopify_file


# DB 入口走 module-level wrapper（与 dianxiaomi.py / shopify_orders.py 同样原理）：
# 让现有 monkeypatch.setattr(oa, "query", fake) 透传到本模块的 query(...) 调用。
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
                # 走 facade 让 monkeypatch.setattr(oa, "resolve_ad_product_match", fake) 透传
                product = _facade().resolve_ad_product_match(row["campaign_name"])
                product_id = product.get("id") if product else None
                matched_product_code = product.get("product_code") if product else None
                if product_id:
                    matched += 1
                meta_business_date = None
                meta_window_start_at = None
                meta_window_end_at = None
                if row["report_start_date"] == row["report_end_date"]:
                    meta_business_date = row["report_start_date"]
                    meta_window_start_at, meta_window_end_at = compute_meta_business_window_bj(meta_business_date)
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
                    meta_business_date,
                    meta_window_start_at,
                    meta_window_end_at,
                    META_ATTRIBUTION_TIMEZONE,
                )
                cur.execute(
                    "INSERT INTO meta_ad_campaign_metrics "
                    "(import_batch_id, report_start_date, report_end_date, import_frequency, "
                    "campaign_name, normalized_campaign_code, matched_product_code, product_id, "
                    "result_count, result_metric, spend_usd, purchase_value_usd, roas_purchase, "
                    "cpm_usd, unique_link_click_cost_usd, link_ctr, campaign_delivery, link_clicks, "
                    "add_to_cart_count, initiate_checkout_count, add_to_cart_cost_usd, "
                    "initiate_checkout_cost_usd, cost_per_result_usd, average_purchase_value_usd, "
                    "impressions, video_avg_play_time, raw_json, "
                    "meta_business_date, meta_window_start_at, meta_window_end_at, attribution_timezone) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
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
                    "raw_json=VALUES(raw_json), meta_business_date=VALUES(meta_business_date), "
                    "meta_window_start_at=VALUES(meta_window_start_at), "
                    "meta_window_end_at=VALUES(meta_window_end_at), "
                    "attribution_timezone=VALUES(attribution_timezone)",
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
    affected = 0
    for table_name in ("meta_ad_campaign_metrics", "meta_ad_daily_campaign_metrics"):
        rows = query(
            f"SELECT id, campaign_name FROM {table_name} "
            "WHERE product_id IS NULL OR matched_product_code IS NULL",
        )
        for row in rows:
            # 走 facade 让 monkeypatch.setattr(oa, "resolve_ad_product_match", fake) 透传
            product = _facade().resolve_ad_product_match(row.get("campaign_name") or "")
            if not product:
                continue
            affected += execute(
                f"UPDATE {table_name} SET product_id=%s, matched_product_code=%s WHERE id=%s",
                (product["id"], product["product_code"], row["id"]),
            )
    return affected


def get_meta_ad_stats() -> dict:
    row = query_one(
        "SELECT COUNT(*) AS total_rows, "
        "COUNT(DISTINCT meta_business_date) AS period_count, "
        "MIN(meta_business_date) AS min_date, MAX(meta_business_date) AS max_date, "
        "SUM(CASE WHEN product_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_rows, "
        "SUM(spend_usd) AS total_spend_usd, "
        "SUM(purchase_value_usd) AS total_purchase_value_usd "
        "FROM meta_ad_daily_campaign_metrics"
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
    if report_end < report_start:
        raise ValueError("end_date must be greater than or equal to start_date")

    use_daily_metrics = not batch_id and bool(start_date and end_date)
    if use_daily_metrics:
        metric_rows = query(
            "SELECT MIN(m.id) AS id, m.product_id, mp.name AS product_name, "
            "mp.product_code AS media_product_code, "
            "COALESCE(m.matched_product_code, m.product_code) AS matched_product_code, "
            "m.campaign_name, SUM(m.result_count) AS result_count, "
            "SUM(m.spend_usd) AS spend_usd, SUM(m.purchase_value_usd) AS purchase_value_usd, "
            "0 AS link_clicks, 0 AS add_to_cart_count, 0 AS initiate_checkout_count, 0 AS impressions "
            "FROM meta_ad_daily_campaign_metrics m "
            "LEFT JOIN media_products mp ON mp.id = m.product_id "
            "WHERE m.meta_business_date >= %s AND m.meta_business_date <= %s "
            "GROUP BY m.product_id, mp.name, mp.product_code, "
            "COALESCE(m.matched_product_code, m.product_code), m.campaign_name "
            "ORDER BY spend_usd DESC",
            (report_start, report_end),
        )
    else:
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

    dianxiaomi_by_product: dict[int, dict] = {}
    if product_ids:
        placeholders = ",".join(["%s"] * len(product_ids))
        dxm_rows = query(
            "SELECT product_id, COUNT(DISTINCT dxm_package_id) AS dianxiaomi_order_count, "
            "SUM(COALESCE(line_amount, 0)) + SUM(COALESCE(ship_amount, 0)) AS dianxiaomi_total_sales "
            "FROM dianxiaomi_order_lines "
            f"WHERE product_id IN ({placeholders}) "
            "AND meta_business_date >= %s AND meta_business_date <= %s "
            "GROUP BY product_id",
            tuple(product_ids + [report_start, report_end]),
        )
        dianxiaomi_by_product = {int(row["product_id"]): row for row in dxm_rows}

    for row in rows:
        dxm_metrics = dianxiaomi_by_product.get(int(row["product_id"] or 0), {})
        dxm_total_sales = _money(dxm_metrics.get("dianxiaomi_total_sales"))
        row["dianxiaomi_order_count"] = int(dxm_metrics.get("dianxiaomi_order_count") or 0)
        row["dianxiaomi_total_sales"] = dxm_total_sales
        row["dianxiaomi_roas"] = _roas(dxm_total_sales, float(row.get("spend_usd") or 0))

    if use_daily_metrics:
        unmatched = query(
            "SELECT MIN(id) AS id, campaign_name, normalized_campaign_code, "
            "SUM(spend_usd) AS spend_usd, SUM(result_count) AS result_count, "
            "SUM(purchase_value_usd) AS purchase_value_usd "
            "FROM meta_ad_daily_campaign_metrics "
            "WHERE meta_business_date >= %s AND meta_business_date <= %s AND product_id IS NULL "
            "GROUP BY campaign_name, normalized_campaign_code "
            "ORDER BY spend_usd DESC",
            (report_start, report_end),
        )
    else:
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
            "source": "meta_ad_daily_campaign_metrics" if use_daily_metrics else "meta_ad_campaign_metrics",
        },
        "rows": rows,
        "unmatched": unmatched,
    }
