"""订单利润核算回填脚本（一次性回填 + 增量重跑都用这个）。

按月份分批跑，避免一次跑爆内存。每行调 calculate_line_profit，
upsert 到 order_profit_lines 表。

用法：
  # dry-run（只预览前 N 条，不写库）
  python tools/order_profit_backfill.py --from 2026-04-01 --to 2026-04-07 --dry-run

  # 全量历史回填
  python tools/order_profit_backfill.py --from 2026-02-25 --to 2026-05-04

  # 只算某一天（增量调试）
  python tools/order_profit_backfill.py --from 2026-05-04 --to 2026-05-04
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore.db import query, query_one
from appcore import exchange_rates
from appcore.order_analytics.cost_allocation import (
    allocate_shipping_to_line,
    get_sku_daily_ad_spend,
    get_sku_daily_units,
    get_unallocated_ad_spend,
)
from appcore.order_analytics.cost_completeness import _safe_positive, check_sku_cost_completeness
from appcore.order_analytics.profit_calculation import (
    aggregate_order_profit,
    calculate_line_profit,
)
from appcore.order_analytics.profit_repository import (
    finish_profit_run,
    start_profit_run,
    upsert_profit_line,
)
from appcore.order_analytics.shopify_fee_resolver import (
    _parse_effective_at as _parse_dynamic_fee_effective_at,
    is_dynamic_fee_effective,
    resolve_shopify_fee_for_order,
)
log = logging.getLogger(__name__)

PURCHASE_SANITY_MAX_REVENUE_RATIO = Decimal("1.0")


_LINE_QUERY = (
    "SELECT d.id AS dxm_order_line_id, d.product_id, d.quantity, "
    "       d.line_amount, d.order_amount, d.ship_amount, d.buyer_country, "
    "       d.order_paid_at, d.paid_at, d.meta_business_date, d.dxm_package_id, "
    "       d.site_code, d.dxm_order_id, d.extended_order_id, d.package_number, "
    "       d.attribution_time_at, d.order_created_at, "
    "       d.logistic_fee, "
    # 采购价：优先用订单上的 snapshot（订单付款时点冻结的值），
    # 没有就 fallback 到 media_products.purchase_price（当前值）。
    "       COALESCE(d.purchase_price_cny, m.purchase_price) AS purchase_price, "
    "       d.purchase_price_cny AS purchase_price_snapshot_cny, "
    "       d.purchase_price_at  AS purchase_price_snapshot_at, "
    "       m.packet_cost_actual, m.packet_cost_estimated "
    "FROM dianxiaomi_order_lines d "
    "LEFT JOIN media_products m ON m.id = d.product_id "
    "WHERE d.meta_business_date BETWEEN %s AND %s "
    "ORDER BY d.id"
)


def _purchase_price_source(line: dict[str, Any], purchase_price: float | None) -> str | None:
    if purchase_price is None:
        return None
    if _safe_positive(line.get("purchase_price_snapshot_cny")) is not None:
        return "order_snapshot"
    return "media_product"


def _purchase_price_sanity(
    *,
    purchase_price_cny: float | None,
    quantity: int,
    rmb_per_usd: Decimal,
    line_revenue_usd: float,
    source: str | None,
) -> dict[str, Any] | None:
    if purchase_price_cny is None or quantity <= 0 or rmb_per_usd <= 0:
        return None
    revenue = Decimal(str(line_revenue_usd or 0))
    if revenue <= 0:
        return None
    purchase = Decimal(str(purchase_price_cny)) * Decimal(quantity) / rmb_per_usd
    max_allowed = revenue * PURCHASE_SANITY_MAX_REVENUE_RATIO
    if purchase <= max_allowed:
        return None
    return {
        "reason": "purchase_usd_exceeds_line_revenue",
        "source": source,
        "purchase_price_cny": float(Decimal(str(purchase_price_cny))),
        "quantity": int(quantity),
        "purchase_usd": float(purchase.quantize(Decimal("0.0001"))),
        "line_revenue_usd": float(revenue.quantize(Decimal("0.0001"))),
        "max_revenue_ratio": float(PURCHASE_SANITY_MAX_REVENUE_RATIO),
    }


def _iter_months(date_from: date, date_to: date):
    """yield (month_start, month_end) 闭区间，分月分批。"""
    cur = date(date_from.year, date_from.month, 1)
    while cur <= date_to:
        if cur.month == 12:
            next_first = date(cur.year + 1, 1, 1)
        else:
            next_first = date(cur.year, cur.month + 1, 1)
        month_end = next_first - timedelta(days=1)
        yield max(cur, date_from), min(month_end, date_to)
        cur = next_first


def _resolve_package_group_key(line: dict) -> str:
    site_code = str(line.get("site_code") or "").strip()
    values = [
        str(line.get("dxm_package_id") or "").strip(),
        str(line.get("dxm_order_id") or "").strip(),
        str(line.get("package_number") or "").strip(),
    ]
    if any(values):
        return "package:" + "|".join([site_code, *values])
    return f"line:{line.get('dxm_order_line_id')}"


def _resolve_shopify_fee_group_key(line: dict) -> str:
    site_code = str(line.get("site_code") or "").strip()
    for field in ("extended_order_id", "package_number"):
        value = str(line.get(field) or "").strip()
        if value:
            return f"shopify:{site_code}|{field}:{value}"
    return _resolve_package_group_key(line)


def _shopify_order_names(line: dict) -> list[str | None]:
    return [line.get("extended_order_id"), line.get("package_number")]


def _compute_group_total_line_amount(lines: list[dict], key_func) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for line in lines:
        order_key = key_func(line)
        amt = line.get("line_amount") or 0
        totals[order_key] += float(amt)
    return totals


def _compute_order_total_line_amount(lines: list[dict]) -> dict[str, float]:
    """按包裹级 key 聚合 line_amount 总和（用于运费按比例摊到行）。"""
    return _compute_group_total_line_amount(lines, _resolve_package_group_key)


def _compute_package_shipping(lines: list[dict]) -> dict[str, float]:
    """按包裹级 key 取一次 ship_amount（订单级运费，重复行取首个非 None）。"""
    shipping: dict[str, float] = {}
    for line in lines:
        package_key = _resolve_package_group_key(line)
        if package_key in shipping:
            continue
        amt = line.get("ship_amount")
        shipping[package_key] = float(amt) if amt is not None else 0.0
    return shipping


def _compute_shopify_fee_shipping(lines: list[dict], package_shipping: dict[str, float]) -> dict[str, float]:
    shipping: dict[str, float] = defaultdict(float)
    seen: set[tuple[str, str]] = set()
    for line in lines:
        fee_key = _resolve_shopify_fee_group_key(line)
        package_key = _resolve_package_group_key(line)
        marker = (fee_key, package_key)
        if marker in seen:
            continue
        seen.add(marker)
        shipping[fee_key] += package_shipping.get(package_key, 0)
    return shipping


def _resolve_business_date(line: dict) -> date | None:
    business_date = line.get("meta_business_date")
    if business_date is None and line.get("order_paid_at"):
        business_date = line["order_paid_at"].date()
    return business_date


def _resolve_order_time(line: dict) -> datetime | None:
    return (
        line.get("order_paid_at")
        or line.get("attribution_time_at")
        or line.get("order_created_at")
    )


def _should_skip_for_dynamic_fee_boundary(line: dict) -> bool:
    if _parse_dynamic_fee_effective_at() is None:
        return True
    order_time = _resolve_order_time(line)
    if order_time is None:
        return True
    return not is_dynamic_fee_effective(order_time)


def _process_line(
    line: dict,
    *,
    order_total_amount: float,
    order_shipping: float,
    sku_units_cache: dict,
    sku_spend_cache: dict,
    rmb_per_usd: Decimal,
    return_reserve_rate: Decimal,
    exchange_rate_basis: dict[str, Any] | None = None,
    shopify_fee_result: dict[str, Any] | None = None,
    fee_total_revenue_usd: float | None = None,
) -> dict:
    """单行核算。返回 calculate_line_profit 结果（含 status, profit_usd 等）。"""
    business_date = _resolve_business_date(line)
    product_id = line.get("product_id")
    line_amount = float(line.get("line_amount") or 0)
    quantity = int(line.get("quantity") or 1)

    shipping_alloc = allocate_shipping_to_line(
        line_amount=line_amount,
        order_total_line_amount=order_total_amount,
        order_shipping_usd=order_shipping,
    )

    # 采购价完备性
    purchase_price = _safe_positive(line.get("purchase_price"))
    purchase_source = _purchase_price_source(line, purchase_price)
    purchase_sanity = _purchase_price_sanity(
        purchase_price_cny=purchase_price,
        quantity=quantity,
        rmb_per_usd=rmb_per_usd,
        line_revenue_usd=line_amount + float(shipping_alloc or 0),
        source=purchase_source,
    )
    if purchase_sanity is not None:
        purchase_price = None
    missing: list[str] = []
    if purchase_price is None:
        missing.append("purchase_price")

    # 小包成本——三级降级链
    shipping_cost_cny: float | None = None
    shipping_cost_source: str | None = None

    logistic_fee = line.get("logistic_fee")
    if logistic_fee is not None:
        lf = float(logistic_fee)
        if lf > 0 and order_total_amount > 0:
            shipping_cost_cny = lf * (line_amount / order_total_amount)
            shipping_cost_source = "order_logistic_fee"

    if shipping_cost_cny is None:
        actual = _safe_positive(line.get("packet_cost_actual"))
        estimated = _safe_positive(line.get("packet_cost_estimated"))
        if actual is not None:
            shipping_cost_cny = actual * quantity
            shipping_cost_source = "product_actual"
        elif estimated is not None:
            shipping_cost_cny = estimated * quantity
            shipping_cost_source = "product_estimated"

    if shipping_cost_cny is None:
        missing.append("shipping_cost")

    # 不再 early return：缺采购价/物流成本时仍走完整 calc，
    # calculate_line_profit 内部会用 PURCHASE_FALLBACK_RATIO / SHIPPING_FALLBACK_RATIO
    # 估算成本，profit 仍可算出，status='incomplete' 标记估算来源。

    # 当日 SKU 总 units / spend（带缓存避免重复查 DB）
    cache_key = (product_id, business_date)
    if cache_key not in sku_units_cache:
        sku_units_cache[cache_key] = get_sku_daily_units(
            product_id=product_id, business_date=business_date,
        )
    if cache_key not in sku_spend_cache:
        sku_spend_cache[cache_key] = get_sku_daily_ad_spend(
            product_id=product_id, business_date=business_date,
        )

    # 运费收入摊到行
    shipping_alloc = allocate_shipping_to_line(
        line_amount=line_amount,
        order_total_line_amount=order_total_amount,
        order_shipping_usd=order_shipping,
    )

    # H1 修复用：订单总营收（line_amount 之和 + 订单运费）→ profit_calculation 按订单算 fee 一次再摊回
    if fee_total_revenue_usd is None:
        order_total_revenue_usd = float(order_total_amount or 0) + float(order_shipping or 0)
    else:
        order_total_revenue_usd = float(fee_total_revenue_usd or 0)

    line_input = {
        "dxm_order_line_id": line["dxm_order_line_id"],
        "product_id": product_id,
        "site_code": line.get("site_code"),
        "extended_order_id": line.get("extended_order_id"),
        "package_number": line.get("package_number"),
        "buyer_country": line.get("buyer_country"),
        "line_amount_usd": line_amount,
        "quantity": quantity,
        "shipping_allocated_usd": shipping_alloc,
        "order_total_revenue_usd": order_total_revenue_usd,  # H1 修复用
        "shopify_fee_result": shopify_fee_result,
        "sku_daily_units": sku_units_cache[cache_key],
        "sku_daily_ad_spend_usd": sku_spend_cache[cache_key],
        "product_purchase_price_cny": purchase_price,
        "purchase_price_source": purchase_source,
        "purchase_price_sanity": purchase_sanity,
        "shipping_cost_cny": shipping_cost_cny,
        "shipping_cost_source": shipping_cost_source,
        **(exchange_rate_basis or {}),
    }
    return calculate_line_profit(
        line_input,
        rmb_per_usd=rmb_per_usd,
        return_reserve_rate=return_reserve_rate,
    ), business_date


def backfill(
    date_from: date,
    date_to: date,
    *,
    dry_run: bool = False,
    rmb_per_usd: Decimal | None = None,
    return_reserve_rate: Decimal | None = None,
) -> dict[str, Any]:
    """主回填函数。返回汇总统计。"""
    from appcore.order_analytics.profit_calculation import get_configured_return_reserve_rate
    manual_rate = Decimal(str(rmb_per_usd)) if rmb_per_usd is not None else None
    run_rate = manual_rate if manual_rate is not None else None
    if return_reserve_rate is None:
        return_reserve_rate = get_configured_return_reserve_rate()
    exchange_rate_mode = "manual_override" if manual_rate is not None else "daily_archive"
    log.info("backfill window: %s ~ %s, exchange_rate_mode=%s, dry_run=%s",
             date_from, date_to, exchange_rate_mode, dry_run)

    if not dry_run:
        run_id = start_profit_run(
            task_code="backfill",
            window_start_at=datetime.combine(date_from, datetime.min.time()),
            window_end_at=datetime.combine(date_to, datetime.max.time()),
            rmb_per_usd=float(run_rate) if run_rate is not None else None,
            return_reserve_rate=float(return_reserve_rate),
        )
    else:
        run_id = None

    totals = {
        "lines_total": 0,
        "lines_ok": 0,
        "lines_incomplete": 0,
        "lines_error": 0,
        "legacy_fee_boundary_skipped": 0,
        "shopify_fee_source_counts": {},
    }
    exchange_rate_stats = {
        "mode": exchange_rate_mode,
        "fallback_lines": 0,
        "manual_override": float(manual_rate) if manual_rate is not None else None,
    }
    sku_units_cache: dict = {}
    sku_spend_cache: dict = {}
    fee_source_counts: defaultdict[str, int] = defaultdict(int)
    legacy_skipped_orders: set[str] = set()
    samples: list[dict] = []  # dry_run 用，前 5 行预览

    try:
        for m_start, m_end in _iter_months(date_from, date_to):
            log.info("processing month: %s ~ %s", m_start, m_end)
            lines = query(_LINE_QUERY, (m_start, m_end))
            order_totals = _compute_order_total_line_amount(lines)
            order_shipping = _compute_package_shipping(lines)
            fee_totals = _compute_group_total_line_amount(lines, _resolve_shopify_fee_group_key)
            fee_shipping = _compute_shopify_fee_shipping(lines, order_shipping)
            fee_result_cache: dict[str, dict[str, Any]] = {}
            if manual_rate is None:
                business_dates = [
                    d for d in (_resolve_business_date(line) for line in lines)
                    if d is not None
                ]
                rate_lookup_map = exchange_rates.get_usd_to_cny_map(
                    business_dates,
                )
            else:
                rate_lookup_map = {}
                manual_lookup = exchange_rates.manual_rate_lookup(manual_rate)

            for line in lines:
                package_key = _resolve_package_group_key(line)
                fee_key = _resolve_shopify_fee_group_key(line)
                try:
                    if _should_skip_for_dynamic_fee_boundary(line):
                        if fee_key not in legacy_skipped_orders:
                            legacy_skipped_orders.add(fee_key)
                            totals["legacy_fee_boundary_skipped"] += 1
                        continue

                    if fee_key not in fee_result_cache:
                        fee_result_cache[fee_key] = resolve_shopify_fee_for_order(
                            amount=(
                                fee_totals.get(fee_key, 0)
                                + fee_shipping.get(fee_key, 0)
                            ),
                            buyer_country=line.get("buyer_country"),
                            site_code=line.get("site_code"),
                            order_names=_shopify_order_names(line),
                            order_time=_resolve_order_time(line),
                        )
                        source = fee_result_cache[fee_key].get("shopify_fee_source") or "unknown"
                        fee_source_counts[source] += 1

                    biz_for_rate = _resolve_business_date(line)
                    if manual_rate is not None:
                        rate_lookup = manual_lookup
                    else:
                        rate_lookup = rate_lookup_map.get(
                            biz_for_rate,
                            exchange_rates.configured_fallback_lookup(),
                        )
                    if rate_lookup.source in {"fallback_30d_average", "configured_fallback"}:
                        exchange_rate_stats["fallback_lines"] += 1
                    result, biz_date = _process_line(
                        line,
                        order_total_amount=order_totals.get(package_key, 0),
                        order_shipping=order_shipping.get(package_key, 0),
                        sku_units_cache=sku_units_cache,
                        sku_spend_cache=sku_spend_cache,
                        rmb_per_usd=rate_lookup.rate,
                        return_reserve_rate=return_reserve_rate,
                        exchange_rate_basis=rate_lookup.cost_basis(),
                        shopify_fee_result=fee_result_cache[fee_key],
                        fee_total_revenue_usd=(
                            fee_totals.get(fee_key, 0)
                            + fee_shipping.get(fee_key, 0)
                        ),
                    )
                    totals["lines_total"] += 1
                    if result["status"] == "ok":
                        totals["lines_ok"] += 1
                    elif result["status"] == "incomplete":
                        totals["lines_incomplete"] += 1
                    else:
                        totals["lines_error"] += 1

                    if dry_run and len(samples) < 5:
                        samples.append({
                            "dxm_order_line_id": result.get("dxm_order_line_id"),
                            "status": result["status"],
                            "profit_usd": result.get("profit_usd"),
                            "missing": result.get("missing_fields"),
                        })

                    if not dry_run:
                        upsert_profit_line(
                            result,
                            business_date=biz_date or m_start,
                            paid_at=line.get("order_paid_at") or line.get("paid_at"),
                            source_run_id=run_id,
                        )
                except Exception as exc:
                    log.exception("error processing line %s: %s",
                                  line.get("dxm_order_line_id"), exc)
                    totals["lines_total"] += 1
                    totals["lines_error"] += 1

        # 算窗口内未匹配广告费总和
        unalloc = 0.0
        d = date_from
        while d <= date_to:
            unalloc += get_unallocated_ad_spend(business_date=d)
            d += timedelta(days=1)
        totals["unallocated_ad_spend_usd"] = round(unalloc, 4)
        totals["exchange_rate"] = exchange_rate_stats
        totals["shopify_fee_source_counts"] = dict(fee_source_counts)

        if not dry_run:
            finish_profit_run(
                run_id=run_id,
                status="success",
                lines_total=totals["lines_total"],
                lines_ok=totals["lines_ok"],
                lines_incomplete=totals["lines_incomplete"],
                lines_error=totals["lines_error"],
                unallocated_ad_spend_usd=totals["unallocated_ad_spend_usd"],
                summary=totals,
            )
    except Exception as exc:
        log.exception("backfill failed: %s", exc)
        if not dry_run and run_id:
            finish_profit_run(
                run_id=run_id, status="failed",
                lines_total=totals["lines_total"],
                error_message=str(exc),
                summary=totals,
            )
        raise

    return {"run_id": run_id, "totals": totals, "samples": samples}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Order profit backfill")
    parser.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true",
                        help="不写库，只打印前 5 条预览 + 总数")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    date_from = datetime.strptime(args.date_from, "%Y-%m-%d").date()
    date_to = datetime.strptime(args.date_to, "%Y-%m-%d").date()
    if date_from > date_to:
        parser.error("date_from must be <= date_to")

    result = backfill(date_from, date_to, dry_run=args.dry_run)
    print(f"run_id={result['run_id']}")
    print(f"totals={result['totals']}")
    if args.dry_run:
        print("samples (first 5):")
        for s in result["samples"]:
            print(f"  {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
