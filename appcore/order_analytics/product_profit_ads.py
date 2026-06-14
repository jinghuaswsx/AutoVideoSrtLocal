"""产品广告明细（campaign 级）聚合，给 Tab ④ 用。

数据流：
  1. 从 ``meta_ad_daily_campaign_metrics`` 拉日期范围内与本产品相关的所有 campaign 行
     （已回填 ``product_id = %s`` 的 + 仍未回填 ``product_id IS NULL`` 的，方便兜底解析）
  2. 对未回填的 campaign 通过 ``meta_ads.resolve_ad_product_match()`` 实时解析，
     看能否归到本产品（命中过 override 兜底）。仍解析不出的进 "unmatched" 区。
  3. 按 ``normalized_campaign_code`` 聚合花费 / 结果数 / Meta 自报购买价值。
  4. 拉同期同产品的 ``order_profit_lines`` 求归属订单 / 收入 / 成本（USD）。
  5. ROAS = 该 campaign 归属收入 / 花费；
     利润贡献 = 归属收入 - 花费 - 同期同产品的成本（按 spend 比例分摊）。

设计取舍：
  - 日表 ``meta_ad_daily_campaign_metrics`` **没有** ``impressions`` / ``link_clicks`` 字段
    （只有 spend / result_count / purchase_value），所以 impressions/clicks 走 period
    主表 ``meta_ad_campaign_metrics`` 按 ``normalized_campaign_code`` SUM 聚合（spec §9
    要求 campaign 行展示 [展示][点击][CTR][CPC]）。period 表按 ``report_start_date /
    report_end_date`` 分桶而不是按日，所以 SUM 是估算（多个 period 完全覆盖范围时偏多
    一点），但简单可解释；列表只用作量级展示而非精确归因。
  - 本 module 输出的 campaign 行用 ``normalized_campaign_code`` 作为主键。
  - **归属订单 / 收入 / 成本按日按 spend 占比分摊**（与 ``allocate_ad_cost_to_line()``
    口径一致）：``campaign.attributed_revenue = Σ_d daily_spend[campaign,d] /
    daily_total_spend[d] × attributed[d].revenue``。这样跨日不同 campaign 的归属互不
    串扰，避免整范围 totals 分摊把别的日子的订单算到本 campaign 头上。
  - country 过滤：订单侧按 ``buyer_country`` 过滤；campaign 侧按
    ``meta_ad_daily_campaign_metrics.market_country`` 过滤。该字段来自广告命名解析，
    不是 Meta API country breakdown。
"""
from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .ad_market_country import is_single_market_country, normalize_market_country
from .meta_ads import resolve_ad_product_match
from . import product_ad_launch

log = logging.getLogger(__name__)

_ALLOCATION_REASON_LABELS = {
    "unmatched_product": "未匹配产品",
    "matched_no_units": "已匹配产品但无可分摊订单",
    "mixed": "多原因未分摊",
}


def _allocation_reason_label(reason: str | None) -> str:
    return _ALLOCATION_REASON_LABELS.get((reason or "").strip(), "未分摊")


_LAUNCH_SEGMENT_NEW = "new_product"
_LAUNCH_SEGMENT_NON_NEW = "non_new_product"


def _empty_launch_segment_summary(window_days: int) -> dict[str, Any]:
    return {
        "window_days": window_days,
        "total_spend_usd": 0.0,
        _LAUNCH_SEGMENT_NEW: {
            "label": "新品",
            "spend_usd": 0.0,
            "share_pct": 0.0,
            "campaign_count": 0,
        },
        _LAUNCH_SEGMENT_NON_NEW: {
            "label": "非新品",
            "spend_usd": 0.0,
            "share_pct": 0.0,
            "campaign_count": 0,
        },
    }


def _load_product_launch_dates(product_ids: set[int]) -> dict[int, date]:
    if not product_ids:
        return {}
    product_ad_launch.seed_missing_fallback_launch_dates()
    product_list = sorted(product_ids)
    rows = query(
        f"SELECT product_id, ad_launch_date FROM product_ad_launch_dates "
        f"WHERE product_id IN ({_sql_in(product_list)})",
        tuple(product_list),
    ) or []
    out: dict[int, date] = {}
    for row in rows:
        try:
            product_id = int(row.get("product_id") or 0)
        except (TypeError, ValueError):
            continue
        launch_date = _date_value(row.get("ad_launch_date"))
        if product_id > 0 and launch_date:
            out[product_id] = launch_date
    return out


