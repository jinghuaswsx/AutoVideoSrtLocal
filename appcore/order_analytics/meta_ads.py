"""Meta 广告报表解析、导入、匹配、统计与汇总查询。

由 ``appcore.order_analytics`` package 在 PR 1.4 抽出；函数体逐字符
保留，行为不变。``__init__.py`` 通过显式 re-export 把这里的公开符号
带回 ``appcore.order_analytics`` 命名空间。
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, datetime, timedelta
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


def _normalize_ad_account_filter(ad_account_id: str | None) -> str | None:
    normalized = str(ad_account_id or "").strip().removeprefix("act_")
    return normalized or None


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


def _summary_search_matches(row: dict, q_clean: str) -> bool:
    if not q_clean:
        return True
    needle = q_clean.lower()
    haystack = " ".join(
        str(row.get(key) or "").lower()
        for key in (
            "campaign_name",
            "normalized_campaign_code",
            "matched_product_code",
            "media_product_code",
            "product_name",
        )
    )
    return needle in haystack


def _fetch_realtime_summary_metric_rows(
    business_date: date,
    *,
    q: str | None = None,
    ad_account_id: str | None = None,
) -> list[dict]:
    """Return overview-shaped rows from latest realtime snapshots for one open day.

    Docs-anchor:
    docs/superpowers/specs/2026-05-18-ads-overview-open-day-realtime-fallback.md
    """
    account_filter = _normalize_ad_account_filter(ad_account_id)
    account_clause = "AND ad_account_id = %s " if account_filter else ""
    latest_args: tuple[Any, ...] = (
        (business_date, account_filter) if account_filter else (business_date,)
    )
    latest_rows = query(
        "SELECT ad_account_id, MAX(snapshot_at) AS snapshot_at "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s AND data_completeness='realtime_partial' "
        f"{account_clause}"
        "GROUP BY ad_account_id",
        latest_args,
    ) or []

    q_clean = (q or "").strip()
    match_cache: dict[str, dict | None] = {}
    metric_rows: list[dict] = []

    for latest in latest_rows:
        snapshot_at = latest.get("snapshot_at")
        if not snapshot_at:
            continue
        latest_account_id = latest.get("ad_account_id")
        if latest_account_id is None:
            rows = query(
                "SELECT MIN(id) AS id, ad_account_id, MAX(ad_account_name) AS ad_account_name, "
                "campaign_name, normalized_campaign_code, SUM(result_count) AS result_count, "
                "SUM(spend_usd) AS spend_usd, SUM(purchase_value_usd) AS purchase_value_usd, "
                "SUM(impressions) AS impressions, SUM(clicks) AS clicks "
                "FROM meta_ad_realtime_daily_campaign_metrics "
                "WHERE business_date=%s AND ad_account_id IS NULL AND snapshot_at=%s "
                "AND data_completeness='realtime_partial' "
                "GROUP BY ad_account_id, campaign_name, normalized_campaign_code "
                "ORDER BY spend_usd DESC",
                (business_date, snapshot_at),
            )
        else:
            rows = query(
                "SELECT MIN(id) AS id, ad_account_id, MAX(ad_account_name) AS ad_account_name, "
                "campaign_name, normalized_campaign_code, SUM(result_count) AS result_count, "
                "SUM(spend_usd) AS spend_usd, SUM(purchase_value_usd) AS purchase_value_usd, "
                "SUM(impressions) AS impressions, SUM(clicks) AS clicks "
                "FROM meta_ad_realtime_daily_campaign_metrics "
                "WHERE business_date=%s AND ad_account_id=%s AND snapshot_at=%s "
                "AND data_completeness='realtime_partial' "
                "GROUP BY ad_account_id, campaign_name, normalized_campaign_code "
                "ORDER BY spend_usd DESC",
                (business_date, latest_account_id, snapshot_at),
            )

        for row in rows or []:
            code = str(
                row.get("normalized_campaign_code") or row.get("campaign_name") or ""
            ).strip().lower()
            match = None
            if code:
                if code not in match_cache:
                    match_cache[code] = _facade().resolve_ad_product_match(code)
                match = match_cache[code]

            metric = {
                "id": row.get("id"),
                "product_id": match.get("id") if match else None,
                "product_name": (
                    match.get("name") or match.get("product_name")
                ) if match else None,
                "media_product_code": match.get("product_code") if match else None,
                "matched_product_code": match.get("product_code") if match else None,
                "campaign_name": row.get("campaign_name"),
                "normalized_campaign_code": row.get("normalized_campaign_code"),
                "ad_account_id": row.get("ad_account_id"),
                "ad_account_name": row.get("ad_account_name"),
                "result_count": int(row.get("result_count") or 0),
                "spend_usd": float(row.get("spend_usd") or 0),
                "purchase_value_usd": float(row.get("purchase_value_usd") or 0),
                "link_clicks": int(row.get("clicks") or 0),
                "add_to_cart_count": 0,
                "initiate_checkout_count": 0,
                "impressions": int(row.get("impressions") or 0),
            }
            if _summary_search_matches(metric, q_clean):
                metric_rows.append(metric)

    return metric_rows


def _format_realtime_unmatched_rows(metric_rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in metric_rows:
        if row.get("product_id"):
            continue
        out.append({
            "id": row.get("id"),
            "campaign_name": row.get("campaign_name"),
            "normalized_campaign_code": row.get("normalized_campaign_code"),
            "ad_account_id": row.get("ad_account_id"),
            "ad_account_name": row.get("ad_account_name"),
            "spend_usd": _money(row.get("spend_usd") or 0),
            "result_count": int(row.get("result_count") or 0),
            "purchase_value_usd": _money(row.get("purchase_value_usd") or 0),
        })
    return out


def _summary_source_label(
    *,
    use_daily_metrics: bool,
    daily_rows: list[dict],
    realtime_rows: list[dict],
) -> str:
    if not use_daily_metrics:
        return "meta_ad_campaign_metrics"
    if realtime_rows and daily_rows:
        return "meta_ad_daily_campaign_metrics+meta_ad_realtime_daily_campaign_metrics"
    if realtime_rows:
        return "meta_ad_realtime_daily_campaign_metrics"
    return "meta_ad_daily_campaign_metrics"


def get_meta_ad_summary(
    batch_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    q: str | None = None,
    ad_account_id: str | None = None,
) -> dict:
    report_start, report_end, resolved_batch_id = _resolve_meta_ad_period(batch_id, start_date, end_date)
    if not report_start or not report_end:
        return {"period": None, "rows": [], "unmatched": []}
    if report_end < report_start:
        raise ValueError("end_date must be greater than or equal to start_date")

    use_daily_metrics = not batch_id and bool(start_date and end_date)
    q_clean = (q or "").strip()
    account_filter = _normalize_ad_account_filter(ad_account_id)
    account_args: tuple[str, ...] = (account_filter,) if account_filter else ()
    daily_account_clause = "AND m.ad_account_id = %s " if account_filter else ""
    daily_unmatched_account_clause = "AND ad_account_id = %s " if account_filter else ""
    search_args: tuple[str, ...] = ()
    daily_search_clause = ""
    batch_search_clause = ""
    daily_unmatched_search_clause = ""
    batch_unmatched_search_clause = ""
    if q_clean:
        pattern = f"%{q_clean}%"
        search_args = (pattern, pattern, pattern, pattern, pattern)
        daily_search_clause = (
            "AND (LOWER(m.campaign_name) LIKE LOWER(%s) "
            "OR LOWER(m.normalized_campaign_code) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(m.matched_product_code, m.product_code, '')) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(mp.name, '')) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(mp.product_code, '')) LIKE LOWER(%s)) "
        )
        batch_search_clause = (
            "AND (LOWER(m.campaign_name) LIKE LOWER(%s) "
            "OR LOWER(m.normalized_campaign_code) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(m.matched_product_code, '')) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(mp.name, '')) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(mp.product_code, '')) LIKE LOWER(%s)) "
        )
        daily_unmatched_search_clause = (
            "AND (LOWER(campaign_name) LIKE LOWER(%s) "
            "OR LOWER(normalized_campaign_code) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(matched_product_code, product_code, '')) LIKE LOWER(%s)) "
        )
        batch_unmatched_search_clause = (
            "AND (LOWER(campaign_name) LIKE LOWER(%s) "
            "OR LOWER(normalized_campaign_code) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(matched_product_code, '')) LIKE LOWER(%s)) "
        )
    open_business_date = current_meta_business_date()
    use_realtime_open_day = (
        use_daily_metrics
        and report_start <= open_business_date <= report_end
    )
    daily_report_end = report_end
    if use_realtime_open_day:
        daily_report_end = min(report_end, open_business_date - timedelta(days=1))
    daily_range_available = report_start <= daily_report_end
    daily_metric_rows: list[dict] = []
    realtime_metric_rows: list[dict] = []
    if use_daily_metrics:
        if daily_range_available:
            daily_metric_rows = query(
                "SELECT MIN(m.id) AS id, m.product_id, mp.name AS product_name, "
                "mp.product_code AS media_product_code, "
                "COALESCE(m.matched_product_code, m.product_code) AS matched_product_code, "
                "m.campaign_name, SUM(m.result_count) AS result_count, "
                "SUM(m.spend_usd) AS spend_usd, SUM(m.purchase_value_usd) AS purchase_value_usd, "
                "0 AS link_clicks, 0 AS add_to_cart_count, 0 AS initiate_checkout_count, 0 AS impressions "
                "FROM meta_ad_daily_campaign_metrics m "
                "LEFT JOIN media_products mp ON mp.id = m.product_id "
                "WHERE m.meta_business_date >= %s AND m.meta_business_date <= %s "
                f"{daily_account_clause}"
                f"{daily_search_clause}"
                "GROUP BY m.product_id, mp.name, mp.product_code, "
                "COALESCE(m.matched_product_code, m.product_code), m.campaign_name "
                "ORDER BY spend_usd DESC",
                (report_start, daily_report_end) + account_args + search_args,
            ) or []
        metric_rows = list(daily_metric_rows)
        if use_realtime_open_day:
            realtime_metric_rows = _fetch_realtime_summary_metric_rows(
                open_business_date,
                q=q_clean,
                ad_account_id=account_filter,
            )
            metric_rows.extend(realtime_metric_rows)
    else:
        metric_rows = query(
            "SELECT m.id, m.product_id, mp.name AS product_name, mp.product_code AS media_product_code, "
            "m.matched_product_code, m.campaign_name, m.result_count, m.spend_usd, "
            "m.purchase_value_usd, m.link_clicks, m.add_to_cart_count, "
            "m.initiate_checkout_count, m.impressions "
            "FROM meta_ad_campaign_metrics m "
            "LEFT JOIN media_products mp ON mp.id = m.product_id "
            "WHERE m.report_start_date=%s AND m.report_end_date=%s "
            f"{batch_search_clause}"
            "ORDER BY m.spend_usd DESC",
            (report_start, report_end) + search_args,
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
        unmatched = []
        if daily_range_available:
            unmatched = query(
                "SELECT MIN(id) AS id, campaign_name, normalized_campaign_code, "
                "SUM(spend_usd) AS spend_usd, SUM(result_count) AS result_count, "
                "SUM(purchase_value_usd) AS purchase_value_usd "
                "FROM meta_ad_daily_campaign_metrics "
                "WHERE meta_business_date >= %s AND meta_business_date <= %s AND product_id IS NULL "
                f"{daily_unmatched_account_clause}"
                f"{daily_unmatched_search_clause}"
                "GROUP BY campaign_name, normalized_campaign_code "
                "ORDER BY spend_usd DESC",
                (report_start, daily_report_end) + account_args + search_args[:3],
            ) or []
        if realtime_metric_rows:
            unmatched.extend(_format_realtime_unmatched_rows(realtime_metric_rows))
    else:
        unmatched = query(
            "SELECT id, campaign_name, normalized_campaign_code, spend_usd, result_count, purchase_value_usd "
            "FROM meta_ad_campaign_metrics "
            "WHERE report_start_date=%s AND report_end_date=%s AND product_id IS NULL "
            f"{batch_unmatched_search_clause}"
            "ORDER BY spend_usd DESC",
            (report_start, report_end) + search_args[:3],
        )
    return {
        "period": {
            "batch_id": resolved_batch_id,
            "report_start_date": report_start,
            "report_end_date": report_end,
            "source": _summary_source_label(
                use_daily_metrics=use_daily_metrics,
                daily_rows=daily_metric_rows,
                realtime_rows=realtime_metric_rows,
            ),
        },
        "rows": rows,
        "unmatched": unmatched,
    }


# ── 购买金额按订单口径兜底（spec: 2026-05-09-ads-purchase-value-order-fallback-design.md） ──

PURCHASE_SOURCE_META = "meta"
PURCHASE_SOURCE_ORDER_FALLBACK = "order_fallback"


def _fetch_product_revenue_for_window(
    *,
    store_codes: tuple[str, ...],
    product_codes: list[str],
    window_start: "datetime",
    window_end: "datetime",
) -> dict[str, float]:
    """Single-SQL revenue lookup for (site_code IN store_codes, product_code IN product_codes)
    over [window_start, window_end). Returns {product_code_lower: revenue_usd}.
    """
    if not store_codes or not product_codes:
        return {}
    site_placeholders = ",".join(["%s"] * len(store_codes))
    product_placeholders = ",".join(["%s"] * len(product_codes))
    order_time_expr = "COALESCE(order_paid_at, attribution_time_at, order_created_at)"
    rows = query(
        f"SELECT LOWER(product_code) AS product_code_lc, "
        f"SUM(COALESCE(line_amount, 0)) AS revenue "
        f"FROM dianxiaomi_order_lines "
        f"WHERE site_code IN ({site_placeholders}) "
        f"AND LOWER(product_code) IN ({product_placeholders}) "
        f"AND {order_time_expr} >= %s AND {order_time_expr} < %s "
        f"GROUP BY LOWER(product_code)",
        tuple(store_codes)
        + tuple(p.lower() for p in product_codes)
        + (window_start, window_end),
    ) or []
    return {
        (row.get("product_code_lc") or "").strip().lower(): float(row.get("revenue") or 0)
        for row in rows
    }


def _fetch_per_account_product_totals(
    *,
    table: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Return per-(ad_account_id, matched_product_code) spend / purchase totals for the
    date range. Used by the fallback to identify broken groups AND to compute pool
    aggregates across accounts that share a store.
    """
    rows = query(
        f"SELECT ad_account_id, LOWER(matched_product_code) AS product_code, "
        f"SUM(spend_usd) AS group_spend, SUM(purchase_value_usd) AS group_purchase "
        f"FROM {table} "
        f"WHERE meta_business_date >= %s AND meta_business_date <= %s "
        f"AND matched_product_code IS NOT NULL AND matched_product_code <> '' "
        f"GROUP BY ad_account_id, LOWER(matched_product_code)",
        (start_date, end_date),
    ) or []
    out = []
    for row in rows:
        account_id = str(row.get("ad_account_id") or "").strip().removeprefix("act_")
        product = (row.get("product_code") or "").strip().lower()
        if not account_id or not product:
            continue
        out.append({
            "account_id": account_id,
            "product_code": product,
            "group_spend": float(row.get("group_spend") or 0),
            "group_purchase": float(row.get("group_purchase") or 0),
        })
    return out


