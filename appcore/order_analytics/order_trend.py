"""Backend logic to aggregate daily, weekly, and monthly order and ad trends for a single product."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from appcore.db import query, query_one

log = logging.getLogger(__name__)

def get_product_order_trend_data(product_code: str) -> dict[str, Any] | None:
    """Fetch daily (last 30 days), weekly (last 12 weeks), and monthly (last 12 months) sales trends,
    plus ad ROAS for the daily trend, of the given product_code.
    """
    # 1. Resolve product metadata
    product = query_one(
        "SELECT id, product_code, name FROM media_products WHERE product_code = %s AND deleted_at IS NULL LIMIT 1",
        (product_code,)
    )
    product_id = product.get("id") if product else None
    
    # Resolve product name: prefer media_products name, fallback to dianxiaomi_order_lines latest name
    product_name = ""
    if product:
        product_name = product.get("name") or ""
    
    if not product_name:
        fallback = query_one(
            "SELECT product_name FROM dianxiaomi_order_lines WHERE product_code = %s AND product_name IS NOT NULL ORDER BY id DESC LIMIT 1",
            (product_code,)
        )
        if fallback:
            product_name = fallback.get("product_name") or ""
        else:
            product_name = product_code

    # If we still can't find product ID but we have orders, we can try to find product ID from dianxiaomi_order_lines
    if product_id is None:
        order_pid_row = query_one(
            "SELECT product_id FROM dianxiaomi_order_lines WHERE product_code = %s AND product_id IS NOT NULL LIMIT 1",
            (product_code,)
        )
        if order_pid_row:
            product_id = order_pid_row.get("product_id")

    # Current date in CST (Beijing time)
    # Using the timezone-aware date logic consistent with other modules
    # (Since we are in appcore/order_analytics, we can calculate current date relative to now)
    # Beijing is UTC+8
    today = (datetime.utcnow() + timedelta(hours=8)).date()

    # 2. Query daily sales for the last 12 months (to aggregate weeks/months in Python)
    # Start of 11 months ago
    year = today.year
    month = today.month
    for _ in range(11):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    start_of_12_months_ago = date(year, month, 1)

    sales_rows = query(
        "SELECT meta_business_date AS d, "
        "       SUM(COALESCE(quantity, 0)) AS units, "
        "       COUNT(DISTINCT dxm_package_id) AS order_count, "
        "       SUM(COALESCE(line_amount, 0)) + SUM(COALESCE(ship_amount, 0)) AS sales "
        "FROM dianxiaomi_order_lines "
        "WHERE product_code = %s AND meta_business_date BETWEEN %s AND %s "
        "GROUP BY meta_business_date",
        (product_code, start_of_12_months_ago, today)
    )

    sales_by_date: dict[date, dict[str, Any]] = {}
    for r in sales_rows:
        d_val = r.get("d")
        if isinstance(d_val, str):
            d_val = date.fromisoformat(d_val[:10])
        elif isinstance(d_val, datetime):
            d_val = d_val.date()
        if d_val:
            sales_by_date[d_val] = {
                "units": int(r.get("units") or 0),
                "orders": int(r.get("order_count") or 0),
                "sales": float(r.get("sales") or 0.0)
            }

    # Fetch realtime data for today to fill or overwrite today's slot in sales_by_date
    rt_summary = {}
    if product_id is not None:
        try:
            from appcore.order_analytics.realtime import get_realtime_roas_overview
            rt_res = get_realtime_roas_overview(
                date_text=today.isoformat(),
                product_id=product_id,
            )
            rt_summary = rt_res.get("summary") or {}
        except Exception as rt_exc:
            log.warning("Fetch realtime data for today failed: %s", rt_exc)

    if rt_summary:
        sales_by_date[today] = {
            "units": int(rt_summary.get("units") or 0),
            "orders": int(rt_summary.get("order_count") or 0),
            "sales": float(rt_summary.get("revenue_with_shipping") or 0.0)
        }

    # 2.5 Query daily country sales breakdown for 9 countries
    country_sales_rows = query(
        "SELECT meta_business_date AS d, "
        "       buyer_country AS country, "
        "       SUM(COALESCE(quantity, 0)) AS units "
        "FROM dianxiaomi_order_lines "
        "WHERE product_code = %s AND meta_business_date BETWEEN %s AND %s "
        "GROUP BY meta_business_date, buyer_country",
        (product_code, start_of_12_months_ago, today)
    )

    country_sales_by_date: dict[date, dict[str, int]] = {}
    for r in country_sales_rows:
        d_val = r.get("d")
        if isinstance(d_val, str):
            d_val = date.fromisoformat(d_val[:10])
        elif isinstance(d_val, datetime):
            d_val = d_val.date()
        if d_val:
            country = str(r.get("country") or "").upper().strip()
            units = int(r.get("units") or 0)
            if d_val not in country_sales_by_date:
                country_sales_by_date[d_val] = {}
            if country:
                country_sales_by_date[d_val][country] = country_sales_by_date[d_val].get(country, 0) + units

    # 3. Query ad metrics for the last 30 days if product_id exists
    ad_by_date: dict[date, dict[str, Decimal]] = {}
    if product_id is not None:
        start_of_30_days_ago = today - timedelta(days=29)
        ad_rows = query(
            "SELECT COALESCE(meta_business_date, report_date) AS d, "
            "       SUM(COALESCE(spend_usd, 0)) AS spend_usd, "
            "       SUM(COALESCE(purchase_value_usd, 0)) AS purchase_value_usd "
            "FROM meta_ad_daily_campaign_metrics "
            "WHERE product_id = %s AND COALESCE(meta_business_date, report_date) BETWEEN %s AND %s "
            "GROUP BY d",
            (product_id, start_of_30_days_ago, today)
        )
        for r in ad_rows:
            d_val = r.get("d")
            if isinstance(d_val, str):
                d_val = date.fromisoformat(d_val[:10])
            elif isinstance(d_val, datetime):
                d_val = d_val.date()
            if d_val:
                ad_by_date[d_val] = {
                    "spend": Decimal(str(r.get("spend_usd") or 0)),
                    "purchase_value": Decimal(str(r.get("purchase_value_usd") or 0))
                }

    if rt_summary:
        ad_by_date[today] = {
            "spend": Decimal(str(rt_summary.get("ad_spend") or 0)),
            "purchase_value": Decimal(str(rt_summary.get("meta_purchase_value") or 0))
        }

    # 4. Generate daily trend (Last 30 Days)
    daily_trend = []
    for i in range(30):
        d_val = today - timedelta(days=29 - i)
        s_data = sales_by_date.get(d_val, {"units": 0, "orders": 0, "sales": 0.0})
        ad_data = ad_by_date.get(d_val, {"spend": Decimal("0"), "purchase_value": Decimal("0")})
        
        spend = float(ad_data["spend"])
        purchase_value = float(ad_data["purchase_value"])
        sales = float(s_data["sales"])
        
        meta_roas = None
        real_roas = None
        if spend > 0:
            meta_roas = round(purchase_value / spend, 2)
            real_roas = round(sales / spend, 2)

        day_countries = country_sales_by_date.get(d_val, {})
        country_data = {
            "US": day_countries.get("US", 0),
            "DE": day_countries.get("DE", 0),
            "FR": day_countries.get("FR", 0),
            "ES": day_countries.get("ES", 0),
            "IT": day_countries.get("IT", 0),
            "JP": day_countries.get("JP", 0),
            "PT": day_countries.get("PT", 0),
            "SE": day_countries.get("SE", 0),
            "NL": day_countries.get("NL", 0),
        }
            
        daily_trend.append({
            "date": d_val.isoformat(),
            "units": s_data["units"],
            "orders": s_data["orders"],
            "sales": sales,
            "spend": spend,
            "purchase_value": purchase_value,
            "meta_roas": meta_roas,
            "real_roas": real_roas,
            "countries": country_data
        })

    # 5. Generate weekly trend (Last 12 Weeks)
    # Monday to Sunday calendar weeks
    weekly_trend = []
    current_monday = today - timedelta(days=today.weekday())
    for i in range(12):
        w_start = current_monday - timedelta(weeks=11 - i)
        w_end = w_start + timedelta(days=6)
        
        w_units = 0
        w_orders = 0
        w_sales = 0.0
        w_countries = {c: 0 for c in ("US", "DE", "FR", "ES", "IT", "JP", "PT", "SE", "NL")}
        
        # Aggregate daily sales in this week
        curr_d = w_start
        while curr_d <= w_end:
            s_data = sales_by_date.get(curr_d)
            if s_data:
                w_units += s_data["units"]
                w_orders += s_data["orders"]
                w_sales += s_data["sales"]
            
            c_data = country_sales_by_date.get(curr_d, {})
            for c in w_countries:
                w_countries[c] += c_data.get(c, 0)
                
            curr_d += timedelta(days=1)
            
        label = f"W{w_start.isocalendar()[1]} ({w_start.strftime('%m-%d')} ~ {w_end.strftime('%m-%d')})"
        weekly_trend.append({
            "label": label,
            "units": w_units,
            "orders": w_orders,
            "sales": w_sales,
            "start_date": w_start.isoformat(),
            "end_date": w_end.isoformat(),
            "countries": w_countries
        })

    # 6. Generate monthly trend (Last 12 Months)
    monthly_trend = []
    # Generate 12 months of year-month labels
    year = today.year
    month = today.month
    month_starts = []
    for i in range(12):
        month_starts.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    # Reverse so it goes chronologically
    month_starts.reverse()

    for y, m in month_starts:
        m_units = 0
        m_orders = 0
        m_sales = 0.0
        m_countries = {c: 0 for c in ("US", "DE", "FR", "ES", "IT", "JP", "PT", "SE", "NL")}
        # Calculate start and end date of that month
        # For simplicity, aggregate daily sales where date has matching y and m
        for d_val, s_data in sales_by_date.items():
            if d_val.year == y and d_val.month == m:
                m_units += s_data["units"]
                m_orders += s_data["orders"]
                m_sales += s_data["sales"]
                
                c_data = country_sales_by_date.get(d_val, {})
                for c in m_countries:
                    m_countries[c] += c_data.get(c, 0)
                
        monthly_trend.append({
            "label": f"{y}-{m:02d}",
            "units": m_units,
            "orders": m_orders,
            "sales": m_sales,
            "countries": m_countries
        })

    return {
        "product_code": product_code,
        "product_name": product_name,
        "product_id": product_id,
        "daily": daily_trend,
        "weekly": weekly_trend,
        "monthly": monthly_trend
    }