def _attach_launch_segments_to_unmatched(
    unmatched_rows: list[dict[str, Any]],
    *,
    window_days: int | None = None,
) -> dict[str, Any]:
    normalized_window_days = product_ad_launch.normalize_product_launch_window_days(window_days)
    summary = _empty_launch_segment_summary(normalized_window_days)
    product_ids = {
        int(row["matched_product_id"])
        for row in unmatched_rows
        if row.get("matched_product_id") is not None
    }
    launch_dates = _load_product_launch_dates(product_ids)
    today = product_ad_launch.beijing_today()

    for row in unmatched_rows:
        product_id = row.get("matched_product_id")
        launch_date = None
        if product_id is not None:
            try:
                launch_date = launch_dates.get(int(product_id))
            except (TypeError, ValueError):
                launch_date = None

        is_new = False
        if launch_date is not None:
            is_new = (
                product_ad_launch.classify_launch_date(
                    launch_date,
                    today=today,
                    window_days=normalized_window_days,
                )
                == "new"
            )

        segment = _LAUNCH_SEGMENT_NEW if is_new else _LAUNCH_SEGMENT_NON_NEW
        row["launch_segment"] = segment
        row["launch_segment_label"] = "新品" if is_new else "非新品"
        row["is_new_product"] = bool(is_new)
        row["ad_launch_date"] = launch_date.isoformat() if launch_date else None
        row["product_launch_window_days"] = normalized_window_days

        spend = round(float(row.get("spend_usd") or 0), 2)
        summary[segment]["spend_usd"] = round(summary[segment]["spend_usd"] + spend, 2)
        summary[segment]["campaign_count"] += 1

    total_spend = round(
        summary[_LAUNCH_SEGMENT_NEW]["spend_usd"]
        + summary[_LAUNCH_SEGMENT_NON_NEW]["spend_usd"],
        2,
    )
    summary["total_spend_usd"] = total_spend
    if total_spend > 0:
        for key in (_LAUNCH_SEGMENT_NEW, _LAUNCH_SEGMENT_NON_NEW):
            summary[key]["share_pct"] = round(summary[key]["spend_usd"] / total_spend * 100, 2)
    return summary


# ---------------------------------------------------------------------------
# DB facade（同 product_profit_list.py / campaign_overrides.py 模式）
# 必须用 wrapper 函数转发到 sys.modules[__package__]，让 monkeypatch.setattr(oa, "query", fake)
# 透传到本 module。
# ---------------------------------------------------------------------------
def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def _load_campaign_metrics(
    product_id: int, date_from: date, date_to: date, country: str | None = None
) -> list[dict[str, Any]]:
    """拉 ``meta_ad_daily_campaign_metrics`` 在日期范围内与本产品相关的所有行。

    谓词：业务日期 ``COALESCE(meta_business_date, report_date) BETWEEN ...``
    AND (``product_id = %s`` OR ``product_id IS NULL``)。
    把未回填的 NULL 行也拉进来，让上层 ``_load_match_map`` 通过
    ``resolve_ad_product_match`` / override 表做兜底实时解析（覆盖同步流程还没回写
    product_id 的 race condition）。
    """
    open_dates = _open_business_dates_in_range(date_from, date_to)
    daily_to = min(open_dates) - timedelta(days=1) if open_dates else date_to
    rows: list[dict[str, Any]] = []
    if daily_to < date_from:
        daily_rows: list[dict[str, Any]] = []
    else:
        sql = (
        "SELECT COALESCE(m.meta_business_date, m.report_date) AS report_date, "
        "       m.ad_account_id, m.ad_account_name, "
        "       m.normalized_campaign_code, m.campaign_name, "
        "       m.product_id, m.matched_product_code, m.market_country, "
        "       o.id AS manual_override_id, "
        "       m.spend_usd, m.result_count, m.purchase_value_usd, m.roas_purchase "
        "FROM meta_ad_daily_campaign_metrics m "
        "LEFT JOIN campaign_product_overrides o "
        "  ON o.normalized_campaign_code = m.normalized_campaign_code "
        " AND o.product_id = %s "
        "WHERE COALESCE(m.meta_business_date, m.report_date) BETWEEN %s AND %s "
        "  AND (m.product_id = %s OR m.product_id IS NULL) "
        )
        params: list[Any] = [product_id, date_from, daily_to, product_id]
        market_country = normalize_market_country(country)
        if is_single_market_country(market_country):
            sql += "  AND m.market_country = %s "
            params.append(market_country)
        sql += "ORDER BY COALESCE(m.meta_business_date, m.report_date) ASC"
        daily_rows = query(
            sql,
            tuple(params),
        )
    rows.extend(daily_rows or [])
    if open_dates:
        rows.extend(
            _load_realtime_campaign_metrics(
                product_id=product_id,
                date_from=min(open_dates),
                date_to=max(open_dates),
                country=country,
            )
        )
    return rows


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _business_dates_between(date_from: date, date_to: date) -> list[date]:
    if not date_from or not date_to or date_from > date_to:
        return []
    dates: list[date] = []
    cur = date_from
    while cur <= date_to:
        dates.append(cur)
        cur += timedelta(days=1)
    return dates


def _open_business_dates_in_range(date_from: date, date_to: date) -> list[date]:
    from tools.meta_daily_final_sync import completed_meta_business_date

    closed_through = completed_meta_business_date()
    return [d for d in _business_dates_between(date_from, date_to) if d > closed_through]


def _sql_in(values: list[Any]) -> str:
    return ", ".join(["%s"] * len(values))