def fill_purchase_value_from_orders(
    rows: list[dict],
    *,
    level: str,
    start_date: date,
    end_date: date,
    accounts_loader=None,
) -> dict:
    """对 ``meta_ad_daily_<level>_metrics`` 中 ``(ad_account_id, matched_product_code)`` 整组
    满足 ``SUM(purchase_value_usd) == 0 & SUM(spend_usd) > 0`` 的视图行，按订单口径兜底回填
    ``purchase_value_usd``。就地修改 ``rows``，返回兜底统计：

        {"fallback_row_count": int, "fallback_revenue_total_usd": float}

    每行最终都带 ``purchase_value_source = "meta" | "order_fallback"``，命中兜底的行
    ``purchase_value_usd`` 与 ``roas_purchase``（如果原 row 上有）一并重算。

    **跨账户共享 store 时按 pool 分摊**：当多个广告账户绑定同一个 store_code（例如 newjoy
    店同时有 newjoyloo_old 与 newjoyloo_bak），订单营收是这些账户共享的；本算法先把
    ``order_revenue - 同 pool 其它账户已被 Meta 报告的购买金额`` 作为「剩余可分摊额」，
    再按 broken 账户在 pool 内的 spend 占比拿走相应份额。这样可避免单账户拿走 100%
    revenue 而忽略已 Meta 上报的部分。

    Docs-anchor: docs/superpowers/specs/2026-05-09-ads-purchase-value-order-fallback-design.md
    """
    for row in rows:
        row.setdefault("purchase_value_source", PURCHASE_SOURCE_META)

    cfg = _LEVEL_CONFIG.get((level or "").strip().lower())
    if not cfg:
        return {"fallback_row_count": 0, "fallback_revenue_total_usd": 0.0}

    per_account_totals = _fetch_per_account_product_totals(
        table=cfg["table"],
        start_date=start_date,
        end_date=end_date,
    )
    # 索引 1：`(account_id, product) -> {group_spend, group_purchase}`。
    totals_by_pair: dict[tuple[str, str], dict] = {}
    for entry in per_account_totals:
        totals_by_pair[(entry["account_id"], entry["product_code"])] = entry

    # 识别 broken groups：spend>0 且 purchase==0 → 进入兜底候选。
    broken_groups: dict[tuple[str, str], float] = {}
    for key, entry in totals_by_pair.items():
        if entry["group_spend"] > 0 and entry["group_purchase"] == 0:
            broken_groups[key] = entry["group_spend"]

    if not broken_groups:
        return {"fallback_row_count": 0, "fallback_revenue_total_usd": 0.0}

    if accounts_loader is None:
        from appcore import meta_ad_accounts

        accounts_loader = meta_ad_accounts.get_all_accounts
    accounts = accounts_loader() or []
    account_store_codes: dict[str, tuple[str, ...]] = {}
    store_to_accounts: dict[str, list[str]] = {}
    for account in accounts:
        account_id = str(account.account_id).strip().removeprefix("act_")
        store_codes = tuple(account.store_codes)
        account_store_codes[account_id] = store_codes
        for store in store_codes:
            store_to_accounts.setdefault(store, []).append(account_id)

    window_start, _ = compute_meta_business_window_bj(start_date)
    _, window_end = compute_meta_business_window_bj(end_date)

    # 索引 2：`(store_code, product) -> order_revenue_usd`（按 store 拆开，因为不同 store
    # 的 dianxiaomi_order_lines 营收池是独立的）。
    needed_store_products: dict[str, set[str]] = {}
    for (account_id, product) in broken_groups.keys():
        for store in account_store_codes.get(account_id, ()):
            needed_store_products.setdefault(store, set()).add(product)

    revenue_by_store_product: dict[tuple[str, str], float] = {}
    for store, products in needed_store_products.items():
        rev_map = _fetch_product_revenue_for_window(
            store_codes=(store,),
            product_codes=list(products),
            window_start=window_start,
            window_end=window_end,
        )
        for product, revenue in rev_map.items():
            revenue_by_store_product[(store, product)] = revenue

    # 对每个 broken pair 计算「有效兜底总额」 derived_total =
    # 该账户对应的每个 store 各自的 (剩余 revenue × 该账户在 pool 中的 spend 占比)。
    derived_total_by_pair: dict[tuple[str, str], float] = {}
    pool_spend_by_pair: dict[tuple[str, str], float] = {}
    for (account_id, product) in broken_groups.keys():
        broken_spend = broken_groups[(account_id, product)]
        derived_for_pair = 0.0
        # 跨 store 累加（多 store 账户少见，但要兼容）。
        stores_for_account = account_store_codes.get(account_id, ())
        # 单 store 的简单情形：直接套公式
        for store in stores_for_account:
            pool_account_ids = store_to_accounts.get(store, [])
            pool_spend = sum(
                totals_by_pair.get((acc, product), {}).get("group_spend", 0.0)
                for acc in pool_account_ids
            )
            pool_meta_purchase = sum(
                totals_by_pair.get((acc, product), {}).get("group_purchase", 0.0)
                for acc in pool_account_ids
            )
            if pool_spend <= 0:
                continue
            order_revenue = revenue_by_store_product.get((store, product), 0.0)
            remaining_revenue = max(0.0, order_revenue - pool_meta_purchase)
            if remaining_revenue <= 0:
                continue
            # 单 store 时账户份额 = 该账户 spend / pool_spend；
            # 多 store 时按账户在每个 store 的 spend 占比累加（这里近似用账户总 spend / pool_spend）。
            account_share = broken_spend / pool_spend
            derived_for_pair += remaining_revenue * account_share
        derived_total_by_pair[(account_id, product)] = derived_for_pair
        pool_spend_by_pair[(account_id, product)] = broken_spend  # 用作下面单行 spend 占比分母

    fallback_row_count = 0
    fallback_revenue_total_usd = 0.0

    for row in rows:
        product = (row.get("matched_product_code") or "").strip().lower()
        account_id = str(row.get("ad_account_id") or "").strip().removeprefix("act_")
        if not product or not account_id:
            continue
        key = (account_id, product)
        if key not in broken_groups:
            continue
        derived_total = derived_total_by_pair.get(key, 0.0)
        if derived_total <= 0:
            continue
        broken_account_total_spend = pool_spend_by_pair.get(key, 0.0)
        if broken_account_total_spend <= 0:
            continue
        spend_val = float(row.get("spend_usd") or 0)
        if spend_val <= 0:
            continue
        # 把 derived_total 按当行 spend / broken_account_total_spend 拆分到当前 row。
        derived = round(derived_total * (spend_val / broken_account_total_spend), 4)
        row["purchase_value_usd"] = _money(derived)
        row["purchase_value_source"] = PURCHASE_SOURCE_ORDER_FALLBACK
        if "roas_purchase" in row:
            row["roas_purchase"] = _roas(derived, spend_val)
        fallback_row_count += 1
        fallback_revenue_total_usd += derived

    return {
        "fallback_row_count": fallback_row_count,
        "fallback_revenue_total_usd": round(fallback_revenue_total_usd, 4),
    }


