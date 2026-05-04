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
from appcore.order_analytics.cost_allocation import (
    allocate_shipping_to_line,
    get_sku_daily_ad_spend,
    get_sku_daily_units,
    get_unallocated_ad_spend,
)
from appcore.order_analytics.cost_completeness import check_sku_cost_completeness
from appcore.order_analytics.profit_calculation import (
    aggregate_order_profit,
    calculate_line_profit,
)
from appcore.order_analytics.profit_repository import (
    finish_profit_run,
    start_profit_run,
    upsert_profit_line,
)
from appcore.product_roas import get_configured_rmb_per_usd

log = logging.getLogger(__name__)


_LINE_QUERY = (
    "SELECT d.id AS dxm_order_line_id, d.product_id, d.quantity, "
    "       d.line_amount, d.order_amount, d.ship_amount, d.buyer_country, "
    "       d.order_paid_at, d.paid_at, d.dxm_package_id, "
    "       m.purchase_price, m.packet_cost_actual, m.packet_cost_estimated "
    "FROM dianxiaomi_order_lines d "
    "LEFT JOIN media_products m ON m.id = d.product_id "
    "WHERE DATE(d.order_paid_at) BETWEEN %s AND %s "
    "ORDER BY d.id"
)


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


def _compute_order_total_line_amount(lines: list[dict]) -> dict[str, float]:
    """按 dxm_package_id 聚合每订单的 line_amount 总和（用于运费按比例摊到行）。"""
    totals: dict[str, float] = defaultdict(float)
    for line in lines:
        pkg = line.get("dxm_package_id") or ""
        amt = line.get("line_amount") or 0
        totals[pkg] += float(amt)
    return totals


def _compute_order_shipping(lines: list[dict]) -> dict[str, float]:
    """按 dxm_package_id 取一次 ship_amount（订单级运费，重复行取首个非 None）。"""
    shipping: dict[str, float] = {}
    for line in lines:
        pkg = line.get("dxm_package_id") or ""
        if pkg in shipping:
            continue
        amt = line.get("ship_amount")
        shipping[pkg] = float(amt) if amt is not None else 0.0
    return shipping


def _process_line(
    line: dict,
    *,
    order_total_amount: float,
    order_shipping: float,
    sku_units_cache: dict,
    sku_spend_cache: dict,
    rmb_per_usd: Decimal,
    return_reserve_rate: Decimal,
) -> dict:
    """单行核算。返回 calculate_line_profit 结果（含 status, profit_usd 等）。"""
    business_date = line["order_paid_at"].date() if line.get("order_paid_at") else None
    product_id = line.get("product_id")

    # 完备性
    completeness = check_sku_cost_completeness({
        "purchase_price": line.get("purchase_price"),
        "packet_cost_actual": line.get("packet_cost_actual"),
        "packet_cost_estimated": line.get("packet_cost_estimated"),
    })

    if not completeness["ok"]:
        return {
            "status": "incomplete",
            "profit_usd": None,
            "missing_fields": completeness["missing"],
            "dxm_order_line_id": line["dxm_order_line_id"],
            "product_id": product_id,
            "buyer_country": line.get("buyer_country"),
        }, business_date

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

    # 运费摊到行
    shipping_alloc = allocate_shipping_to_line(
        line_amount=float(line.get("line_amount") or 0),
        order_total_line_amount=order_total_amount,
        order_shipping_usd=order_shipping,
    )

    line_input = {
        "dxm_order_line_id": line["dxm_order_line_id"],
        "product_id": product_id,
        "buyer_country": line.get("buyer_country"),
        "line_amount_usd": float(line.get("line_amount") or 0),
        "quantity": int(line.get("quantity") or 1),
        "shipping_allocated_usd": shipping_alloc,
        "sku_daily_units": sku_units_cache[cache_key],
        "sku_daily_ad_spend_usd": sku_spend_cache[cache_key],
        "product_purchase_price_cny": completeness["purchase_price"],
        "product_packet_cost_cny": completeness["packet_cost"],
        "packet_cost_basis": completeness["using_packet_cost"],
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
    return_reserve_rate: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    """主回填函数。返回汇总统计。"""
    rmb = rmb_per_usd or get_configured_rmb_per_usd()
    log.info("backfill window: %s ~ %s, rmb_per_usd=%s, dry_run=%s",
             date_from, date_to, rmb, dry_run)

    if not dry_run:
        run_id = start_profit_run(
            task_code="backfill",
            window_start_at=datetime.combine(date_from, datetime.min.time()),
            window_end_at=datetime.combine(date_to, datetime.max.time()),
            rmb_per_usd=float(rmb),
            return_reserve_rate=float(return_reserve_rate),
        )
    else:
        run_id = None

    totals = {"lines_total": 0, "lines_ok": 0, "lines_incomplete": 0, "lines_error": 0}
    sku_units_cache: dict = {}
    sku_spend_cache: dict = {}
    samples: list[dict] = []  # dry_run 用，前 5 行预览

    try:
        for m_start, m_end in _iter_months(date_from, date_to):
            log.info("processing month: %s ~ %s", m_start, m_end)
            lines = query(_LINE_QUERY, (m_start, m_end))
            order_totals = _compute_order_total_line_amount(lines)
            order_shipping = _compute_order_shipping(lines)

            for line in lines:
                pkg = line.get("dxm_package_id") or ""
                try:
                    result, biz_date = _process_line(
                        line,
                        order_total_amount=order_totals.get(pkg, 0),
                        order_shipping=order_shipping.get(pkg, 0),
                        sku_units_cache=sku_units_cache,
                        sku_spend_cache=sku_spend_cache,
                        rmb_per_usd=rmb,
                        return_reserve_rate=return_reserve_rate,
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