def _load_realtime_campaign_metrics(
    *,
    product_id: int,
    date_from: date,
    date_to: date,
    country: str | None = None,
) -> list[dict[str, Any]]:
    market_country = normalize_market_country(country)
    single_country = is_single_market_country(market_country)
    table_name = (
        "meta_ad_realtime_daily_ad_metrics"
        if single_country
        else "meta_ad_realtime_daily_campaign_metrics"
    )
    country_select = "m.country_code" if single_country else "NULL"
    country_filter = ""
    params: list[Any] = [product_id, date_from, date_to, date_from, date_to]
    if single_country:
        country_filter = " AND UPPER(COALESCE(m.country_code, '')) = %s"
        params.append(market_country)
    sql = (
        "SELECT m.business_date AS report_date, "
        "       m.ad_account_id, MAX(m.ad_account_name) AS ad_account_name, "
        "       m.normalized_campaign_code, MAX(m.campaign_name) AS campaign_name, "
        "       NULL AS product_id, NULL AS matched_product_code, "
        f"       {country_select} AS market_country, "
        "       o.id AS manual_override_id, "
        "       SUM(m.spend_usd) AS spend_usd, "
        "       SUM(m.result_count) AS result_count, "
        "       SUM(m.purchase_value_usd) AS purchase_value_usd, "
        "       NULL AS roas_purchase "
        f"FROM {table_name} m "
        "LEFT JOIN campaign_product_overrides o "
        "  ON o.normalized_campaign_code = m.normalized_campaign_code "
        " AND o.product_id = %s "
        "INNER JOIN ("
        "  SELECT business_date, ad_account_id, MAX(snapshot_at) AS snapshot_at "
        f"  FROM {table_name} "
        "  WHERE business_date BETWEEN %s AND %s "
        "    AND data_completeness='realtime_partial' "
        "  GROUP BY business_date, ad_account_id"
        ") latest "
        "ON m.business_date = latest.business_date "
        "AND (m.ad_account_id = latest.ad_account_id "
        "     OR (m.ad_account_id IS NULL AND latest.ad_account_id IS NULL)) "
        "AND m.snapshot_at = latest.snapshot_at "
        "WHERE m.business_date BETWEEN %s AND %s "
        "  AND m.data_completeness='realtime_partial'"
        f"{country_filter} "
        "GROUP BY m.business_date, m.ad_account_id, m.normalized_campaign_code, "
        "         o.id"
    )
    if single_country:
        sql += ", m.country_code"
    sql += " ORDER BY m.business_date ASC"
    return query(sql, tuple(params)) or []


def _load_profit_units_for_products(
    business_dates: list[date],
    product_ids: set[int],
    country: str | None = None,
) -> dict[tuple[date, int], int]:
    if not business_dates or not product_ids:
        return {}
    product_list = sorted(product_ids)
    sql = (
        "SELECT d.meta_business_date AS business_date, p.product_id, "
        "COALESCE(SUM(d.quantity), 0) AS units "
        "FROM order_profit_lines p "
        "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id "
        f"WHERE d.meta_business_date IN ({_sql_in(business_dates)}) "
        f"AND p.product_id IN ({_sql_in(product_list)}) "
    )
    params: list[Any] = list(business_dates) + product_list
    market_country = normalize_market_country(country)
    if is_single_market_country(market_country):
        sql += "AND p.buyer_country = %s "
        params.append(market_country)
    sql += "GROUP BY d.meta_business_date, p.product_id"
    rows = query(sql, tuple(params))
    out: dict[tuple[date, int], int] = {}
    for row in rows or []:
        business_date = _date_value(row.get("business_date"))
        product_id = row.get("product_id")
        if business_date and product_id is not None:
            out[(business_date, int(product_id))] = int(row.get("units") or 0)
    return out


