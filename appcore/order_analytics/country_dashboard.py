"""国家维度 Dashboard：按订单量从高到低排序的国家分布。

由 ``appcore.order_analytics`` package 在 PR 1.7 抽出；函数体逐字符
保留，行为不变。``__init__.py`` 通过显式 re-export 把这里的公开符号
带回 ``appcore.order_analytics`` 命名空间。

跨子模块依赖：``get_country_dashboard`` 调用 dashboard 域的
``_resolve_period_range`` 和 ``_format_period_label`` —— 当前仍在
``__init__.py``，PR 1.8 拆出 ``dashboard.py`` 后移过去。本模块通过
``_facade()`` 间接调用，确保拆分顺序不影响 import 链。
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from typing import Any

from ._constants import META_ATTRIBUTION_TIMEZONE
from ._helpers import _money, _parse_iso_date_param, _revenue_with_shipping


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


def _sort_order_dashboard_rows(rows: list[dict], *, name_key: str) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            -(int(row.get("orders") or row.get("order_count") or 0)),
            -(float(row.get("revenue") or row.get("total_sales") or 0)),
            str(row.get(name_key) or "").lower(),
        ),
    )


from decimal import Decimal

_COUNTRY_NAMES = {
    "US": "美国",
    "DE": "德国",
    "FR": "法国",
    "ES": "西班牙",
    "IT": "意大利",
    "JP": "日本",
    "PT": "葡萄牙",
    "NL": "荷兰",
    "AU": "澳大利亚",
    "NZ": "新西兰",
    "CA": "加拿大",
    "GB": "英国",
}


def get_country_dashboard(
    period: str,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    date_str: str | None = None,
    today: date | None = None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    product_code: str | None = None,
) -> dict:
    period = str(period or "").strip().lower()
    if start_date is not None or end_date is not None:
        if start_date is None or end_date is None:
            raise ValueError("start_date and end_date are required")
        start = _coerce_country_dashboard_date(start_date, "start_date")
        end = _coerce_country_dashboard_date(end_date, "end_date")
        if end < start:
            raise ValueError("end_date must be >= start_date")
        period_type = "range"
    else:
        if period not in ("day", "week", "month"):
            raise ValueError("period must be one of day/week/month")
        # 走 facade：_resolve_period_range 在 dashboard 域，PR 1.8 后由 _facade() 转发
        start, end = _facade()._resolve_period_range(
            period,
            year=year,
            month=month,
            week=week,
            date_str=date_str,
            today=today,
        )
        period_type = period

    # Resolve product_id and product_name if product_code is provided
    product_id = None
    product_name = None
    if product_code:
        prod_row = query_one(
            "SELECT id, name FROM media_products WHERE product_code = %s AND deleted_at IS NULL LIMIT 1",
            (product_code,)
        )
        if prod_row:
            product_id = prod_row.get("id")
            product_name = prod_row.get("name")

        if product_id is None:
            order_pid_row = query_one(
                "SELECT product_id, product_name FROM dianxiaomi_order_lines "
                "WHERE product_code = %s AND product_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                (product_code,)
            )
            if order_pid_row:
                product_id = order_pid_row.get("product_id")
                if not product_name:
                    product_name = order_pid_row.get("product_name")

        if not product_name:
            fallback_name = query_one(
                "SELECT product_name FROM dianxiaomi_order_lines "
                "WHERE product_code = %s AND product_name IS NOT NULL ORDER BY id DESC LIMIT 1",
                (product_code,)
            )
            if fallback_name:
                product_name = fallback_name.get("product_name")
            else:
                product_name = product_code

    order_sql = (
        "SELECT buyer_country, buyer_country_name, "
        "COUNT(DISTINCT dxm_package_id) AS order_count, "
        "SUM(COALESCE(quantity, 0)) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS product_net_sales, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping "
        "FROM dianxiaomi_order_lines "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
    )
    order_params = [start, end]
    if product_code:
        order_sql += " AND product_code = %s "
        order_params.append(product_code)

    order_sql += " GROUP BY buyer_country, buyer_country_name"
    rows = query(order_sql, tuple(order_params))

    # Query ad spend and purchase value by country (supporting both closed and open dates)
    from tools.meta_daily_final_sync import completed_meta_business_date
    closed_through = completed_meta_business_date()

    closed_start = start
    closed_end = min(end, closed_through)

    ad_by_country = {}

    if closed_start <= closed_end:
        if product_code:
            hist_sql = (
                "SELECT campaign_name, market_country, "
                "       SUM(COALESCE(spend_usd, 0)) AS spend, "
                "       SUM(COALESCE(purchase_value_usd, 0)) AS purchase_value "
                "FROM meta_ad_daily_campaign_metrics "
                "WHERE COALESCE(meta_business_date, report_date) >= %s AND COALESCE(meta_business_date, report_date) <= %s "
                "  AND (product_id = %s OR matched_product_code = %s OR product_code = %s OR LOWER(campaign_name) LIKE %s) "
                "GROUP BY campaign_name, market_country"
            )
            hist_params = [
                closed_start,
                closed_end,
                product_id if product_id is not None else -1,
                product_code,
                product_code,
                f"%{product_code.lower()}%"
            ]
            hist_rows = query(hist_sql, tuple(hist_params))

            from appcore.order_analytics.meta_ads import resolve_ad_product_match
            from appcore.order_analytics.ad_market_country import extract_market_country

            match_cache = {}
            for r in hist_rows:
                spend = Decimal(str(r.get("spend") or 0))
                purchase_value = Decimal(str(r.get("purchase_value") or 0))
                if spend <= 0 and purchase_value <= 0:
                    continue

                campaign_name = (r.get("campaign_name") or "").strip()
                if not campaign_name:
                    continue

                lookup = campaign_name.lower()
                if lookup not in match_cache:
                    match = resolve_ad_product_match(lookup)
                    match_cache[lookup] = int(match["id"]) if match and match.get("id") is not None else None
                matched_pid = match_cache[lookup]

                if matched_pid != product_id:
                    continue

                cc = (r.get("market_country") or "").strip().upper()
                if not cc:
                    cc = extract_market_country(campaign_name) or ""
                cc = cc.strip().upper()

                if cc:
                    ad_by_country.setdefault(cc, {"spend": Decimal("0"), "purchase_value": Decimal("0")})
                    ad_by_country[cc]["spend"] += spend
                    ad_by_country[cc]["purchase_value"] += purchase_value
        else:
            hist_sql = (
                "SELECT market_country, "
                "       SUM(COALESCE(spend_usd, 0)) AS spend, "
                "       SUM(COALESCE(purchase_value_usd, 0)) AS purchase_value "
                "FROM meta_ad_daily_ad_metrics "
                "WHERE COALESCE(meta_business_date, report_date) >= %s AND COALESCE(meta_business_date, report_date) <= %s "
                "GROUP BY market_country"
            )
            hist_rows = query(hist_sql, (closed_start, closed_end))
            for r in hist_rows:
                mc = (r.get("market_country") or "").strip().upper()
                if mc:
                    ad_by_country[mc] = {
                        "spend": Decimal(str(r.get("spend") or 0)),
                        "purchase_value": Decimal(str(r.get("purchase_value") or 0)),
                    }

    from datetime import timedelta
    open_start = max(start, closed_through + timedelta(days=1))
    open_end = end

    if open_start <= open_end:
        has_realtime = False
        try:
            test_row = query_one(
                "SELECT 1 AS ok FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s LIMIT 1",
                ("meta_ad_realtime_daily_ad_metrics",),
            )
            has_realtime = bool(test_row and test_row.get("ok"))
        except Exception:
            has_realtime = False

        if has_realtime:
            rt_sql = """
                SELECT m.normalized_campaign_code, m.campaign_name, m.country_code,
                       SUM(m.spend_usd) AS spend, SUM(m.purchase_value_usd) AS purchase_value
                FROM meta_ad_realtime_daily_ad_metrics m
                INNER JOIN (
                    SELECT business_date, ad_account_id, MAX(snapshot_at) AS max_snap
                    FROM meta_ad_realtime_daily_ad_metrics
                    WHERE business_date BETWEEN %s AND %s AND data_completeness = 'realtime_partial'
                    GROUP BY business_date, ad_account_id
                ) latest
                ON m.business_date = latest.business_date
                AND m.ad_account_id = latest.ad_account_id
                AND m.snapshot_at = latest.max_snap
                WHERE m.business_date BETWEEN %s AND %s AND m.data_completeness = 'realtime_partial'
                GROUP BY m.normalized_campaign_code, m.campaign_name, m.country_code
            """
            rt_rows = query(rt_sql, (open_start, open_end, open_start, open_end))

            from appcore.order_analytics.meta_ads import resolve_ad_product_match
            from appcore.order_analytics.ad_market_country import extract_market_country

            match_cache = {}
            for r in rt_rows:
                spend = Decimal(str(r.get("spend") or 0))
                pvalue = Decimal(str(r.get("purchase_value") or 0))
                if spend <= 0 and pvalue <= 0:
                    continue

                code = str(r.get("normalized_campaign_code") or r.get("campaign_name") or "").strip()
                if not code:
                    continue

                # Match product
                if product_code:
                    lookup = code.lower()
                    if lookup not in match_cache:
                        match = resolve_ad_product_match(lookup)
                        match_cache[lookup] = int(match["id"]) if match and match.get("id") is not None else None
                    matched_pid = match_cache[lookup]
                    if matched_pid != product_id:
                        continue

                # Match country
                cc = (r.get("country_code") or "").strip().upper()
                if not cc:
                    cc = extract_market_country(code) or ""

                if cc:
                    ad_by_country.setdefault(cc, {"spend": Decimal("0"), "purchase_value": Decimal("0")})
                    ad_by_country[cc]["spend"] += spend
                    ad_by_country[cc]["purchase_value"] += pvalue

    unknown_display_name = "未知"
    countries = []

    order_by_country = {}
    for row in rows:
        c_code = (row.get("buyer_country") or "").strip().upper()
        order_by_country[c_code] = row

    all_country_codes = sorted(list(order_by_country.keys() | ad_by_country.keys()))

    for c_code in all_country_codes:
        row = order_by_country.get(c_code)
        ad_data = ad_by_country.get(c_code, {"spend": Decimal("0"), "purchase_value": Decimal("0")})

        spend = float(ad_data["spend"])
        purchase_value = float(ad_data["purchase_value"])

        if row:
            product_net_sales = _money(row.get("product_net_sales"))
            shipping = _money(row.get("shipping"))
            country_name = (row.get("buyer_country_name") or "").strip()
            if not country_name:
                country_name = _COUNTRY_NAMES.get(c_code, c_code)
            order_count = int(row.get("order_count") or 0)
            units = int(row.get("units") or 0)
        else:
            product_net_sales = 0.0
            shipping = 0.0
            country_name = _COUNTRY_NAMES.get(c_code, c_code)
            order_count = 0
            units = 0

        total_sales = _revenue_with_shipping(product_net_sales, shipping)

        real_roas = round(total_sales / spend, 2) if spend > 0 else None
        meta_roas = round(purchase_value / spend, 2) if spend > 0 else None

        display_name = (
            f"{country_name} / {c_code}"
            if country_name and c_code
            else country_name or c_code or unknown_display_name
        )

        countries.append({
            "buyer_country": c_code,
            "buyer_country_name": country_name,
            "display_name": display_name,
            "order_count": order_count,
            "units": units,
            "product_net_sales": product_net_sales,
            "shipping": shipping,
            "total_sales": total_sales,
            "spend": spend,
            "roas": real_roas,
            "meta_roas": meta_roas,
            "purchase_value": purchase_value,
        })

    countries = _sort_order_dashboard_rows(countries, name_key="display_name")

    total_sales_sum = sum(row["total_sales"] for row in countries)
    total_spend_sum = sum(row["spend"] for row in countries)
    total_purchase_value_sum = sum(row["purchase_value"] for row in countries)

    summary = {
        "country_count": len(countries),
        "total_orders": sum(row["order_count"] for row in countries),
        "total_units": sum(row["units"] for row in countries),
        "total_sales": round(total_sales_sum, 2),
        "shipping": round(sum(row["shipping"] for row in countries), 2),
        "product_net_sales": round(sum(row["product_net_sales"] for row in countries), 2),
        "total_spend": round(total_spend_sum, 2),
        "overall_roas": round(total_sales_sum / total_spend_sum, 2) if total_spend_sum > 0 else None,
        "overall_meta_roas": round(total_purchase_value_sum / total_spend_sum, 2) if total_spend_sum > 0 else None,
    }
    return {
        "period": {
            "type": period_type,
            "start": start,
            "end": end,
            "label": _facade()._format_period_label(start, end, period_type),
            "date_field": "meta_business_date",
            "timezone": META_ATTRIBUTION_TIMEZONE,
        },
        "summary": summary,
        "countries": countries,
        "product_code": product_code,
        "product_name": product_name,
    }


def _coerce_country_dashboard_date(value: str | date, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_iso_date_param(str(value or ""), name)