def _ads_purchase_data_quality(fallback_stats: dict) -> dict:
    """Build a small data_quality block describing whether order-fallback was applied to
    Meta-purchase numbers in this response. Front-end shows a banner when status is
    ``fallback_used``.
    """
    fallback_row_count = int(fallback_stats.get("fallback_row_count") or 0)
    fallback_revenue_total_usd = float(
        fallback_stats.get("fallback_revenue_total_usd") or 0
    )
    if fallback_row_count <= 0:
        return {"status": "ok", "purchase_value": {"fallback_row_count": 0}}
    return {
        "status": "fallback_used",
        "purchase_value": {
            "fallback_row_count": fallback_row_count,
            "fallback_revenue_total_usd": _money(fallback_revenue_total_usd),
            "note": (
                "Meta CSV 缺购买列（账户级 column_preset 未配置），"
                "购买金额按 dianxiaomi_order_lines 站内同产品营收 × 该行 spend 占比兜底；"
                "源 column_preset 修复后会自动回到 Meta 真值。"
            ),
        },
    }


# ── 三层级（Campaign / Ad Set / Ad）查询：list / search / detail ──────
# Docs-anchor: docs/superpowers/specs/2026-05-08-ads-analytics-tabs-design.md
# Docs-anchor: docs/superpowers/specs/2026-05-28-ads-level-realtime-default-today.md
# Docs-anchor: docs/superpowers/specs/2026-05-28-ads-hierarchy-drilldown-design.md