def _load_daily_unmatched_campaign_metrics(
    date_from: date,
    date_to: date,
    country: str | None = None,
) -> list[dict[str, Any]]:
    """拉日终表里会计入未分摊广告费的广告行。"""
    market_country = normalize_market_country(country)
    if is_single_market_country(market_country):
        rows = query(
            "SELECT COALESCE(m.meta_business_date, m.report_date) AS report_date, "
            "       m.ad_account_id, m.ad_account_name, "
            "       m.normalized_ad_code AS normalized_campaign_code, "
            "       m.ad_name AS campaign_name, "
            "       m.product_id, mp.product_code AS matched_product_code, "
            "       mp.name AS matched_product_name, "
            "       m.spend_usd, m.result_count, m.purchase_value_usd "
            "FROM meta_ad_daily_ad_metrics m "
            "LEFT JOIN media_products mp ON mp.id = m.product_id "
            "WHERE COALESCE(m.meta_business_date, m.report_date) BETWEEN %s AND %s "
            "  AND m.market_country = %s "
            "ORDER BY COALESCE(m.meta_business_date, m.report_date) ASC",
            (date_from, date_to, market_country),
        )
    else:
        rows = query(
            "SELECT COALESCE(m.meta_business_date, m.report_date) AS report_date, "
            "       m.ad_account_id, m.ad_account_name, "
            "       m.normalized_campaign_code, m.campaign_name, "
            "       m.product_id, "
            "       COALESCE(m.matched_product_code, mp.product_code) AS matched_product_code, "
            "       mp.name AS matched_product_name, "
            "       m.spend_usd, m.result_count, m.purchase_value_usd "
            "FROM meta_ad_daily_campaign_metrics m "
            "LEFT JOIN media_products mp ON mp.id = m.product_id "
            "WHERE COALESCE(m.meta_business_date, m.report_date) BETWEEN %s AND %s "
            "ORDER BY COALESCE(m.meta_business_date, m.report_date) ASC",
            (date_from, date_to),
        )

    if not rows:
        return []

    business_dates = sorted(
        {
            business_date
            for business_date in (_date_value(row.get("report_date")) for row in rows)
            if business_date
        }
    )
    product_ids = {
        int(row["product_id"])
        for row in rows
        if row.get("product_id") is not None
    }
    units_by_product = _load_profit_units_for_products(
        business_dates,
        product_ids,
        market_country,
    )

    out: list[dict[str, Any]] = []
    for row in rows:
        spend = Decimal(str(row.get("spend_usd") or 0))
        if spend <= 0:
            continue
        business_date = _date_value(row.get("report_date"))
        product_id = row.get("product_id")
        item = dict(row)
        item["report_date"] = business_date
        item["allocation_reason"] = "unmatched_product"
        item["matched_product_id"] = None
        item["matched_profit_units"] = None
        if product_id is not None:
            pid = int(product_id)
            units = int(units_by_product.get((business_date, pid)) or 0)
            if units > 0:
                continue
            item["allocation_reason"] = "matched_no_units"
            item["matched_product_id"] = pid
            item["matched_profit_units"] = units
        out.append(item)
    return out


