from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from appcore.db import query, query_one
from appcore.order_analytics import current_meta_business_date
from appcore.order_analytics._constants import COUNTRY_TO_LANG

def _country_to_lang(country: Any) -> str | None:
    code = str(country or "").strip().upper()
    if not code:
        return None
    lang = COUNTRY_TO_LANG.get(code)
    if not lang:
        return None
    return str(lang).strip().lower()

def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None

def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _candidate_country(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("market_country")
        or candidate.get("country_code")
        or ""
    ).strip().upper()

def _candidate_day_account_key(candidate: dict[str, Any]) -> tuple[int, date, str] | None:
    activity_date = _date_value(candidate.get("activity_date"))
    if activity_date is None:
        return None
    return (
        int(candidate.get("product_id") or 0),
        activity_date,
        str(candidate.get("ad_account_id") or "").strip().removeprefix("act_"),
    )

def _filter_daily_candidates_for_realtime(
    daily_candidates: list[dict[str, Any]],
    realtime_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    realtime_open_keys = {
        key
        for key in (_candidate_day_account_key(candidate) for candidate in realtime_candidates)
        if key is not None
    }
    if not realtime_open_keys:
        return daily_candidates
    return [
        candidate
        for candidate in daily_candidates
        if _candidate_day_account_key(candidate) not in realtime_open_keys
    ]

def _realtime_ad_table_exists() -> bool:
    try:
        row = query_one(
            "SELECT 1 AS ok FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s LIMIT 1",
            ("meta_ad_realtime_daily_ad_metrics",),
        )
    except Exception:
        return False
    return bool(row and row.get("ok"))

def _empty_row() -> dict[str, Any]:
    return {
        "today_spend": 0.0, "today_orders": 0, "today_purchase_value": 0.0, "today_order_profit": 0.0, "today_revenue": 0.0,
        "yesterday_spend": 0.0, "yesterday_orders": 0, "yesterday_purchase_value": 0.0, "yesterday_order_profit": 0.0, "yesterday_revenue": 0.0,
        "last_7d_spend": 0.0, "last_7d_orders": 0, "last_7d_purchase_value": 0.0, "last_7d_order_profit": 0.0, "last_7d_revenue": 0.0,
        "last_30d_spend": 0.0, "last_30d_orders": 0, "last_30d_purchase_value": 0.0, "last_30d_order_profit": 0.0, "last_30d_revenue": 0.0,
        "total_spend": 0.0, "total_orders": 0, "total_purchase_value": 0.0, "total_order_profit": 0.0, "total_revenue": 0.0,
    }

def get_product_ad_orders_report(product_id: int, today: date | None = None) -> dict[str, Any]:
    """Retrieve spend, orders, purchase value, and calculated ROAS for a product,
    grouped by buyer country / market country (mapped to language code).
    """
    product = query_one(
        "SELECT id, product_code, name, purchase_price, packet_cost_estimated, packet_cost_actual, standalone_price, standalone_shipping_fee "
        "FROM media_products WHERE id = %s AND deleted_at IS NULL",
        (product_id,),
    )
    if not product:
        return {"total": {}, "by_lang": {}}

    product_code = str(product.get("product_code") or "").strip()
    business_today = today or current_meta_business_date()

    from appcore import product_roas
    rmb_per_usd = product_roas.get_configured_rmb_per_usd()
    roas_calc = product_roas.calculate_break_even_roas(
        purchase_price=product.get("purchase_price"),
        estimated_packet_cost=product.get("packet_cost_estimated"),
        actual_packet_cost=product.get("packet_cost_actual"),
        standalone_price=product.get("standalone_price"),
        standalone_shipping_fee=product.get("standalone_shipping_fee"),
        rmb_per_usd=rmb_per_usd,
    )
    breakeven_roas = roas_calc.get("effective_roas") if roas_calc else None

    # Define date limits for windows
    yesterday = business_today - timedelta(days=1)
    last_7d_start = business_today - timedelta(days=6)
    last_30d_start = business_today - timedelta(days=29)

    # 1. Fetch and process Orders (from dianxiaomi_order_lines + order_profit_lines)
    order_rows = query(
        "SELECT "
        "  opl.product_id, "
        "  UPPER(TRIM(COALESCE(NULLIF(TRIM(opl.buyer_country), ''), NULLIF(TRIM(dol.buyer_country), ''), ''))) AS buyer_country, "
        "  dol.meta_business_date AS business_date, "
        "  COUNT(DISTINCT NULLIF(TRIM(dol.dxm_package_id), '')) AS order_count, "
        "  SUM(COALESCE(opl.revenue_usd, 0)) AS revenue_usd, "
        "  SUM(COALESCE(opl.shopify_fee_usd, 0)) AS shopify_fee_usd, "
        "  SUM(COALESCE(opl.purchase_usd, 0)) AS purchase_usd, "
        "  SUM(COALESCE(opl.shipping_cost_usd, 0)) AS shipping_cost_usd, "
        "  SUM(COALESCE(opl.return_reserve_usd, 0)) AS return_reserve_usd "
        "FROM order_profit_lines opl "
        "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
        "WHERE opl.product_id = %s "
        "GROUP BY buyer_country, dol.meta_business_date",
        (product_id,),
    )

    report: dict[str, dict[str, Any]] = {}

    def get_or_create_lang_row(lang: str) -> dict[str, Any]:
        return report.setdefault(lang, _empty_row())

    total_row = _empty_row()

    # Process orders
    for row in order_rows:
        count = int(row.get("order_count") or 0)
        if count <= 0:
            continue
        bdate = _date_value(row.get("business_date"))
        if bdate is None:
            continue
        country = str(row.get("buyer_country") or "").strip().upper()
        lang = _country_to_lang(country)

        # Calculate profit metrics for this buyer country and business date combination
        revenue = _float_value(row.get("revenue_usd"))
        shopify_fee = _float_value(row.get("shopify_fee_usd"))
        purchase = _float_value(row.get("purchase_usd"))
        shipping = _float_value(row.get("shipping_cost_usd"))
        return_reserve = _float_value(row.get("return_reserve_usd"))
        order_profit = revenue - shopify_fee - purchase - shipping - return_reserve

        # Update total row
        if bdate == business_today:
            total_row["today_orders"] += count
            total_row["today_order_profit"] += order_profit
            total_row["today_revenue"] += revenue
        if bdate == yesterday:
            total_row["yesterday_orders"] += count
            total_row["yesterday_order_profit"] += order_profit
            total_row["yesterday_revenue"] += revenue
        if last_7d_start <= bdate <= business_today:
            total_row["last_7d_orders"] += count
            total_row["last_7d_order_profit"] += order_profit
            total_row["last_7d_revenue"] += revenue
        if last_30d_start <= bdate <= business_today:
            total_row["last_30d_orders"] += count
            total_row["last_30d_order_profit"] += order_profit
            total_row["last_30d_revenue"] += revenue
        total_row["total_orders"] += count
        total_row["total_order_profit"] += order_profit
        total_row["total_revenue"] += revenue

        # Update language row
        if lang:
            l_row = get_or_create_lang_row(lang)
            if bdate == business_today:
                l_row["today_orders"] += count
                l_row["today_order_profit"] += order_profit
                l_row["today_revenue"] += revenue
            if bdate == yesterday:
                l_row["yesterday_orders"] += count
                l_row["yesterday_order_profit"] += order_profit
                l_row["yesterday_revenue"] += revenue
            if last_7d_start <= bdate <= business_today:
                l_row["last_7d_orders"] += count
                l_row["last_7d_order_profit"] += order_profit
                l_row["last_7d_revenue"] += revenue
            if last_30d_start <= bdate <= business_today:
                l_row["last_30d_orders"] += count
                l_row["last_30d_order_profit"] += order_profit
                l_row["last_30d_revenue"] += revenue
            l_row["total_orders"] += count
            l_row["total_order_profit"] += order_profit
            l_row["total_revenue"] += revenue

    # 2. Fetch Ad candidates
    # Daily ad metrics (historical)
    daily_candidates = query(
        "SELECT m.product_id, m.ad_account_id, "
        "       COALESCE(m.meta_business_date, m.report_date) AS activity_date, "
        "       m.spend_usd, m.purchase_value_usd, m.market_country, m.id "
        "FROM meta_ad_daily_ad_metrics m "
        "WHERE m.product_id = %s AND COALESCE(m.spend_usd, 0) > 0",
        (product_id,),
    )
    for row in daily_candidates:
        row["metric_source"] = "daily"

    # Realtime ad metrics (today)
    realtime_candidates = []
    if product_code and _realtime_ad_table_exists():
        realtime_candidates = query(
            "SELECT p_rt.id AS product_id, m.ad_account_id, m.business_date AS activity_date, "
            "       m.spend_usd, m.purchase_value_usd, m.country_code AS market_country, m.id "
            "FROM ("
            "  SELECT latest_day.business_date, latest_day.ad_account_id, MAX(rt.snapshot_at) AS max_snapshot_at "
            "  FROM meta_ad_realtime_daily_ad_metrics rt "
            "  INNER JOIN ("
            "    SELECT ad_account_id, MAX(business_date) AS business_date "
            "    FROM meta_ad_realtime_daily_ad_metrics "
            "    WHERE data_completeness = 'realtime_partial' "
            "    GROUP BY ad_account_id"
            "  ) latest_day "
            "    ON rt.business_date = latest_day.business_date "
            "   AND (rt.ad_account_id <=> latest_day.ad_account_id) "
            "  WHERE rt.data_completeness = 'realtime_partial' "
            "  GROUP BY latest_day.business_date, latest_day.ad_account_id"
            ") latest "
            "STRAIGHT_JOIN meta_ad_realtime_daily_ad_metrics m "
            "  ON m.business_date = latest.business_date "
            " AND (m.ad_account_id <=> latest.ad_account_id) "
            " AND m.snapshot_at = latest.max_snapshot_at "
            "JOIN media_products p_rt "
            "  ON p_rt.id = %s "
            " AND p_rt.deleted_at IS NULL "
            " AND ( "
            "   LOWER(COALESCE(m.normalized_campaign_code, '')) LIKE CONCAT(LOWER(p_rt.product_code), '%%') "
            "   OR LOWER(COALESCE(m.campaign_name, '')) LIKE CONCAT(LOWER(p_rt.product_code), '%%') "
            "   OR LOWER(COALESCE(m.normalized_ad_code, '')) LIKE CONCAT(LOWER(p_rt.product_code), '%%') "
            "   OR LOWER(COALESCE(m.ad_name, '')) LIKE CONCAT(LOWER(p_rt.product_code), '%%') "
            " ) "
            "WHERE m.data_completeness = 'realtime_partial' "
            "  AND COALESCE(m.spend_usd, 0) > 0",
            (product_id,),
        )
        for row in realtime_candidates:
            row["metric_source"] = "realtime"

    # Filter overlaps
    ad_candidates = _filter_daily_candidates_for_realtime(daily_candidates, realtime_candidates) + realtime_candidates

    # Process ad metrics
    for candidate in ad_candidates:
        spend = _float_value(candidate.get("spend_usd"))
        pvalue = _float_value(candidate.get("purchase_value_usd"))
        if spend <= 0:
            continue
        bdate = _date_value(candidate.get("activity_date"))
        if bdate is None:
            continue
        country = _candidate_country(candidate)
        lang = _country_to_lang(country)

        # Update total row spend and pvalue
        if bdate == business_today:
            total_row["today_spend"] += spend
            total_row["today_purchase_value"] += pvalue
        if bdate == yesterday:
            total_row["yesterday_spend"] += spend
            total_row["yesterday_purchase_value"] += pvalue
        if last_7d_start <= bdate <= business_today:
            total_row["last_7d_spend"] += spend
            total_row["last_7d_purchase_value"] += pvalue
        if last_30d_start <= bdate <= business_today:
            total_row["last_30d_spend"] += spend
            total_row["last_30d_purchase_value"] += pvalue
        total_row["total_spend"] += spend
        total_row["total_purchase_value"] += pvalue

        # Update language row spend and pvalue
        if lang:
            l_row = get_or_create_lang_row(lang)
            if bdate == business_today:
                l_row["today_spend"] += spend
                l_row["today_purchase_value"] += pvalue
            if bdate == yesterday:
                l_row["yesterday_spend"] += spend
                l_row["yesterday_purchase_value"] += pvalue
            if last_7d_start <= bdate <= business_today:
                l_row["last_7d_spend"] += spend
                l_row["last_7d_purchase_value"] += pvalue
            if last_30d_start <= bdate <= business_today:
                l_row["last_30d_spend"] += spend
                l_row["last_30d_purchase_value"] += pvalue
            l_row["total_spend"] += spend
            l_row["total_purchase_value"] += pvalue

    # Helper function to compute ROAS and clean dict fields
    def finalize_row(row: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for key in ("today", "yesterday", "last_7d", "last_30d", "total"):
            spend = round(row[f"{key}_spend"], 2)
            orders = int(row[f"{key}_orders"])
            pvalue = row[f"{key}_purchase_value"]
            roas = round(pvalue / spend, 2) if spend > 0 else None
            profit = round(row[f"{key}_order_profit"] - spend, 2)
            revenue = row[f"{key}_revenue"]
            order_roas = round(revenue / spend, 2) if spend > 0 else None
            out[f"{key}_spend"] = spend
            out[f"{key}_orders"] = orders
            out[f"{key}_roas"] = roas
            out[f"{key}_profit"] = profit
            out[f"{key}_order_roas"] = order_roas
            out[f"{key}_revenue"] = round(revenue, 2)
        return out

    # Finalize total row and language rows
    final_total = finalize_row(total_row)
    final_by_lang = {lang: finalize_row(row) for lang, row in report.items()}

    return {
        "product_id": product_id,
        "product_name": product.get("name") or "",
        "product_code": product_code,
        "breakeven_roas": float(breakeven_roas) if breakeven_roas is not None else None,
        "total": final_total,
        "by_lang": final_by_lang,
        "computed_at": datetime.now().isoformat(),
    }


def get_product_ad_orders_report_for_range(
    product_id: int,
    date_from: date,
    date_to: date,
    country_code: str | None = None,
    query_fn=query,
) -> dict[str, Any]:
    """Retrieve spend, orders, purchase value, and calculated ROAS for a product
    specifically for a date range and optionally filtered by a buyer/market country.
    """
    # 1. Fetch product
    rows = query_fn(
        "SELECT id, product_code, name FROM media_products WHERE id = %s AND deleted_at IS NULL",
        [product_id],
    )
    if not rows:
        return {"spend": 0.0, "purchase_value": 0.0, "roas": None, "orders": 0}
    product = rows[0]
    product_code = str(product.get("product_code") or "").strip()

    # 2. Fetch Orders (order_count)
    sql_orders = """
        SELECT COUNT(DISTINCT NULLIF(TRIM(dol.dxm_package_id), '')) AS order_count 
        FROM order_profit_lines opl 
        JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id 
        WHERE opl.product_id = %s 
          AND DATE(dol.meta_business_date) BETWEEN %s AND %s
    """
    params_orders = [product_id, date_from.isoformat(), date_to.isoformat()]
    if country_code:
        sql_orders += " AND UPPER(TRIM(COALESCE(NULLIF(TRIM(opl.buyer_country), ''), NULLIF(TRIM(dol.buyer_country), ''), ''))) = %s"
        params_orders.append(country_code.upper())
    
    orders = 0
    try:
        order_rows = query_fn(sql_orders, params_orders)
        if order_rows:
            orders = int(order_rows[0].get("order_count") or 0)
    except Exception:
        pass

    # 3. Fetch Ad metrics (Historical)
    sql_daily = """
        SELECT SUM(COALESCE(m.spend_usd, 0)) AS spend, SUM(COALESCE(m.purchase_value_usd, 0)) AS pvalue
        FROM meta_ad_daily_ad_metrics m
        WHERE m.product_id = %s
          AND DATE(COALESCE(m.meta_business_date, m.report_date)) BETWEEN %s AND %s
    """
    params_daily = [product_id, date_from.isoformat(), date_to.isoformat()]
    if country_code:
        sql_daily += " AND UPPER(COALESCE(m.market_country, '')) = %s"
        params_daily.append(country_code.upper())

    spend = 0.0
    purchase_value = 0.0
    try:
        daily_rows = query_fn(sql_daily, params_daily)
        if daily_rows and daily_rows[0]:
            spend += _float_value(daily_rows[0].get("spend"))
            purchase_value += _float_value(daily_rows[0].get("pvalue"))
    except Exception:
        pass

    # 4. Fetch Ad metrics (Realtime)
    has_realtime = False
    try:
        test_row = query_fn(
            "SELECT 1 AS ok FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s LIMIT 1",
            ["meta_ad_realtime_daily_ad_metrics"],
        )
        has_realtime = bool(test_row and test_row[0].get("ok"))
    except Exception:
        has_realtime = False

    if has_realtime and product_code:
        sql_rt = """
            SELECT SUM(COALESCE(m.spend_usd, 0)) AS spend, SUM(COALESCE(m.purchase_value_usd, 0)) AS pvalue
            FROM (
              SELECT latest_day.business_date, latest_day.ad_account_id, MAX(rt.snapshot_at) AS max_snapshot_at
              FROM meta_ad_realtime_daily_ad_metrics rt
              INNER JOIN (
                SELECT ad_account_id, MAX(business_date) AS business_date
                FROM meta_ad_realtime_daily_ad_metrics
                WHERE data_completeness = 'realtime_partial'
                GROUP BY ad_account_id
              ) latest_day
                ON rt.business_date = latest_day.business_date
               AND (rt.ad_account_id <=> latest_day.ad_account_id)
              WHERE rt.data_completeness = 'realtime_partial'
              GROUP BY latest_day.business_date, latest_day.ad_account_id
            ) latest
            STRAIGHT_JOIN meta_ad_realtime_daily_ad_metrics m
              ON m.business_date = latest.business_date
             AND (m.ad_account_id <=> latest.ad_account_id)
             AND m.snapshot_at = latest.max_snapshot_at
            WHERE m.data_completeness = 'realtime_partial'
              AND m.business_date BETWEEN %s AND %s
              AND (
                LOWER(COALESCE(m.normalized_campaign_code, '')) LIKE CONCAT(LOWER(%s), '%%')
                OR LOWER(COALESCE(m.campaign_name, '')) LIKE CONCAT(LOWER(%s), '%%')
                OR LOWER(COALESCE(m.normalized_ad_code, '')) LIKE CONCAT(LOWER(%s), '%%')
                OR LOWER(COALESCE(m.ad_name, '')) LIKE CONCAT(LOWER(%s), '%%')
              )
        """
        params_rt = [date_from.isoformat(), date_to.isoformat(), product_code, product_code, product_code, product_code]
        if country_code:
            sql_rt += " AND UPPER(COALESCE(m.country_code, '')) = %s"
            params_rt.append(country_code.upper())

        try:
            rt_rows = query_fn(sql_rt, params_rt)
            if rt_rows and rt_rows[0]:
                spend += _float_value(rt_rows[0].get("spend"))
                purchase_value += _float_value(rt_rows[0].get("pvalue"))
        except Exception:
            pass

    spend = round(spend, 2)
    purchase_value = round(purchase_value, 2)
    roas = round(purchase_value / spend, 2) if spend > 0 else None

    return {
        "spend": spend,
        "purchase_value": purchase_value,
        "roas": roas,
        "orders": orders,
    }