_LEVEL_CONFIG: dict[str, dict[str, Any]] = {
    "campaign": {
        "table": "meta_ad_daily_campaign_metrics",
        "code_col": "normalized_campaign_code",
        "name_col": "campaign_name",
        "realtime_table": "meta_ad_realtime_daily_campaign_metrics",
        "realtime_code_col": "normalized_campaign_code",
        "realtime_name_col": "campaign_name",
        "supports_realtime": True,
    },
    "adset": {
        "table": "meta_ad_daily_adset_metrics",
        "code_col": "normalized_adset_code",
        "name_col": "adset_name",
        "parent_filters": {
            "campaign": {
                "daily_col": "normalized_campaign_code",
                "realtime_col": "normalized_campaign_code",
            },
        },
        "realtime_table": "meta_ad_realtime_daily_adset_metrics",
        "realtime_code_col": "normalized_adset_code",
        "realtime_name_col": "adset_name",
        "supports_realtime": True,
    },
    "ad": {
        "table": "meta_ad_daily_ad_metrics",
        "code_col": "normalized_ad_code",
        "name_col": "ad_name",
        "parent_filters": {
            "campaign": {
                "daily_col": "normalized_campaign_code",
                "realtime_col": "normalized_campaign_code",
            },
            "adset": {
                "daily_col": "normalized_adset_code",
                "realtime_col": "normalized_adset_code",
            },
        },
        "realtime_table": "meta_ad_realtime_daily_ad_metrics",
        "realtime_code_col": "normalized_ad_code",
        "realtime_name_col": "ad_name",
        "supports_realtime": True,
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
    # CPC: 单次链接点击费用 - 独立用户 (USD) / 单次链接点击费用 (USD) / English variants.
    "cpc_usd": (
        "单次链接点击费用 - 独立用户 (USD)",
        "单次链接点击费用 (USD)",
        "每次链接点击费用 (USD)",
        "unique_link_click_cost_usd",
        "unique_link_click_cost",
        "link_click_cost",
        "cost_per_link_click",
        "cost_per_unique_link_click",
        "cpc",
    ),
    # eCPM: CPM（千次展示费用） (USD) — note Chinese full-width parens around 千次展示费用.
    "ecpm_usd": (
        "CPM（千次展示费用） (USD)",
        "CPM (千次展示费用) (USD)",
        "千次展示费用 (USD)",
        "千次展示费用",
        "cpm_usd",
        "cpm",
        "ecpm",
        "cost_per_1000_impressions",
    ),
    "impressions": (
        "展示次数",
        "展示量",
        "impressions",
    ),
    "link_clicks": (
        "链接点击量",
        "链接点击次数",
        "link_clicks",
        "linkclicks",
    ),
    "add_to_cart_count": (
        "加入购物车次数",
        "加购次数",
        "add_to_cart_count",
        "add_to_cart",
        "atc",
        "Adds to Cart",
    ),
    "initiate_checkout_count": (
        "结账发起次数",
        "发起结账次数",
        "initiate_checkout_count",
        "initiates_checkout",
        "ic",
        "Initiate Checkouts",
    ),
    "video_avg_play_time": (
        "视频平均播放时长",
        "视频均播时长",
        "video_avg_play_time",
        "video_avg_time_watched",
        "video_avg_time_watched_actions",
    ),
}


def _resolve_ads_level(level: str) -> dict:
    cfg = _LEVEL_CONFIG.get((level or "").strip().lower())
    if not cfg:
        raise ValueError("level must be one of campaign/adset/ad")
    return cfg


def _resolve_ads_parent_filter(
    cfg: dict,
    parent_level: str | None,
    parent_code: str | None,
) -> dict[str, str] | None:
    parent_level_norm = (parent_level or "").strip().lower()
    parent_code_clean = (parent_code or "").strip()
    if not parent_level_norm and not parent_code_clean:
        return None
    if not parent_level_norm or not parent_code_clean:
        raise ValueError("parent_level and parent_code must be provided together")
    filters = cfg.get("parent_filters") or {}
    parent_cfg = filters.get(parent_level_norm)
    if not parent_cfg:
        raise ValueError("unsupported ads hierarchy parent filter")
    return {
        "level": parent_level_norm,
        "code": parent_code_clean,
        "daily_col": parent_cfg["daily_col"],
        "realtime_col": parent_cfg["realtime_col"],
    }


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
    """Decode raw_json column and return the leaf metric dict.

    Production rows store the report row nested as ``{"rows": [{...metrics}], "merged_rows": N}``;
    legacy / test fixtures may store the metric dict flat. Unwrap automatically so callers
    can do flat lookups regardless of source.
    """
    if not raw_json:
        return {}
    parsed: Any = raw_json
    if isinstance(raw_json, str):
        try:
            parsed = json.loads(raw_json)
        except (TypeError, ValueError):
            return {}
    if not isinstance(parsed, dict):
        return {}
    rows = parsed.get("rows")
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return parsed


def _parse_hms_to_seconds(value: str) -> float | None:
    """Parse "HH:MM:SS" / "MM:SS" video play-time strings into seconds."""
    parts = value.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
    except (TypeError, ValueError):
        return None
    return None


def _coerce_raw_value(raw: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in raw and raw[key] not in (None, "", "—", "-", "00:00:00"):
            text = str(raw[key]).replace(",", "").replace("%", "").strip()
            if not text:
                continue
            if ":" in text:
                hms = _parse_hms_to_seconds(text)
                if hms is not None:
                    return hms
                continue
            try:
                return float(text)
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
    q: str | None = None,
    ad_account_id: str | None = None,
    parent_level: str | None = None,
    parent_code: str | None = None,
) -> dict:
    """List Campaign / Ad Set / Ad rows aggregated by code within a date range."""
    cfg = _resolve_ads_level(level)
    parent_filter = _resolve_ads_parent_filter(cfg, parent_level, parent_code)
    start, end = _coerce_ads_date_range(start_date, end_date)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))

    sort_expr = _ADS_LIST_SORT_EXPR.get(sort_by) or _ADS_LIST_SORT_EXPR["spend_usd"]
    sort_dir_norm = "ASC" if (sort_dir or "").lower() == "asc" else "DESC"

    supports_realtime = cfg["supports_realtime"]
    q_clean = (q or "").strip()
    account_filter = _normalize_ad_account_filter(ad_account_id)
    account_clause = "AND ad_account_id = %s " if account_filter else ""
    account_args: tuple[str, ...] = (account_filter,) if account_filter else ()
    parent_daily_clause = (
        f"AND {parent_filter['daily_col']} = %s " if parent_filter else ""
    )
    parent_realtime_clause = (
        f"AND m.{parent_filter['realtime_col']} = %s " if parent_filter else ""
    )
    parent_args: tuple[str, ...] = (parent_filter["code"],) if parent_filter else ()

    today = current_meta_business_date()
    use_union = supports_realtime and end >= today

    if use_union:
        # 子查询 1: 历史日终表（昨天及以前）
        hist_table_sql = (
            f"SELECT meta_business_date, {cfg['code_col']} AS code, {cfg['name_col']} AS name, "
            f"ad_account_id, ad_account_name, matched_product_code, "
            f"spend_usd, purchase_value_usd, result_count "
            f"FROM {cfg['table']} "
            f"WHERE meta_business_date >= %s AND meta_business_date <= %s "
            f"{account_clause}"
            f"{parent_daily_clause}"
        )
        hist_end = min(end, today - timedelta(days=1))
        hist_args = (start, hist_end) + account_args + parent_args

        # 子查询 2: 实时表的今日最新快照
        real_account_clause = "AND m.ad_account_id = %s " if account_filter else ""
        realtime_table = cfg["realtime_table"]
        realtime_code_col = cfg["realtime_code_col"]
        realtime_name_col = cfg["realtime_name_col"]
        realtime_sql = (
            f"SELECT m.business_date AS meta_business_date, "
            f"m.{realtime_code_col} AS code, "
            f"m.{realtime_name_col} AS name, "
            f"m.ad_account_id, m.ad_account_name, "
            f"NULL AS matched_product_code, "
            f"m.spend_usd, m.purchase_value_usd, m.result_count "
            f"FROM {realtime_table} m "
            f"INNER JOIN ("
            f"  SELECT business_date, ad_account_id, MAX(snapshot_at) AS max_snapshot_at "
            f"  FROM {realtime_table} "
            f"  WHERE business_date = %s AND data_completeness = 'realtime_partial' "
            f"  GROUP BY business_date, ad_account_id "
            f") latest "
            f"ON m.business_date = latest.business_date "
            f"AND m.ad_account_id = latest.ad_account_id "
            f"AND m.snapshot_at = latest.max_snapshot_at "
            f"WHERE m.business_date = %s AND m.data_completeness = 'realtime_partial' "
            f"{real_account_clause}"
            f"{parent_realtime_clause}"
        )
        real_args = (today, today) + account_args + parent_args

        table = f"( {hist_table_sql} UNION ALL {realtime_sql} ) AS combined_t"
        code_col = "code"
        name_col = "name"
        subquery_args = hist_args + real_args
        where_clause = "WHERE 1=1"
        where_args = ()
    else:
        table = cfg["table"]
        code_col = cfg["code_col"]
        name_col = cfg["name_col"]
        subquery_args = ()
        where_clause = (
            f"WHERE meta_business_date >= %s AND meta_business_date <= %s "
            f"{account_clause}"
            f"{parent_daily_clause}"
        )
        where_args = (start, end) + account_args + parent_args

    search_clause = ""
    search_args: tuple[str, ...] = ()
    if q_clean:
        pattern = f"%{q_clean}%"
        filter_name_col = "name" if use_union else name_col
        filter_code_col = "code" if use_union else code_col
        search_clause = (
            f"AND (LOWER({filter_name_col}) LIKE LOWER(%s) "
            f"OR LOWER({filter_code_col}) LIKE LOWER(%s) "
            "OR LOWER(COALESCE(matched_product_code, '')) LIKE LOWER(%s)) "
        )
        search_args = (pattern, pattern, pattern)

    query_args = subquery_args + where_args + search_args

    # 按 (code, ad_account_id) 分组
    total_row = query_one(
        f"SELECT COUNT(*) AS total FROM ("
        f"SELECT 1 FROM {table} "
        f"{where_clause} "
        f"{search_clause}"
        f"GROUP BY {code_col}, ad_account_id"
        f") AS count_t",
        query_args,
    )
    total = int((total_row or {}).get("total") or 0)

    offset = (page - 1) * page_size
    rows = query(
        f"SELECT {code_col} AS code, MAX({name_col}) AS name, "
        "ad_account_id, MAX(ad_account_name) AS ad_account_name, "
        "MAX(matched_product_code) AS matched_product_code, "
        "SUM(spend_usd) AS spend_usd, SUM(purchase_value_usd) AS purchase_value_usd, "
        "SUM(result_count) AS result_count, "
        "COUNT(DISTINCT meta_business_date) AS day_count, "
        "(SUM(purchase_value_usd) / NULLIF(SUM(spend_usd), 0)) AS roas_purchase "
        f"FROM {table} "
        f"{where_clause} "
        f"{search_clause}"
        f"GROUP BY {code_col}, ad_account_id "
        f"ORDER BY {sort_expr} {sort_dir_norm} "
        "LIMIT %s OFFSET %s",
        query_args + (page_size, offset),
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
            "matched_product_code": row.get("matched_product_code"),
            "spend_usd": _money(spend),
            "purchase_value_usd": _money(purchase),
            "roas_purchase": _roas(purchase, spend),
            "result_count": int(row.get("result_count") or 0),
            "day_count": int(row.get("day_count") or 0),
        })

    # 为没有 matched_product_code 且来自今日实时快照的数据，前置通过解析/人工配置规则补全产品关联
    for row in out:
        if not row.get("matched_product_code") and row.get("code"):
            match = resolve_ad_product_match(row["code"])
            if match:
                row["matched_product_code"] = match.get("product_code")

    fallback_stats = _facade().fill_purchase_value_from_orders(
        out,
        level=level,
        start_date=start,
        end_date=end,
    )

    dq_result = _ads_purchase_data_quality(fallback_stats)

    result = {
        "level": level,
        "period": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        "rows": out,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": (page * page_size) < total,
        "data_quality": dq_result,
    }
    if parent_filter:
        result["parent"] = {
            "level": parent_filter["level"],
            "code": parent_filter["code"],
        }
    return result


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