def _load_realtime_unmatched_campaign_metrics(
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    """拉开放业务日实时快照中会计入未分摊广告费的 campaign 行。"""
    business_dates = _business_dates_between(date_from, date_to)
    if not business_dates:
        return []
    snapshot_rows = query(
        "SELECT business_date, ad_account_id, MAX(snapshot_at) AS snapshot_at "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        f"WHERE business_date IN ({_sql_in(business_dates)}) "
        "AND data_completeness='realtime_partial' "
        "GROUP BY business_date, ad_account_id",
        tuple(business_dates),
    ) or []

    rows: list[dict[str, Any]] = []
    for snapshot in snapshot_rows:
        business_date = _date_value(snapshot.get("business_date"))
        snapshot_at = snapshot.get("snapshot_at")
        if not business_date or not snapshot_at:
            continue
        ad_account_id = snapshot.get("ad_account_id")
        select_sql = (
            "SELECT business_date AS report_date, ad_account_id, "
            "MAX(ad_account_name) AS ad_account_name, "
            "campaign_name, normalized_campaign_code, "
            "SUM(spend_usd) AS spend_usd, "
            "SUM(result_count) AS result_count, "
            "SUM(purchase_value_usd) AS purchase_value_usd "
            "FROM meta_ad_realtime_daily_campaign_metrics "
            "WHERE business_date=%s "
        )
        params: list[Any] = [business_date]
        if ad_account_id is None:
            select_sql += "AND ad_account_id IS NULL "
        else:
            select_sql += "AND ad_account_id=%s "
            params.append(ad_account_id)
        select_sql += (
            "AND snapshot_at=%s "
            "AND data_completeness='realtime_partial' "
            "GROUP BY business_date, ad_account_id, campaign_name, normalized_campaign_code "
            "ORDER BY spend_usd DESC"
        )
        params.append(snapshot_at)
        rows.extend(query(select_sql, tuple(params)) or [])

    if not rows:
        return []

    annotated: list[dict[str, Any]] = []
    product_ids: set[int] = set()
    match_cache: dict[str, dict[str, Any] | None] = {}
    for row in rows:
        spend = Decimal(str(row.get("spend_usd") or 0))
        if spend <= 0:
            continue
        code = str(
            row.get("normalized_campaign_code") or row.get("campaign_name") or ""
        ).strip()
        if not code:
            continue
        lookup = code.lower()
        if lookup not in match_cache:
            match_cache[lookup] = resolve_ad_product_match(lookup)
        match = match_cache[lookup]
        item = dict(row)
        item["report_date"] = _date_value(row.get("report_date"))
        item["normalized_campaign_code"] = code
        item["allocation_reason"] = "unmatched_product"
        item["matched_product_id"] = None
        item["matched_product_code"] = None
        item["matched_product_name"] = None
        if match and match.get("id") is not None:
            product_id = int(match["id"])
            item["matched_product_id"] = product_id
            item["matched_product_code"] = match.get("product_code")
            item["matched_product_name"] = match.get("name") or match.get("product_name")
            item["_matched_product_id"] = product_id
            product_ids.add(product_id)
        annotated.append(item)

    units_by_product = _load_profit_units_for_products(business_dates, product_ids)
    out: list[dict[str, Any]] = []
    for item in annotated:
        product_id = item.pop("_matched_product_id", None)
        if product_id is not None:
            business_date = _date_value(item.get("report_date"))
            units = int(units_by_product.get((business_date, int(product_id))) or 0)
            if units > 0:
                continue
            item["allocation_reason"] = "matched_no_units"
            item["matched_profit_units"] = units
        out.append(item)
    return out


def _load_unmatched_campaign_metrics(
    date_from: date,
    date_to: date,
    country: str | None = None,
) -> list[dict[str, Any]]:
    """拉日期范围内会计入未分摊广告费的广告行，供全局排查入口使用。"""
    market_country = normalize_market_country(country)
    open_dates = _open_business_dates_in_range(date_from, date_to)
    daily_to = min(open_dates) - timedelta(days=1) if open_dates else date_to
    rows: list[dict[str, Any]] = []
    if daily_to >= date_from:
        rows.extend(_load_daily_unmatched_campaign_metrics(date_from, daily_to, country))
    if open_dates and not is_single_market_country(market_country):
        rows.extend(
            _load_realtime_unmatched_campaign_metrics(
                min(open_dates),
                max(open_dates),
            )
        )
    return rows


def _load_campaign_perf(
    normalized_codes: set[str], date_from: date, date_to: date
) -> dict[str, dict[str, int]]:
    """从 period 主表 ``meta_ad_campaign_metrics`` 拉 impressions / link_clicks。

    日表没有这两个字段，period 表按 ``report_start_date / report_end_date`` 分桶。
    用 ``report_start_date BETWEEN ... OR report_end_date BETWEEN ...`` 拉与日期范围
    有重叠的 period，再按 ``normalized_campaign_code`` SUM。结果是估算（多 period
    完全覆盖范围时偏多），但供"花费量级 + 点击量级"展示已够用。

    Returns:
        ``{normalized_campaign_code: {"impressions": int, "clicks": int}}``
    """
    if not normalized_codes:
        return {}
    placeholders = ",".join(["%s"] * len(normalized_codes))
    sql = (
        "SELECT normalized_campaign_code AS code, "
        "       SUM(impressions) AS impressions, "
        "       SUM(link_clicks) AS clicks "
        "FROM meta_ad_campaign_metrics "
        f"WHERE normalized_campaign_code IN ({placeholders}) "
        "  AND (report_start_date BETWEEN %s AND %s "
        "       OR report_end_date BETWEEN %s AND %s "
        "       OR (report_start_date <= %s AND report_end_date >= %s)) "
        "GROUP BY normalized_campaign_code"
    )
    params: list[Any] = list(normalized_codes) + [
        date_from, date_to,
        date_from, date_to,
        date_from, date_to,
    ]
    rows = query(sql, tuple(params))
    return {
        (r["code"] or "").strip(): {
            "impressions": int(r["impressions"] or 0),
            "clicks": int(r["clicks"] or 0),
        }
        for r in rows
        if (r["code"] or "").strip()
    }


def _load_match_map(normalized_codes: set[str]) -> dict[str, int | None]:
    """``normalized_campaign_code`` → ``product_id``（命中），或 None（未匹配）。

    通过 ``meta_ads.resolve_ad_product_match`` 逐个解析。``resolve_ad_product_match``
    内部对输入会做 ``.strip().lower()`` 归一化 + 尝试 ``-rjc`` 后缀变体 + 查 override 表，
    所以传 normalized code 进去结果一致。这里独立函数方便测试 mock。

    Returns:
        ``{normalized_campaign_code: product_id | None}``
    """
    result: dict[str, int | None] = {}
    for code in normalized_codes:
        match = resolve_ad_product_match(code)
        # resolve_ad_product_match 返回结构：{id, product_code, name} 或 None
        result[code] = match.get("id") if match else None
    return result


def _load_attributed_orders(
    product_id: int,
    date_from: date,
    date_to: date,
    country: str | None = None,
) -> dict[date, dict[str, Any]]:
    """同期同产品订单按 Meta business_date 聚合（USD 金额 + 订单去重计数）。

    Returns:
        ``{business_date: {revenue, purchase, shipping, reserve, order_count}}``
        ``order_count`` 用 ``COUNT(DISTINCT dxm_package_id)`` 去重，与
        ``product_profit_list`` 口径一致（同一订单含多 SKU 行只算 1 单）。

    country 过滤：传非空且不是 "all" 时按 ``buyer_country`` 严格过滤（已 upper 归一化）。
    """
    sql = (
        "SELECT dol.meta_business_date AS d, "
        "       SUM(opl.revenue_usd) AS revenue, "
        "       SUM(opl.purchase_usd) AS purchase, "
        "       SUM(opl.shipping_cost_usd) AS shipping, "
        "       SUM(opl.return_reserve_usd) AS reserve, "
        "       COUNT(DISTINCT dol.dxm_package_id) AS order_count "
        "FROM order_profit_lines opl "
        "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
        "WHERE opl.product_id = %s "
        "  AND dol.meta_business_date BETWEEN %s AND %s "
    )
    params: list[Any] = [product_id, date_from, date_to]
    normalized_country = normalize_market_country(country)
    if normalized_country:
        sql += " AND opl.buyer_country = %s "
        params.append(normalized_country)
    sql += " GROUP BY dol.meta_business_date"

    rows = query(sql, tuple(params))
    return {
        r["d"]: {
            "revenue": Decimal(str(r["revenue"] or 0)),
            "purchase": Decimal(str(r["purchase"] or 0)),
            "shipping": Decimal(str(r["shipping"] or 0)),
            "reserve": Decimal(str(r["reserve"] or 0)),
            "order_count": int(r["order_count"] or 0),
        }
        for r in rows
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def generate_ads_report(
    *,
    product_id: int,
    date_from: date,
    date_to: date,
    country: str | None = None,
) -> dict[str, Any]:
    """生成广告明细报表（Tab ④ 数据源）。

    Args:
        product_id: media_products.id
        date_from / date_to: Meta 业务日期范围（广告按 ``meta_business_date``，
            订单按 ``dianxiaomi_order_lines.meta_business_date``）
        country: 可选国家过滤；订单侧用 buyer_country，campaign 侧用 market_country

    Returns:
        {
          "accounts": [
            {"ad_account_id": str, "label": str, "spend_usd": float,
             "result_count": int, "impressions": int, "clicks": int,
             "attributed_revenue_usd": float, "roas": float | None}
          ],
          "campaigns": [
            {"normalized_campaign_code": str, "campaign_name": str,
             "ad_account_id": str, "ad_account_name": str,
             "spend_usd": float, "result_count": int,
             "impressions": int, "clicks": int,
             "ctr": float, "cpc": float | None,
             "purchase_value_usd": float, "roas_meta": float | None,
             "attributed_order_count": int, "attributed_revenue_usd": float,
             "roas": float | None, "profit_contribution_usd": float}, ...
          ],
          "daily": [{"date": "YYYY-MM-DD", "spend_usd": float, "revenue_usd": float}, ...],
          "unmatched": [{"normalized_campaign_code": str, "campaign_name": str,
                         "spend_usd": float}, ...]
        }
    """
    from ._open_day_freshness import ensure_open_day_profit_lines_fresh
    ensure_open_day_profit_lines_fresh(date_from, date_to)

    rows = _load_campaign_metrics(product_id, date_from, date_to, country)
    if not rows:
        return {"accounts": [], "campaigns": [], "daily": [], "unmatched": []}

    # 收集所有 product_id 仍 NULL 的 normalized_campaign_code → 让 resolve_ad_product_match 兜底解析
    null_codes: set[str] = {
        (r.get("normalized_campaign_code") or "").strip()
        for r in rows
        if r.get("product_id") is None and (r.get("normalized_campaign_code") or "").strip()
    }
    match_map = _load_match_map(null_codes) if null_codes else {}

    attributed = _load_attributed_orders(product_id, date_from, date_to, country)

    # campaign 聚合（key: normalized_campaign_code）
    def _campaign_zero() -> dict[str, Any]:
        return {
            "normalized_campaign_code": "",
            "campaign_name": "",
            "ad_account_id": "",
            "ad_account_name": "",
            "manual_override_id": None,
            "spend": Decimal("0"),
            "result_count": 0,
            "purchase_value": Decimal("0"),
        }

    by_campaign: dict[str, dict[str, Any]] = defaultdict(_campaign_zero)
    daily_spend: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    # (norm_code, date) → daily spend，用于按日分摊
    daily_campaign_spend: dict[tuple[str, date], Decimal] = defaultdict(lambda: Decimal("0"))
    unmatched: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"normalized_campaign_code": "", "campaign_name": "", "spend": Decimal("0")}
    )

    for r in rows:
        norm = (r.get("normalized_campaign_code") or "").strip()
        if not norm:
            continue
        # 决定本行归属哪个 product_id：表里已回填 → 直接用；NULL → 通过 match_map 兜底
        row_pid = r.get("product_id")
        if row_pid is None:
            row_pid = match_map.get(norm)

        spend = Decimal(str(r.get("spend_usd") or 0))

        if row_pid != product_id:
            # 不属于当前产品：仅当依旧未匹配（None）时进 unmatched 桶；
            # 已匹配到别的产品 → 静默跳过（不污染 unmatched 列表）
            if row_pid is None:
                u = unmatched[norm]
                u["normalized_campaign_code"] = norm
                u["campaign_name"] = (r.get("campaign_name") or "").strip()
                u["spend"] += spend
            continue

        b = by_campaign[norm]
        b["normalized_campaign_code"] = norm
        b["campaign_name"] = (r.get("campaign_name") or "").strip()
        b["ad_account_id"] = r.get("ad_account_id") or ""
        b["ad_account_name"] = r.get("ad_account_name") or ""
        if r.get("manual_override_id") is not None:
            b["manual_override_id"] = int(r["manual_override_id"])
        b["spend"] += spend
        b["result_count"] += int(r.get("result_count") or 0)
        b["purchase_value"] += Decimal(str(r.get("purchase_value_usd") or 0))
        report_date = r.get("report_date")
        if report_date is not None:
            daily_spend[report_date] += spend
            daily_campaign_spend[(norm, report_date)] += spend

    # period 表里的 impressions / link_clicks（按 norm code SUM）
    matched_codes: set[str] = {norm for norm in by_campaign.keys() if norm}
    perf_map = _load_campaign_perf(matched_codes, date_from, date_to) if matched_codes else {}

    # 按"当日 spend 占比 × 当日 attributed.*"分摊归属收入 / 成本 / 订单数
    # campaign.attributed_revenue = Σ_d daily_campaign_spend[(norm, d)] / daily_spend[d] × attributed[d].revenue
    attributed_per_campaign: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "revenue": Decimal("0"),
            "costs": Decimal("0"),
            "orders": 0.0,
        }
    )
    for (norm, d), spend_in_day in daily_campaign_spend.items():
        day_total_spend = daily_spend.get(d, Decimal("0"))
        if day_total_spend <= 0:
            continue
        share = spend_in_day / day_total_spend
        day_attr = attributed.get(d)
        if not day_attr:
            continue
        bucket = attributed_per_campaign[norm]
        bucket["revenue"] += day_attr["revenue"] * share
        bucket["costs"] += (
            day_attr["purchase"] + day_attr["shipping"] + day_attr["reserve"]
        ) * share
        # orders 用浮点累加，最终 round 一次（每日 round 累加误差更大）
        bucket["orders"] += float(day_attr["order_count"]) * float(share)

    campaigns: list[dict[str, Any]] = []
    by_account: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "ad_account_id": "",
            "label": "",
            "spend": Decimal("0"),
            "result_count": 0,
            "impressions": 0,
            "clicks": 0,
            "attributed_revenue": Decimal("0"),
        }
    )

    for norm, b in by_campaign.items():
        spend = b["spend"]
        attr = attributed_per_campaign.get(norm, {"revenue": Decimal("0"), "costs": Decimal("0"), "orders": 0.0})
        attributed_revenue_share = attr["revenue"]
        attributed_costs_share = attr["costs"]
        attributed_orders = int(round(attr["orders"]))

        perf = perf_map.get(norm, {})
        impressions = int(perf.get("impressions") or 0)
        clicks = int(perf.get("clicks") or 0)
        ctr = float(clicks) / float(impressions) if impressions > 0 else 0.0
        cpc = float(spend) / float(clicks) if clicks > 0 else None

        roas = float(attributed_revenue_share / spend) if spend > 0 else None
        roas_meta = (
            float(b["purchase_value"] / spend) if spend > 0 and b["purchase_value"] > 0 else None
        )
        profit = attributed_revenue_share - spend - attributed_costs_share

        campaigns.append({
            "normalized_campaign_code": norm,
            "campaign_name": b["campaign_name"],
            "ad_account_id": b["ad_account_id"],
            "ad_account_name": b["ad_account_name"],
            "manual_override_id": b.get("manual_override_id"),
            "spend_usd": float(spend),
            "result_count": b["result_count"],
            "impressions": impressions,
            "clicks": clicks,
            "ctr": ctr,
            "cpc": cpc,
            "purchase_value_usd": float(b["purchase_value"]),
            "roas_meta": roas_meta,
            "attributed_order_count": attributed_orders,
            "attributed_revenue_usd": float(attributed_revenue_share),
            "roas": roas,
            "profit_contribution_usd": float(profit),
        })

        acc = by_account[b["ad_account_id"]]
        acc["ad_account_id"] = b["ad_account_id"]
        acc["label"] = b["ad_account_name"] or acc["label"]
        acc["spend"] += spend
        acc["result_count"] += b["result_count"]
        acc["impressions"] += impressions
        acc["clicks"] += clicks
        acc["attributed_revenue"] += attributed_revenue_share

    campaigns.sort(key=lambda c: -c["spend_usd"])

    accounts: list[dict[str, Any]] = []
    for aid, a in by_account.items():
        spend = a["spend"]
        roas = float(a["attributed_revenue"] / spend) if spend > 0 else None
        accounts.append({
            "ad_account_id": aid,
            "label": a["label"],
            "spend_usd": float(spend),
            "result_count": a["result_count"],
            "impressions": a["impressions"],
            "clicks": a["clicks"],
            "attributed_revenue_usd": float(a["attributed_revenue"]),
            "roas": roas,
        })
    accounts.sort(key=lambda a: -a["spend_usd"])

    daily = sorted(
        [
            {
                "date": d.isoformat(),
                "spend_usd": float(s),
                "revenue_usd": float(attributed.get(d, {}).get("revenue", Decimal("0"))),
            }
            for d, s in daily_spend.items()
        ],
        key=lambda x: x["date"],
    )

    unmatched_list = sorted(
        [
            {
                "normalized_campaign_code": norm,
                "campaign_name": u["campaign_name"],
                "spend_usd": float(u["spend"]),
            }
            for norm, u in unmatched.items()
        ],
        key=lambda x: -x["spend_usd"],
    )

    return {
        "accounts": accounts,
        "campaigns": campaigns,
        "daily": daily,
        "unmatched": unmatched_list,
    }


