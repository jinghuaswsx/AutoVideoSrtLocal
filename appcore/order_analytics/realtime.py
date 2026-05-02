"""实时大盘 / 真实 ROAS 查询：当天 partial snapshot + 历史日级最终报表。

由 ``appcore.order_analytics`` package 在 PR 1.5 抽出；函数体逐字符
保留，行为不变。``__init__.py`` 通过显式 re-export 把这里的公开符号
带回 ``appcore.order_analytics`` 命名空间。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from typing import Any

from ._constants import META_ATTRIBUTION_CUTOVER_HOUR_BJ, META_ATTRIBUTION_TIMEZONE
from ._helpers import (
    _beijing_now,
    current_meta_business_date,
    _business_hour,
    _money,
    _parse_iso_date_param,
    _revenue_with_shipping,
    _roas,
)
from .dianxiaomi import compute_meta_business_window_bj


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


def _get_realtime_order_details(target: date, day_start: datetime, data_until: datetime) -> list[dict[str, Any]]:
    order_time_expr = "COALESCE(order_paid_at, attribution_time_at, order_created_at)"
    rows = query(
        "SELECT site_code, dxm_package_id, dxm_order_id, package_number, order_state, "
        "buyer_country, buyer_country_name, " + order_time_expr + " AS order_time, "
        "COUNT(*) AS line_count, SUM(COALESCE(quantity, 0)) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS product_revenue, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue, "
        "SUM(COALESCE(line_amount, 0)) + SUM(COALESCE(ship_amount, 0)) AS total_revenue, "
        "GROUP_CONCAT(DISTINCT NULLIF(product_sku, '') ORDER BY product_sku SEPARATOR ' / ') AS skus, "
        "GROUP_CONCAT(DISTINCT NULLIF(product_name, '') ORDER BY product_name SEPARATOR ' / ') AS product_names "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND meta_business_date=%s "
        "AND " + order_time_expr + " <= %s "
        "GROUP BY site_code, dxm_package_id, dxm_order_id, package_number, order_state, "
        "buyer_country, buyer_country_name, " + order_time_expr + " "
        "ORDER BY order_time DESC, dxm_package_id DESC",
        (target, data_until),
    )
    details: list[dict[str, Any]] = []
    for row in rows:
        order_time = row.get("order_time")
        details.append({
            "order_time": order_time,
            "business_hour": _business_hour(order_time, day_start),
            "site_code": row.get("site_code"),
            "dxm_package_id": row.get("dxm_package_id"),
            "dxm_order_id": row.get("dxm_order_id"),
            "package_number": row.get("package_number"),
            "order_state": row.get("order_state"),
            "buyer_country": row.get("buyer_country"),
            "buyer_country_name": row.get("buyer_country_name"),
            "line_count": int(row.get("line_count") or 0),
            "units": int(row.get("units") or 0),
            "product_revenue": _money(row.get("product_revenue")),
            "shipping_revenue": _money(row.get("shipping_revenue")),
            "total_revenue": _money(row.get("total_revenue")),
            "skus": row.get("skus"),
            "product_names": row.get("product_names"),
        })
    return details


def _get_realtime_campaign_details(target: date, snapshot_at: datetime | None) -> list[dict[str, Any]]:
    if not snapshot_at:
        return []
    rows = query(
        "SELECT ad_account_id, ad_account_name, campaign_id, campaign_name, normalized_campaign_code, "
        "result_count, spend_usd, purchase_value_usd, impressions, clicks "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s AND snapshot_at=%s AND data_completeness='realtime_partial' "
        "ORDER BY spend_usd DESC, campaign_name",
        (target, snapshot_at),
    )
    campaigns: list[dict[str, Any]] = []
    for row in rows:
        spend = _money(row.get("spend_usd"))
        purchase_value = _money(row.get("purchase_value_usd"))
        campaigns.append({
            "ad_account_id": row.get("ad_account_id"),
            "ad_account_name": row.get("ad_account_name"),
            "campaign_id": row.get("campaign_id"),
            "campaign_name": row.get("campaign_name"),
            "normalized_campaign_code": row.get("normalized_campaign_code"),
            "result_count": int(row.get("result_count") or 0),
            "spend_usd": spend,
            "purchase_value_usd": purchase_value,
            "platform_roas": _roas(purchase_value, spend),
            "impressions": int(row.get("impressions") or 0),
            "clicks": int(row.get("clicks") or 0),
        })
    return campaigns


def _get_daily_campaigns(target: date) -> list[dict[str, Any]]:
    """从 Meta 日级最终报表按 campaign 聚合，字段对齐实时表的 campaign_details。"""
    rows = query(
        "SELECT ad_account_id, ad_account_name, campaign_name, normalized_campaign_code, "
        "SUM(result_count) AS result_count, "
        "SUM(spend_usd) AS spend, "
        "SUM(purchase_value_usd) AS purchase_value "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date=%s "
        "GROUP BY ad_account_id, ad_account_name, campaign_name, normalized_campaign_code "
        "ORDER BY spend DESC, campaign_name",
        (target,),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        spend = _money(row.get("spend"))
        purchase_value = _money(row.get("purchase_value"))
        out.append({
            "ad_account_id": row.get("ad_account_id"),
            "ad_account_name": row.get("ad_account_name"),
            "campaign_id": None,
            "campaign_name": row.get("campaign_name"),
            "normalized_campaign_code": row.get("normalized_campaign_code"),
            "result_count": int(row.get("result_count") or 0),
            "spend_usd": spend,
            "purchase_value_usd": purchase_value,
            "platform_roas": _roas(purchase_value, spend),
            "impressions": 0,
            "clicks": 0,
        })
    return out


def _get_today_realtime_meta_totals(business_date: date) -> dict[str, Any] | None:
    """对当天广告系统日，从 Meta 实时抓取表汇总最新 snapshot 的总值。

    每天导出的 daily report 在当日往往还没有数据；为了让"真实 ROAS"列表对当天行
    也能展示真实的 Meta 广告费/购物价值，落到实时表上拿最近一次 partial snapshot。
    没数据时返回 None。
    """
    rows = query(
        "SELECT MAX(snapshot_at) AS snapshot_at FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s",
        (business_date,),
    )
    snapshot_at = rows[0].get("snapshot_at") if rows else None
    if not snapshot_at:
        return None
    agg = query(
        "SELECT SUM(spend_usd) AS ad_spend, "
        "SUM(purchase_value_usd) AS meta_purchase_value, "
        "SUM(result_count) AS meta_purchases "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s AND snapshot_at=%s",
        (business_date, snapshot_at),
    )
    if not agg:
        return None
    row = agg[0]
    return {
        "ad_spend": _money(row.get("ad_spend")),
        "meta_purchase_value": _money(row.get("meta_purchase_value")),
        "meta_purchases": int(row.get("meta_purchases") or 0),
        "snapshot_at": snapshot_at,
    }


def _get_realtime_ad_updated_at(target: date, snapshot_at: datetime | None) -> datetime | None:
    if not snapshot_at:
        return None
    row = query(
        "SELECT COALESCE(MAX(r.finished_at), MAX(m.updated_at), MAX(m.created_at)) AS last_ad_updated_at "
        "FROM meta_ad_realtime_daily_campaign_metrics m "
        "LEFT JOIN meta_ad_realtime_import_runs r ON r.id=m.import_run_id "
        "WHERE m.business_date=%s AND m.snapshot_at=%s AND m.data_completeness='realtime_partial'",
        (target, snapshot_at),
    )
    if not row:
        return None
    return row[0].get("last_ad_updated_at")


def _build_realtime_overview_for_range(start: date, end: date, now: datetime) -> dict:
    """范围分支：只返回 summary + freshness + period，不返回 hourly / 明细。

    复用 get_true_roas_summary 同款 SQL（按 meta_business_date 聚合 dxm 订单和 Meta 广告），
    但不依赖 _beijing_now，避免范围内的"今天" partial 覆盖逻辑。
    """
    order_rows = query(
        "SELECT meta_business_date, "
        "COUNT(DISTINCT dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(quantity) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS order_revenue, "
        "SUM(COALESCE(line_amount, 0)) AS line_revenue, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue, "
        "MAX(COALESCE(order_paid_at, attribution_time_at, order_created_at)) AS last_order_at "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND meta_business_date >= %s AND meta_business_date <= %s "
        "GROUP BY meta_business_date",
        (start, end),
    )
    ad_rows = query(
        "SELECT meta_business_date, "
        "SUM(spend_usd) AS ad_spend, "
        "SUM(purchase_value_usd) AS meta_purchase_value, "
        "SUM(result_count) AS meta_purchases, "
        "MAX(updated_at) AS last_ad_updated_at "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        "GROUP BY meta_business_date",
        (start, end),
    )

    summary = {
        "order_count": 0,
        "line_count": 0,
        "units": 0,
        "order_revenue": 0.0,
        "line_revenue": 0.0,
        "shipping_revenue": 0.0,
        "ad_spend": 0.0,
        "meta_purchase_value": 0.0,
        "meta_purchases": 0,
    }
    last_order_at: datetime | None = None
    last_ad_updated_at: datetime | None = None

    for row in order_rows:
        summary["order_count"] += int(row.get("order_count") or 0)
        summary["line_count"] += int(row.get("line_count") or 0)
        summary["units"] += int(row.get("units") or 0)
        summary["order_revenue"] += float(row.get("order_revenue") or 0)
        summary["line_revenue"] += float(row.get("line_revenue") or 0)
        summary["shipping_revenue"] += float(row.get("shipping_revenue") or 0)
        if row.get("last_order_at") and (last_order_at is None or row["last_order_at"] > last_order_at):
            last_order_at = row["last_order_at"]
    for row in ad_rows:
        summary["ad_spend"] += float(row.get("ad_spend") or 0)
        summary["meta_purchase_value"] += float(row.get("meta_purchase_value") or 0)
        summary["meta_purchases"] += int(row.get("meta_purchases") or 0)
        if row.get("last_ad_updated_at") and (last_ad_updated_at is None or row["last_ad_updated_at"] > last_ad_updated_at):
            last_ad_updated_at = row["last_ad_updated_at"]

    for key in ("order_revenue", "line_revenue", "shipping_revenue", "ad_spend", "meta_purchase_value"):
        summary[key] = round(summary[key], 2)

    summary["revenue_with_shipping"] = _revenue_with_shipping(summary["order_revenue"], summary["shipping_revenue"])
    summary["true_roas"] = _roas(summary["revenue_with_shipping"], summary["ad_spend"])
    summary["meta_roas"] = _roas(summary["meta_purchase_value"], summary["ad_spend"])
    summary["order_data_status"] = "ok"
    summary["ad_data_status"] = "ok"

    range_start_at, _ = compute_meta_business_window_bj(start)
    _, range_end_at = compute_meta_business_window_bj(end)

    return {
        "period": {
            "start_date": start,
            "end_date": end,
            "timezone": META_ATTRIBUTION_TIMEZONE,
            "day_start_at": range_start_at,
            "day_end_at": range_end_at,
            "data_until_at": last_ad_updated_at or last_order_at,
            "complete_hour_until_at": range_end_at,
            "meta_cutover_hour_bj": META_ATTRIBUTION_CUTOVER_HOUR_BJ,
            "day_definition": "meta_ad_platform_business_day_range",
        },
        "scope": {
            "stores": ["newjoy", "omurio"],
            "ad_platforms": ["meta"],
            "order_source": "dianxiaomi",
            "ad_source": "meta_ad_daily_campaign_metrics",
            "ad_granularity": "daily",
            "hourly_ad_ready": False,
        },
        "freshness": {
            "first_order_at": None,
            "last_order_at": last_order_at,
            "last_ad_updated_at": last_ad_updated_at,
        },
        "summary": summary,
        "hourly": [],
        "roas_points": [],
        "snapshots": [],
        "order_details": [],
        "campaigns": [],
    }


def get_realtime_roas_overview(
    date_text: str | None = None,
    now: datetime | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    now = (now or _beijing_now()).replace(microsecond=0)

    # 范围模式：start_date / end_date 同时给出，且为不同日期 → 走范围聚合分支
    if start_date and end_date:
        start = _parse_iso_date_param(start_date, "start_date")
        end = _parse_iso_date_param(end_date, "end_date")
        if end < start:
            raise ValueError("end_date must be >= start_date")
        if start != end:
            return _build_realtime_overview_for_range(start, end, now)
        # start == end → 走单日分支，把 start_date 作为目标日
        date_text = start_date

    target = _parse_iso_date_param(date_text, "date") if date_text else current_meta_business_date(now)
    day_start, day_end = compute_meta_business_window_bj(target)
    current_business_date = current_meta_business_date(now)
    if target == current_business_date:
        data_until = min(now, day_end)
        complete_hour_until = now.replace(minute=0, second=0, microsecond=0)
    elif target < current_business_date:
        data_until = day_end
        complete_hour_until = day_end
    else:
        data_until = day_start
        complete_hour_until = day_start

    roas_node_rows = query(
        "SELECT node_hour, node_at, order_count, units, order_revenue_usd, "
        "shipping_revenue_usd, ad_spend_usd, true_roas, order_data_status, ad_data_status "
        "FROM roi_daily_roas_nodes "
        "WHERE business_date=%s AND store_scope='newjoy,omurio' AND ad_platform_scope='meta' "
        "ORDER BY node_hour",
        (target,),
    )
    roas_nodes_by_hour = {int(row["node_hour"]): row for row in roas_node_rows if row.get("node_hour") is not None}
    roas_points = [
        {
            "hour": hour,
            "node_at": (roas_nodes_by_hour.get(hour) or {}).get("node_at"),
            "order_count": int((roas_nodes_by_hour.get(hour) or {}).get("order_count") or 0),
            "units": int((roas_nodes_by_hour.get(hour) or {}).get("units") or 0),
            "order_revenue": _money((roas_nodes_by_hour.get(hour) or {}).get("order_revenue_usd")),
            "shipping_revenue": _money((roas_nodes_by_hour.get(hour) or {}).get("shipping_revenue_usd")),
            "ad_spend": _money((roas_nodes_by_hour.get(hour) or {}).get("ad_spend_usd")),
            "true_roas": (
                round(float((roas_nodes_by_hour.get(hour) or {}).get("true_roas")), 4)
                if (roas_nodes_by_hour.get(hour) or {}).get("true_roas") is not None
                else None
            ),
            "order_data_status": (roas_nodes_by_hour.get(hour) or {}).get("order_data_status"),
            "ad_data_status": (roas_nodes_by_hour.get(hour) or {}).get("ad_data_status"),
        }
        for hour in range(24)
    ]

    # 历史日期直接走主路径（日级最终报表 + dxm 订单日表），避免被实时 partial 截胡且数据已过期。
    # 仅"当天"和"未来"日期下才尝试 ROI 实时快照。
    latest_snapshot = query(
        "SELECT * FROM roi_realtime_daily_snapshots "
        "WHERE business_date=%s AND store_scope='newjoy,omurio' AND ad_platform_scope='meta' "
        "ORDER BY CASE WHEN ad_data_status='ok' THEN 0 ELSE 1 END, snapshot_at DESC, id DESC LIMIT 1",
        (target,),
    ) if target >= current_business_date else []
    if latest_snapshot:
        snap = latest_snapshot[0]
        snapshot_at = snap.get("snapshot_at") or data_until
        order_revenue = _money(snap.get("order_revenue_usd"))
        shipping_revenue = _money(snap.get("shipping_revenue_usd"))
        revenue_with_shipping = _revenue_with_shipping(order_revenue, shipping_revenue)
        ad_spend = _money(snap.get("ad_spend_usd"))
        order_details = _get_realtime_order_details(target, day_start, snapshot_at)
        campaign_details = _get_realtime_campaign_details(target, snapshot_at)
        last_ad_updated_at = _get_realtime_ad_updated_at(target, snapshot_at)
        return {
            "period": {
                "date": target,
                "timezone": META_ATTRIBUTION_TIMEZONE,
                "day_start_at": day_start,
                "day_end_at": day_end,
                "data_until_at": snapshot_at,
                "complete_hour_until_at": complete_hour_until,
                "meta_cutover_hour_bj": META_ATTRIBUTION_CUTOVER_HOUR_BJ,
                "day_definition": "meta_ad_platform_business_day",
            },
            "scope": {
                "stores": ["newjoy", "omurio"],
                "ad_platforms": ["meta"],
                "order_source": "dianxiaomi",
                "ad_source": "roi_realtime_daily_snapshots",
                "ad_granularity": "day_realtime_snapshot",
                "hourly_ad_ready": False,
            },
            "freshness": {
                "first_order_at": None,
                "last_order_at": snap.get("last_order_at"),
                "last_ad_updated_at": last_ad_updated_at,
            },
            "summary": {
                "order_count": int(snap.get("order_count") or 0),
                "line_count": int(snap.get("line_count") or 0),
                "units": int(snap.get("units") or 0),
                "order_revenue": order_revenue,
                "revenue_with_shipping": revenue_with_shipping,
                "line_revenue": 0.0,
                "shipping_revenue": shipping_revenue,
                "ad_spend": ad_spend,
                "meta_purchase_value": round(sum(c["purchase_value_usd"] for c in campaign_details), 2) if campaign_details else 0.0,
                "meta_purchases": sum(c["result_count"] for c in campaign_details) if campaign_details else 0,
                "true_roas": _roas(revenue_with_shipping, ad_spend),
                "order_data_status": snap.get("order_data_status") or "ok",
                "ad_data_status": snap.get("ad_data_status") or "pending_source",
            },
            "hourly": [],
            "roas_points": roas_points,
            "snapshots": [snap],
            "order_details": order_details,
            "campaigns": campaign_details,
        }

    order_time_expr = "COALESCE(order_paid_at, attribution_time_at, order_created_at)"
    order_rows = query(
        "SELECT HOUR(" + order_time_expr + ") AS hour, "
        "COUNT(DISTINCT dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(quantity) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS order_revenue, "
        "SUM(COALESCE(line_amount, 0)) AS line_revenue, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue, "
        "MIN(" + order_time_expr + ") AS first_order_at, "
        "MAX(" + order_time_expr + ") AS last_order_at "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND " + order_time_expr + " >= %s AND " + order_time_expr + " < %s "
        "GROUP BY HOUR(" + order_time_expr + ") "
        "ORDER BY hour",
        (day_start, day_end),
    )
    ad_rows = query(
        "SELECT SUM(spend_usd) AS ad_spend, "
        "SUM(purchase_value_usd) AS meta_purchase_value, "
        "SUM(result_count) AS meta_purchases, "
        "MAX(updated_at) AS last_ad_updated_at "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date = %s",
        (target,),
    )

    orders_by_hour = {int(row["hour"]): row for row in order_rows if row.get("hour") is not None}
    ad = ad_rows[0] if ad_rows else {}
    summary = {
        "order_count": 0,
        "line_count": 0,
        "units": 0,
        "order_revenue": 0.0,
        "line_revenue": 0.0,
        "shipping_revenue": 0.0,
        "ad_spend": _money(ad.get("ad_spend")),
        "meta_purchase_value": _money(ad.get("meta_purchase_value")),
        "meta_purchases": int(ad.get("meta_purchases") or 0),
    }
    first_order_at = None
    last_order_at = None
    hourly: list[dict[str, Any]] = []
    for hour in range(24):
        row = orders_by_hour.get(hour, {})
        order_revenue = _money(row.get("order_revenue"))
        item = {
            "hour": hour,
            "window_start_at": day_start + timedelta(hours=hour),
            "window_end_at": day_start + timedelta(hours=hour + 1),
            "order_count": int(row.get("order_count") or 0),
            "line_count": int(row.get("line_count") or 0),
            "units": int(row.get("units") or 0),
            "order_revenue": order_revenue,
            "line_revenue": _money(row.get("line_revenue")),
            "shipping_revenue": _money(row.get("shipping_revenue")),
            "ad_spend": None,
            "true_roas": None,
        }
        hourly.append(item)
        for key in ("order_count", "line_count", "units"):
            summary[key] += item[key]
        for key in ("order_revenue", "line_revenue", "shipping_revenue"):
            summary[key] = round(summary[key] + float(item[key]), 2)
        if row.get("first_order_at") and (first_order_at is None or row["first_order_at"] < first_order_at):
            first_order_at = row["first_order_at"]
        if row.get("last_order_at") and (last_order_at is None or row["last_order_at"] > last_order_at):
            last_order_at = row["last_order_at"]

    summary["revenue_with_shipping"] = _revenue_with_shipping(summary["order_revenue"], summary["shipping_revenue"])
    summary["true_roas"] = _roas(summary["revenue_with_shipping"], summary["ad_spend"])
    return {
        "period": {
            "date": target,
            "timezone": META_ATTRIBUTION_TIMEZONE,
            "day_start_at": day_start,
            "day_end_at": day_end,
            "data_until_at": data_until,
            "complete_hour_until_at": complete_hour_until,
            "meta_cutover_hour_bj": META_ATTRIBUTION_CUTOVER_HOUR_BJ,
            "day_definition": "meta_ad_platform_business_day",
        },
        "scope": {
            "stores": ["newjoy", "omurio"],
            "ad_platforms": ["meta"],
            "order_source": "dianxiaomi",
            "ad_source": "meta_ad_daily_campaign_metrics",
            "ad_granularity": "daily",
            "hourly_ad_ready": False,
        },
        "freshness": {
            "first_order_at": first_order_at,
            "last_order_at": last_order_at,
            "last_ad_updated_at": ad.get("last_ad_updated_at"),
        },
        "summary": summary,
        "hourly": hourly,
        "roas_points": roas_points,
        "order_details": _get_realtime_order_details(target, day_start, data_until),
        "campaigns": _get_daily_campaigns(target),
    }


def get_true_roas_summary(start_date: str, end_date: str) -> dict:
    start = _parse_iso_date_param(start_date, "start_date")
    end = _parse_iso_date_param(end_date, "end_date")
    if end < start:
        raise ValueError("end_date must be >= start_date")

    order_rows = query(
        "SELECT meta_business_date, "
        "COUNT(DISTINCT dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(quantity) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS order_revenue, "
        "SUM(COALESCE(line_amount, 0)) AS line_revenue, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue "
        "FROM dianxiaomi_order_lines "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        "GROUP BY meta_business_date",
        (start, end),
    )
    ad_rows = query(
        "SELECT meta_business_date, "
        "SUM(spend_usd) AS ad_spend, "
        "SUM(purchase_value_usd) AS meta_purchase_value, "
        "SUM(result_count) AS meta_purchases "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        "GROUP BY meta_business_date",
        (start, end),
    )

    orders_by_day = {row["meta_business_date"]: row for row in order_rows}
    ads_by_day = {row["meta_business_date"]: row for row in ad_rows}
    today_business = current_meta_business_date()
    rows: list[dict[str, Any]] = []
    totals = {
        "order_count": 0,
        "line_count": 0,
        "units": 0,
        "order_revenue": 0.0,
        "line_revenue": 0.0,
        "shipping_revenue": 0.0,
        "ad_spend": 0.0,
        "meta_purchase_value": 0.0,
        "meta_purchases": 0,
    }

    current = start
    while current <= end:
        order = orders_by_day.get(current, {})
        ad = ads_by_day.get(current, {})
        # 当天行：daily report 还没出，从 Meta 实时抓取表覆盖
        if current == today_business:
            realtime = _get_today_realtime_meta_totals(current)
            if realtime:
                ad = realtime
        window_start, window_end = compute_meta_business_window_bj(current)
        order_revenue = _money(order.get("order_revenue"))
        shipping_revenue = _money(order.get("shipping_revenue"))
        revenue_with_shipping = _revenue_with_shipping(order_revenue, shipping_revenue)
        ad_spend = _money(ad.get("ad_spend"))
        meta_purchase_value = _money(ad.get("meta_purchase_value"))
        item = {
            "meta_business_date": current,
            "window_start_at": window_start,
            "window_end_at": window_end,
            "order_count": int(order.get("order_count") or 0),
            "line_count": int(order.get("line_count") or 0),
            "units": int(order.get("units") or 0),
            "order_revenue": order_revenue,
            "line_revenue": _money(order.get("line_revenue")),
            "shipping_revenue": shipping_revenue,
            "revenue_with_shipping": revenue_with_shipping,
            "ad_spend": ad_spend,
            "true_roas": _roas(revenue_with_shipping, ad_spend),
            "meta_purchase_value": meta_purchase_value,
            "meta_roas": _roas(meta_purchase_value, ad_spend),
            "meta_purchases": int(ad.get("meta_purchases") or 0),
        }
        rows.append(item)
        for key in totals:
            totals[key] += item[key]
        current += timedelta(days=1)

    for key in ("order_revenue", "line_revenue", "shipping_revenue", "ad_spend", "meta_purchase_value"):
        totals[key] = round(float(totals[key]), 2)
    summary = dict(totals)
    summary["revenue_with_shipping"] = _revenue_with_shipping(summary["order_revenue"], summary["shipping_revenue"])
    summary["true_roas"] = _roas(summary["revenue_with_shipping"], summary["ad_spend"])
    summary["meta_roas"] = _roas(summary["meta_purchase_value"], summary["ad_spend"])
    return {
        "period": {
            "start": start,
            "end": end,
            "timezone": META_ATTRIBUTION_TIMEZONE,
            "cutover_hour_bj": META_ATTRIBUTION_CUTOVER_HOUR_BJ,
        },
        "summary": summary,
        "rows": rows,
    }