def _fetch_realtime_today_level(
    cfg: dict[str, Any],
    code: str,
    business_date: date,
    ad_account_id: str | None = None,
) -> dict | None:
    """Latest-snapshot-per-account aggregation for one ads-level code on `business_date`.

    Mirrors the (business_date, ad_account_id) -> MAX(snapshot_at) rule documented in
    CLAUDE.md "Meta 广告多账户同步" — DO NOT use a global MAX(snapshot_at).
    """
    realtime_table = cfg["realtime_table"]
    realtime_code_col = cfg["realtime_code_col"]
    realtime_name_col = cfg["realtime_name_col"]
    account_filter = _normalize_ad_account_filter(ad_account_id)
    inner_account_clause = "AND ad_account_id = %s " if account_filter else ""
    outer_account_clause = "AND m.ad_account_id = %s " if account_filter else ""
    args: list[Any] = [business_date, code]
    if account_filter:
        args.append(account_filter)
    args.extend([business_date, code])
    if account_filter:
        args.append(account_filter)
    row = query_one(
        "SELECT SUM(m.spend_usd) AS spend_usd, "
        "SUM(m.purchase_value_usd) AS purchase_value_usd, "
        "SUM(m.result_count) AS result_count, "
        "SUM(m.impressions) AS impressions, "
        "SUM(m.clicks) AS clicks, "
        "MAX(m.snapshot_at) AS snapshot_at, "
        f"MAX(m.{realtime_name_col}) AS name, "
        "GROUP_CONCAT(DISTINCT m.ad_account_id) AS ad_account_id, "
        "GROUP_CONCAT(DISTINCT m.ad_account_name) AS ad_account_name "
        f"FROM {realtime_table} m "
        "INNER JOIN ("
        "  SELECT business_date, ad_account_id, MAX(snapshot_at) AS max_snapshot_at "
        f"  FROM {realtime_table} "
        f"  WHERE business_date = %s AND {realtime_code_col} = %s "
        "  AND data_completeness = 'realtime_partial' "
        f"{inner_account_clause}"
        "  GROUP BY business_date, ad_account_id "
        ") latest "
        "ON m.business_date = latest.business_date "
        "AND m.ad_account_id = latest.ad_account_id "
        "AND m.snapshot_at = latest.max_snapshot_at "
        f"WHERE m.business_date = %s AND m.{realtime_code_col} = %s "
        "AND m.data_completeness = 'realtime_partial' "
        f"{outer_account_clause}",
        tuple(args),
    )
    if not row or row.get("spend_usd") is None:
        return None
    return row


