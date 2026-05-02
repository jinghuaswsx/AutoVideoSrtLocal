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


def get_country_dashboard(
    period: str,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    date_str: str | None = None,
    today: date | None = None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
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

    rows = query(
        "SELECT buyer_country, buyer_country_name, "
        "COUNT(DISTINCT dxm_package_id) AS order_count, "
        "SUM(COALESCE(quantity, 0)) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS product_net_sales, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping "
        "FROM dianxiaomi_order_lines "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        "GROUP BY buyer_country, buyer_country_name",
        (start, end),
    )

    unknown_display_name = "未知"
    countries = []
    for row in rows:
        product_net_sales = _money(row.get("product_net_sales"))
        shipping = _money(row.get("shipping"))
        country_code = (row.get("buyer_country") or "").strip()
        country_name = (row.get("buyer_country_name") or "").strip()
        display_name = (
            f"{country_name} / {country_code}"
            if country_name and country_code
            else country_name or country_code or unknown_display_name
        )
        countries.append({
            "buyer_country": country_code,
            "buyer_country_name": country_name,
            "display_name": display_name,
            "order_count": int(row.get("order_count") or 0),
            "units": int(row.get("units") or 0),
            "product_net_sales": product_net_sales,
            "shipping": shipping,
            "total_sales": _revenue_with_shipping(product_net_sales, shipping),
        })

    countries = _sort_order_dashboard_rows(countries, name_key="display_name")
    summary = {
        "country_count": len(countries),
        "total_orders": sum(row["order_count"] for row in countries),
        "total_units": sum(row["units"] for row in countries),
        "total_sales": round(sum(row["total_sales"] for row in countries), 2),
        "shipping": round(sum(row["shipping"] for row in countries), 2),
        "product_net_sales": round(sum(row["product_net_sales"] for row in countries), 2),
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
    }


def _coerce_country_dashboard_date(value: str | date, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_iso_date_param(str(value or ""), name)
