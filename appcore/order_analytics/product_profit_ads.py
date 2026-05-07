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
  - country 过滤：仅透传给 ``_load_attributed_orders`` 的订单 SQL。campaign 维度的国家
    通常没有意义（同一 campaign 可投到多国），所以 campaign 行不做 country 过滤。
"""
from __future__ import annotations

import logging
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from .meta_ads import resolve_ad_product_match

log = logging.getLogger(__name__)


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
    product_id: int, date_from: date, date_to: date
) -> list[dict[str, Any]]:
    """拉 ``meta_ad_daily_campaign_metrics`` 在日期范围内与本产品相关的所有行。

    谓词：``report_date BETWEEN ...`` AND (``product_id = %s`` OR ``product_id IS NULL``)。
    把未回填的 NULL 行也拉进来，让上层 ``_load_match_map`` 通过
    ``resolve_ad_product_match`` / override 表做兜底实时解析（覆盖同步流程还没回写
    product_id 的 race condition）。
    """
    return query(
        "SELECT report_date, ad_account_id, ad_account_name, "
        "       normalized_campaign_code, campaign_name, "
        "       product_id, matched_product_code, "
        "       spend_usd, result_count, purchase_value_usd, roas_purchase "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE report_date BETWEEN %s AND %s "
        "  AND (product_id = %s OR product_id IS NULL) "
        "ORDER BY report_date ASC",
        (date_from, date_to, product_id),
    )


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
    """同期同产品订单按 business_date 聚合（USD 金额 + 订单去重计数）。

    Returns:
        ``{business_date: {revenue, purchase, shipping, reserve, order_count}}``
        ``order_count`` 用 ``COUNT(DISTINCT dxm_package_id)`` 去重，与
        ``product_profit_list`` 口径一致（同一订单含多 SKU 行只算 1 单）。

    country 过滤：传非空且不是 "all" 时按 ``buyer_country`` 严格过滤（已 upper 归一化）。
    """
    sql = (
        "SELECT opl.business_date AS d, "
        "       SUM(opl.revenue_usd) AS revenue, "
        "       SUM(opl.purchase_usd) AS purchase, "
        "       SUM(opl.shipping_cost_usd) AS shipping, "
        "       SUM(opl.return_reserve_usd) AS reserve, "
        "       COUNT(DISTINCT dol.dxm_package_id) AS order_count "
        "FROM order_profit_lines opl "
        "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
        "WHERE opl.product_id = %s "
        "  AND opl.business_date BETWEEN %s AND %s "
    )
    params: list[Any] = [product_id, date_from, date_to]
    if country and country.strip().lower() not in ("", "all"):
        sql += " AND opl.buyer_country = %s "
        params.append(country.strip().upper())
    sql += " GROUP BY opl.business_date"

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
        date_from / date_to: 业务日期范围（按 ``report_date`` / ``business_date``）
        country: 可选 buyer_country 过滤（仅作用于"归属订单"侧）

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
    rows = _load_campaign_metrics(product_id, date_from, date_to)
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