def generate_unmatched_ads_report(
    *,
    date_from: date,
    date_to: date,
    country: str | None = None,
) -> dict[str, Any]:
    """生成全局未分摊广告列表，给 Tab ④ 的 summary 跳转入口使用。"""
    from ._open_day_freshness import ensure_open_day_profit_lines_fresh

    ensure_open_day_profit_lines_fresh(date_from, date_to)
    rows = _load_unmatched_campaign_metrics(date_from, date_to, country)
    if not rows:
        return {
            "accounts": [],
            "campaigns": [],
            "daily": [],
            "unmatched": [],
            "allocated_ad_spend_usd": 0.0,
            "unallocated_ad_spend_usd": 0.0,
            "unallocated_launch_segment_summary": _empty_launch_segment_summary(
                product_ad_launch.NEW_PRODUCT_WINDOW_DAYS
            ),
        }

    unmatched: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "normalized_campaign_code": "",
            "campaign_name": "",
            "ad_account_id": "",
            "ad_account_name": "",
            "spend": Decimal("0"),
            "result_count": 0,
            "purchase_value": Decimal("0"),
            "last_seen": None,
            "allocation_reasons": set(),
            "matched_product_id": None,
            "matched_product_code": None,
            "matched_product_name": None,
            "matched_profit_units": None,
        }
    )

    for r in rows:
        norm = (r.get("normalized_campaign_code") or "").strip()
        if not norm:
            continue
        spend = Decimal(str(r.get("spend_usd") or 0))
        bucket = unmatched[norm]
        bucket["normalized_campaign_code"] = norm
        bucket["campaign_name"] = (r.get("campaign_name") or "").strip()
        bucket["ad_account_id"] = r.get("ad_account_id") or ""
        bucket["ad_account_name"] = r.get("ad_account_name") or ""
        bucket["spend"] += spend
        bucket["result_count"] += int(r.get("result_count") or 0)
        bucket["purchase_value"] += Decimal(str(r.get("purchase_value_usd") or 0))
        reason = (r.get("allocation_reason") or "unmatched_product").strip()
        if reason:
            bucket["allocation_reasons"].add(reason)
        if bucket["matched_product_id"] is None and r.get("matched_product_id") is not None:
            bucket["matched_product_id"] = r.get("matched_product_id")
            bucket["matched_product_code"] = r.get("matched_product_code")
            bucket["matched_product_name"] = r.get("matched_product_name")
        if bucket["matched_profit_units"] is None and r.get("matched_profit_units") is not None:
            bucket["matched_profit_units"] = int(r.get("matched_profit_units") or 0)
        report_date = r.get("report_date")
        if report_date is not None and (
            bucket["last_seen"] is None or report_date > bucket["last_seen"]
        ):
            bucket["last_seen"] = report_date

    unmatched_list = []
    for norm, item in unmatched.items():
        spend = item["spend"]
        purchase_value = item["purchase_value"]
        last_seen = item["last_seen"]
        reasons = sorted(item["allocation_reasons"])
        reason = reasons[0] if len(reasons) == 1 else "mixed"
        unmatched_list.append({
            "normalized_campaign_code": norm,
            "campaign_name": item["campaign_name"],
            "ad_account_id": item["ad_account_id"],
            "ad_account_name": item["ad_account_name"],
            "spend_usd": float(spend),
            "result_count": item["result_count"],
            "purchase_value_usd": float(purchase_value),
            "roas_meta": float(purchase_value / spend) if spend > 0 and purchase_value > 0 else None,
            "last_seen": last_seen.isoformat() if hasattr(last_seen, "isoformat") else last_seen,
            "allocation_reason": reason,
            "allocation_label": _allocation_reason_label(reason),
            "matched_product_id": item["matched_product_id"],
            "matched_product_code": item["matched_product_code"],
            "matched_product_name": item["matched_product_name"],
            "matched_profit_units": item["matched_profit_units"],
        })
    unmatched_list.sort(key=lambda x: -x["spend_usd"])
    total_unmatched_spend = sum(float(item["spend_usd"] or 0) for item in unmatched_list)
    launch_segment_summary = _attach_launch_segments_to_unmatched(unmatched_list)

    return {
        "accounts": [],
        "campaigns": [],
        "daily": [],
        "unmatched": unmatched_list,
        "allocated_ad_spend_usd": 0.0,
        "unallocated_ad_spend_usd": total_unmatched_spend,
        "unallocated_launch_segment_summary": launch_segment_summary,
    }
