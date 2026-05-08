"""Meta 广告报表解析、导入、匹配、统计与汇总查询。

由 ``appcore.order_analytics`` package 在 PR 1.4 抽出；函数体逐字符
保留，行为不变。``__init__.py`` 通过显式 re-export 把这里的公开符号
带回 ``appcore.order_analytics`` 命名空间。
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, timedelta
from typing import Any

from ._constants import (
    META_ATTRIBUTION_TIMEZONE,
    _META_AD_NUMERIC_FIELDS,
    _META_AD_REQUIRED_COLS,
    _META_AD_SUMMARY_NUMERIC_FIELDS,
)
from ._helpers import (
    _money,
    _parse_iso_date_param,
    _parse_meta_date,
    _roas,
    _safe_float_default,
    _safe_int,
    current_meta_business_date,
)
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
    # 自动匹配失败 → 查人工配对兜底（plan 阶段 5 扩展）
    from .campaign_overrides import resolve_override
    normalized = (campaign_name or "").strip().lower()
    override = resolve_override(normalized)
    if override:
        return override
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


def manual_match_meta_ad_campaign(
    normalized_campaign_code: str,
    product_id: int,
    *,
    reason: str = "",
    created_by: str = "admin",
) -> dict:
    """把指定归一化广告系列名下所有未匹配的行人工配对到 media_products 产品。

    Source of truth：``campaign_product_overrides`` 表。委托给
    ``appcore.order_analytics.campaign_overrides.create_override``，
    它会同时：
      1. INSERT/UPDATE ``campaign_product_overrides`` 表（持久化映射）
      2. UPDATE ``meta_ad_campaign_metrics`` + ``meta_ad_daily_campaign_metrics``
         两张事实表（让历史 dashboard / 利润核算立即看到匹配）

    未来同步流程的 ``resolve_ad_product_match`` 也会查 override 表，
    同名 campaign 自动应用，无需再手工配对。

    Returns 跟原 schema 兼容：
        {matched_periodic, matched_daily, product_id, product_code, product_name}
    """
    from .campaign_overrides import create_override
    res = create_override(
        normalized_campaign_code=normalized_campaign_code,
        product_id=product_id,
        reason=reason,
        created_by=created_by,
    )
    return {
        "matched_periodic": res["matched_periodic"],
        "matched_daily": res["matched_daily"],
        "product_id": res["product_id"],
        "product_code": res["product_code"],
        "product_name": res["product_name"],
    }


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
            "SUM(COALESCE(quantity, 0)) AS dianxiaomi_units, "
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
        row["dianxiaomi_units"] = int(dxm_metrics.get("dianxiaomi_units") or 0)
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


# ── 三层级（Campaign / Ad Set / Ad）查询：list / search / detail ──────
# Docs-anchor: docs/superpowers/specs/2026-05-08-ads-analytics-tabs-design.md

_LEVEL_CONFIG: dict[str, dict[str, Any]] = {
    "campaign": {
        "table": "meta_ad_daily_campaign_metrics",
        "code_col": "normalized_campaign_code",
        "name_col": "campaign_name",
        "supports_realtime": True,
    },
    "adset": {
        "table": "meta_ad_daily_adset_metrics",
        "code_col": "normalized_adset_code",
        "name_col": "adset_name",
        "supports_realtime": False,
    },
    "ad": {
        "table": "meta_ad_daily_ad_metrics",
        "code_col": "normalized_ad_code",
        "name_col": "ad_name",
        "supports_realtime": False,
    },
}

_ADS_LIST_SORT_EXPR: dict[str, str] = {
    "spend_usd": "SUM(spend_usd)",
    "purchase_value_usd": "SUM(purchase_value_usd)",
    "result_count": "SUM(result_count)",
    "roas_purchase": "(SUM(purchase_value_usd) / NULLIF(SUM(spend_usd), 0))",
    "day_count": "COUNT(DISTINCT meta_business_date)",
}

_RAW_JSON_KEY_VARIANTS: dict[str, tuple[str, ...]] = {
    "cpc_usd": (
        "link_click_cost",
        "cpc",
        "unique_link_click_cost",
        "unique_link_click_cost_usd",
        "cost_per_link_click",
        "cost_per_unique_link_click",
        "每次链接点击费用 (USD)",
        "单次链接点击费用 (USD)",
    ),
    "ecpm_usd": (
        "cpm",
        "cpm_usd",
        "ecpm",
        "cost_per_1000_impressions",
        "千次展示费用 (USD)",
        "千次展示费用",
    ),
    "impressions": (
        "impressions",
        "展示次数",
        "展示量",
    ),
    "link_clicks": (
        "link_clicks",
        "linkclicks",
        "链接点击量",
        "链接点击次数",
    ),
    "add_to_cart_count": (
        "add_to_cart_count",
        "add_to_cart",
        "atc",
        "加购次数",
        "Adds to Cart",
    ),
    "initiate_checkout_count": (
        "initiate_checkout_count",
        "initiates_checkout",
        "ic",
        "发起结账次数",
        "Initiate Checkouts",
    ),
    "video_avg_play_time": (
        "video_avg_play_time",
        "video_avg_time_watched",
        "video_avg_time_watched_actions",
        "视频均播时长",
        "视频平均播放时长",
    ),
}


def _resolve_ads_level(level: str) -> dict:
    cfg = _LEVEL_CONFIG.get((level or "").strip().lower())
    if not cfg:
        raise ValueError("level must be one of campaign/adset/ad")
    return cfg


def _coerce_ads_date_range(
    start_date: str | None,
    end_date: str | None,
    *,
    default_days: int = 14,
) -> tuple[date, date]:
    today = current_meta_business_date()
    end = _parse_iso_date_param(end_date, "end_date") if end_date else today
    start = (
        _parse_iso_date_param(start_date, "start_date")
        if start_date
        else end - timedelta(days=default_days - 1)
    )
    if start > end:
        raise ValueError("start_date must be <= end_date")
    return start, end


def _parse_raw_json_field(raw_json: Any) -> dict:
    if not raw_json:
        return {}
    if isinstance(raw_json, dict):
        return raw_json
    if isinstance(raw_json, str):
        try:
            return json.loads(raw_json)
        except (TypeError, ValueError):
            return {}
    return {}


def _coerce_raw_value(raw: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in raw and raw[key] not in (None, "", "—", "-"):
            try:
                return float(str(raw[key]).replace(",", "").replace("%", "").strip())
            except (TypeError, ValueError):
                continue
    return None


def get_ads_level_list(
    level: str,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "spend_usd",
    sort_dir: str = "desc",
) -> dict:
    """List Campaign / Ad Set / Ad rows aggregated by code within a date range."""
    cfg = _resolve_ads_level(level)
    start, end = _coerce_ads_date_range(start_date, end_date)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))

    sort_expr = _ADS_LIST_SORT_EXPR.get(sort_by) or _ADS_LIST_SORT_EXPR["spend_usd"]
    sort_dir_norm = "ASC" if (sort_dir or "").lower() == "asc" else "DESC"

    table = cfg["table"]
    code_col = cfg["code_col"]
    name_col = cfg["name_col"]

    total_row = query_one(
        f"SELECT COUNT(DISTINCT {code_col}) AS total FROM {table} "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s",
        (start, end),
    )
    total = int((total_row or {}).get("total") or 0)

    offset = (page - 1) * page_size
    rows = query(
        f"SELECT {code_col} AS code, MAX({name_col}) AS name, "
        "MAX(ad_account_id) AS ad_account_id, MAX(ad_account_name) AS ad_account_name, "
        "SUM(spend_usd) AS spend_usd, SUM(purchase_value_usd) AS purchase_value_usd, "
        "SUM(result_count) AS result_count, "
        "COUNT(DISTINCT meta_business_date) AS day_count, "
        "(SUM(purchase_value_usd) / NULLIF(SUM(spend_usd), 0)) AS roas_purchase "
        f"FROM {table} "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        f"GROUP BY {code_col} "
        f"ORDER BY {sort_expr} {sort_dir_norm} "
        "LIMIT %s OFFSET %s",
        (start, end, page_size, offset),
    )

    out: list[dict] = []
    for row in rows or []:
        spend = float(row.get("spend_usd") or 0)
        purchase = float(row.get("purchase_value_usd") or 0)
        out.append({
            "code": row.get("code"),
            "name": row.get("name"),
            "ad_account_id": row.get("ad_account_id"),
            "ad_account_name": row.get("ad_account_name"),
            "spend_usd": _money(spend),
            "purchase_value_usd": _money(purchase),
            "roas_purchase": _roas(purchase, spend),
            "result_count": int(row.get("result_count") or 0),
            "day_count": int(row.get("day_count") or 0),
        })

    return {
        "level": level,
        "period": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        "rows": out,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": (page * page_size) < total,
    }


def search_ads_by_level(level: str, q: str, limit: int = 20) -> dict:
    """Per-tab autocomplete: match `name` LIKE %q%, return top N by recency + spend."""
    cfg = _resolve_ads_level(level)
    q_clean = (q or "").strip()
    if not q_clean:
        raise ValueError("q must be non-empty")
    limit = max(1, min(int(limit or 20), 50))

    table = cfg["table"]
    code_col = cfg["code_col"]
    name_col = cfg["name_col"]

    rows = query(
        f"SELECT {code_col} AS code, MAX({name_col}) AS name, "
        "MAX(meta_business_date) AS last_active_date, "
        "SUM(CASE WHEN meta_business_date >= DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY) "
        "         THEN spend_usd ELSE 0 END) AS total_spend_usd_30d "
        f"FROM {table} "
        f"WHERE {name_col} LIKE %s "
        f"GROUP BY {code_col} "
        "ORDER BY last_active_date DESC, total_spend_usd_30d DESC "
        "LIMIT %s",
        (f"%{q_clean}%", limit),
    )

    out: list[dict] = []
    for row in rows or []:
        last_active = row.get("last_active_date")
        out.append({
            "code": row.get("code"),
            "name": row.get("name"),
            "last_active_date": last_active.isoformat() if last_active else None,
            "total_spend_usd_30d": _money(row.get("total_spend_usd_30d") or 0),
        })

    return {"level": level, "query": q_clean, "rows": out}


def _fetch_realtime_today_campaign(
    code: str,
    business_date: date,
) -> dict | None:
    """Latest-snapshot-per-account aggregation for a single campaign code on `business_date`.

    Mirrors the (business_date, ad_account_id) -> MAX(snapshot_at) rule documented in
    CLAUDE.md "Meta 广告多账户同步" — DO NOT use a global MAX(snapshot_at).
    """
    row = query_one(
        "SELECT SUM(m.spend_usd) AS spend_usd, "
        "SUM(m.purchase_value_usd) AS purchase_value_usd, "
        "SUM(m.result_count) AS result_count, "
        "SUM(m.impressions) AS impressions, "
        "SUM(m.clicks) AS clicks, "
        "MAX(m.snapshot_at) AS snapshot_at, "
        "MAX(m.campaign_name) AS campaign_name, "
        "GROUP_CONCAT(DISTINCT m.ad_account_id) AS ad_account_id, "
        "GROUP_CONCAT(DISTINCT m.ad_account_name) AS ad_account_name "
        "FROM meta_ad_realtime_daily_campaign_metrics m "
        "INNER JOIN ("
        "  SELECT business_date, ad_account_id, MAX(snapshot_at) AS max_snapshot_at "
        "  FROM meta_ad_realtime_daily_campaign_metrics "
        "  WHERE business_date = %s AND normalized_campaign_code = %s "
        "  GROUP BY business_date, ad_account_id "
        ") latest "
        "ON m.business_date = latest.business_date "
        "AND m.ad_account_id = latest.ad_account_id "
        "AND m.snapshot_at = latest.max_snapshot_at "
        "WHERE m.business_date = %s AND m.normalized_campaign_code = %s",
        (business_date, code, business_date, code),
    )
    if not row or row.get("spend_usd") is None:
        return None
    return row


def get_ads_level_detail(
    level: str,
    code: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Per-day detail for one Campaign / Ad Set / Ad code within a date range."""
    cfg = _resolve_ads_level(level)
    code_clean = (code or "").strip()
    if not code_clean:
        raise ValueError("code is required")
    start, end = _coerce_ads_date_range(start_date, end_date)

    table = cfg["table"]
    code_col = cfg["code_col"]
    name_col = cfg["name_col"]
    supports_realtime = cfg["supports_realtime"]

    raw_rows = query(
        f"SELECT meta_business_date, {name_col} AS name, "
        "ad_account_id, ad_account_name, "
        "spend_usd, purchase_value_usd, result_count, raw_json "
        f"FROM {table} "
        f"WHERE {code_col} = %s "
        "AND meta_business_date >= %s AND meta_business_date <= %s "
        "ORDER BY meta_business_date DESC",
        (code_clean, start, end),
    )

    today = current_meta_business_date()
    realtime_row = None
    if supports_realtime and end >= today:
        realtime_row = _fetch_realtime_today_campaign(code_clean, today)

    daily_by_date: dict[date, list[dict]] = {}
    name_seen = None
    account_id_seen = None
    account_name_seen = None
    for row in raw_rows or []:
        d = row.get("meta_business_date")
        if not d:
            continue
        daily_by_date.setdefault(d, []).append(row)
        name_seen = name_seen or row.get("name")
        account_id_seen = account_id_seen or row.get("ad_account_id")
        account_name_seen = account_name_seen or row.get("ad_account_name")

    if realtime_row and not name_seen:
        name_seen = realtime_row.get("campaign_name")
    if realtime_row and not account_id_seen:
        account_id_seen = realtime_row.get("ad_account_id")
        account_name_seen = realtime_row.get("ad_account_name")

    out_rows: list[dict] = []
    all_dates: set[date] = set(daily_by_date.keys())
    if realtime_row:
        all_dates.add(today)

    for d in sorted(all_dates, reverse=True):
        if realtime_row and d == today and supports_realtime:
            spend = float(realtime_row.get("spend_usd") or 0)
            purchase = float(realtime_row.get("purchase_value_usd") or 0)
            impressions = int(realtime_row.get("impressions") or 0)
            link_clicks = int(realtime_row.get("clicks") or 0)
            out_rows.append({
                "date": d.isoformat(),
                "is_realtime": True,
                "spend_usd": _money(spend),
                "purchase_value_usd": _money(purchase),
                "roas_purchase": _roas(purchase, spend),
                "result_count": int(realtime_row.get("result_count") or 0),
                "budget_usd": None,
                "cpc_usd": _money(spend / link_clicks) if link_clicks > 0 else None,
                "ecpm_usd": _money(spend / impressions * 1000) if impressions > 0 else None,
                "impressions": impressions if impressions > 0 else None,
                "link_clicks": link_clicks if link_clicks > 0 else None,
                "add_to_cart_count": None,
                "initiate_checkout_count": None,
                "video_avg_play_time": None,
            })
            continue

        rows_for_date = daily_by_date.get(d) or []
        if not rows_for_date:
            continue
        spend = sum(float(r.get("spend_usd") or 0) for r in rows_for_date)
        purchase = sum(float(r.get("purchase_value_usd") or 0) for r in rows_for_date)
        result_count_total = sum(int(r.get("result_count") or 0) for r in rows_for_date)

        merged_raw: dict[str, float | None] = {key: None for key in _RAW_JSON_KEY_VARIANTS}
        for r in rows_for_date:
            parsed = _parse_raw_json_field(r.get("raw_json"))
            if not parsed:
                continue
            for out_key, candidates in _RAW_JSON_KEY_VARIANTS.items():
                if merged_raw[out_key] is None:
                    merged_raw[out_key] = _coerce_raw_value(parsed, candidates)

        out_rows.append({
            "date": d.isoformat(),
            "is_realtime": False,
            "spend_usd": _money(spend),
            "purchase_value_usd": _money(purchase),
            "roas_purchase": _roas(purchase, spend),
            "result_count": result_count_total,
            "budget_usd": None,
            "cpc_usd": _money(merged_raw["cpc_usd"]) if merged_raw["cpc_usd"] is not None else None,
            "ecpm_usd": _money(merged_raw["ecpm_usd"]) if merged_raw["ecpm_usd"] is not None else None,
            "impressions": int(merged_raw["impressions"]) if merged_raw["impressions"] is not None else None,
            "link_clicks": int(merged_raw["link_clicks"]) if merged_raw["link_clicks"] is not None else None,
            "add_to_cart_count": int(merged_raw["add_to_cart_count"]) if merged_raw["add_to_cart_count"] is not None else None,
            "initiate_checkout_count": int(merged_raw["initiate_checkout_count"]) if merged_raw["initiate_checkout_count"] is not None else None,
            "video_avg_play_time": _money(merged_raw["video_avg_play_time"]) if merged_raw["video_avg_play_time"] is not None else None,
        })

    total_spend = sum(r.get("spend_usd") or 0 for r in out_rows)
    total_purchase = sum(r.get("purchase_value_usd") or 0 for r in out_rows)
    total_results = sum(r.get("result_count") or 0 for r in out_rows)

    return {
        "level": level,
        "code": code_clean,
        "name": name_seen,
        "ad_account_id": account_id_seen,
        "ad_account_name": account_name_seen,
        "period": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        "rows": out_rows,
        "totals": {
            "spend_usd": _money(total_spend),
            "purchase_value_usd": _money(total_purchase),
            "roas_purchase": _roas(total_purchase, total_spend),
            "result_count": total_results,
            "day_count": len(out_rows),
        },
        "supports_realtime": supports_realtime,
    }