def get_ads_level_detail(
    level: str,
    code: str,
    start_date: str | None = None,
    end_date: str | None = None,
    ad_account_id: str | None = None,
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
    account_filter = _normalize_ad_account_filter(ad_account_id)
    account_clause = "AND ad_account_id = %s " if account_filter else ""
    account_args: tuple[str, ...] = (account_filter,) if account_filter else ()

    raw_rows = query(
        f"SELECT meta_business_date, {name_col} AS name, "
        "ad_account_id, ad_account_name, matched_product_code, "
        "spend_usd, purchase_value_usd, result_count, raw_json "
        f"FROM {table} "
        f"WHERE {code_col} = %s "
        "AND meta_business_date >= %s AND meta_business_date <= %s "
        f"{account_clause}"
        "ORDER BY meta_business_date DESC",
        (code_clean, start, end) + account_args,
    )

    today = current_meta_business_date()
    realtime_row = None
    if supports_realtime and end >= today:
        realtime_row = _fetch_realtime_today_level(cfg, code_clean, today, account_filter)

    daily_by_date: dict[date, list[dict]] = {}
    name_seen = None
    account_id_seen = None
    account_name_seen = None
    matched_product_seen = None
    for row in raw_rows or []:
        d = row.get("meta_business_date")
        if not d:
            continue
        daily_by_date.setdefault(d, []).append(row)
        name_seen = name_seen or row.get("name")
        account_id_seen = account_id_seen or row.get("ad_account_id")
        account_name_seen = account_name_seen or row.get("ad_account_name")
        matched_product_seen = matched_product_seen or row.get("matched_product_code")

    if realtime_row and not name_seen:
        name_seen = realtime_row.get("name")
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
            "ad_account_id": account_id_seen,
            "matched_product_code": matched_product_seen,
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

    # 兜底：仅对历史日（非 realtime）应用，因为实时表 ad/adset 级没有数据，
    # 且 realtime row 直接来自 meta_ad_realtime_daily_campaign_metrics，不归本兜底覆盖。
    fallback_targets = [r for r in out_rows if not r.get("is_realtime")]
    fallback_stats = _facade().fill_purchase_value_from_orders(
        fallback_targets,
        level=level,
        start_date=start,
        end_date=end,
    )

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
        "data_quality": _ads_purchase_data_quality(fallback_stats),
    }
