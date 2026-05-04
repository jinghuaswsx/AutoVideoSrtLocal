from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable

from appcore.db import execute, query


log = logging.getLogger(__name__)


def shopify_modes_by_sku() -> list[dict[str, Any]]:
    rows = query(
        """
        SELECT lineitem_sku, lineitem_price, shipping, COUNT(*) AS freq
        FROM shopify_orders
        WHERE lineitem_sku IS NOT NULL AND lineitem_sku <> ''
          AND lineitem_quantity = 1
          AND lineitem_price IS NOT NULL
        GROUP BY lineitem_sku, lineitem_price, shipping
        """
    )
    by_sku: dict[str, dict[str, Any]] = {}
    for r in rows:
        sku = str(r["lineitem_sku"])
        bucket = by_sku.setdefault(sku, {"prices": {}, "shipping": {}, "samples": 0})
        price = r["lineitem_price"]
        shipping = r["shipping"]
        freq = int(r["freq"] or 0)
        if price is not None:
            bucket["prices"][price] = bucket["prices"].get(price, 0) + freq
        if shipping is not None:
            bucket["shipping"][shipping] = bucket["shipping"].get(shipping, 0) + freq
        bucket["samples"] += freq
    out: list[dict[str, Any]] = []
    for sku, bucket in by_sku.items():
        if not bucket["prices"]:
            continue
        out.append({
            "sku": sku,
            "price": max(bucket["prices"].items(), key=lambda kv: kv[1])[0],
            "shipping": max(bucket["shipping"].items(), key=lambda kv: kv[1])[0]
                if bucket["shipping"] else None,
            "sample_size": bucket["samples"],
        })
    return out


def order_counts_by_sku() -> dict[str, int]:
    rows = query(
        """
        SELECT product_display_sku AS sku, COUNT(*) AS n
        FROM dianxiaomi_order_lines
        WHERE product_display_sku IS NOT NULL
        GROUP BY product_display_sku
        """
    )
    return {str(r["sku"]): int(r["n"] or 0) for r in rows}


def update_xmyc_sku_shopify_aggregates(*, force: bool = False, dry_run: bool = False) -> dict[str, int]:
    modes = shopify_modes_by_sku()
    sku_to_xmyc = {
        r["sku"]: r["sku"]
        for r in query("SELECT sku FROM xmyc_storage_skus")
    }
    matched = [m for m in modes if m["sku"] in sku_to_xmyc]
    updated = 0
    if dry_run:
        for m in matched:
            log.info("[dry-run] sku=%s price=%s shipping=%s sample=%s",
                     m["sku"], m["price"], m["shipping"], m["sample_size"])
        return {"shopify_modes": len(modes), "xmyc_matched": len(matched), "updated": 0}
    for m in matched:
        if force:
            execute(
                "UPDATE xmyc_storage_skus "
                "SET standalone_price_sku=%s, standalone_shipping_fee_sku=%s "
                "WHERE sku=%s",
                (m["price"], m["shipping"], m["sku"]),
            )
        else:
            execute(
                "UPDATE xmyc_storage_skus "
                "SET standalone_price_sku = COALESCE(standalone_price_sku, %s), "
                "    standalone_shipping_fee_sku = COALESCE(standalone_shipping_fee_sku, %s) "
                "WHERE sku=%s",
                (m["price"], m["shipping"], m["sku"]),
            )
        updated += 1
    return {"shopify_modes": len(modes), "xmyc_matched": len(matched), "updated": updated}


def update_xmyc_sku_order_counts() -> int:
    counts = order_counts_by_sku()
    if not counts:
        return 0
    rows = query("SELECT sku FROM xmyc_storage_skus")
    updated = 0
    for r in rows:
        sku = r["sku"]
        if sku in counts:
            execute(
                "UPDATE xmyc_storage_skus SET sku_orders_count=%s WHERE sku=%s",
                (counts[sku], sku),
            )
            updated += 1
        else:
            execute(
                "UPDATE xmyc_storage_skus SET sku_orders_count=0 WHERE sku=%s",
                (sku,),
            )
    return updated


def compute_sku_roas(sku_row: dict[str, Any], rmb_per_usd: Any = None) -> dict[str, Any]:
    from appcore import product_roas
    if rmb_per_usd is None:
        rmb_per_usd = product_roas.DEFAULT_RMB_PER_USD
    packet = sku_row.get("packet_cost_actual_sku")
    result = product_roas.calculate_break_even_roas(
        purchase_price=sku_row.get("unit_price"),
        estimated_packet_cost=packet,
        actual_packet_cost=packet,
        standalone_price=sku_row.get("standalone_price_sku"),
        standalone_shipping_fee=sku_row.get("standalone_shipping_fee_sku"),
        rmb_per_usd=rmb_per_usd,
    )
    fields = (
        sku_row.get("unit_price"),
        packet,
        sku_row.get("standalone_price_sku"),
    )
    can_compute = all(f is not None for f in fields)
    return {
        "can_compute": can_compute,
        "effective_roas": result.get("effective_roas"),
        "estimated_roas": result.get("estimated_roas"),
        "actual_roas": result.get("actual_roas"),
        "rmb_per_usd": result.get("rmb_per_usd"),
    }


def enrich_skus_with_roas(rows: list[dict[str, Any]], rmb_per_usd: Any = None) -> list[dict[str, Any]]:
    return [{**r, "roas": compute_sku_roas(r, rmb_per_usd)} for r in rows]


def _xmyc_skus_with_shop() -> tuple[dict[str, str], dict[str, set[str]]]:
    rows = query(
        """
        SELECT s.sku, d.dxm_shop_id, COUNT(*) AS n
        FROM xmyc_storage_skus s
        JOIN dianxiaomi_order_lines d ON d.product_display_sku = s.sku
        WHERE d.dxm_shop_id IS NOT NULL
        GROUP BY s.sku, d.dxm_shop_id
        ORDER BY n DESC
        """
    )
    sku_to_shop: dict[str, str] = {}
    shop_to_skus: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        sku = str(r["sku"])
        shop = str(r["dxm_shop_id"])
        shop_to_skus[shop].add(sku)
        if sku not in sku_to_shop:
            sku_to_shop[sku] = shop
    return sku_to_shop, shop_to_skus


def _query_logistic_fees_by_sku(
    skus: set[str],
    start_time: datetime,
    end_time: datetime,
) -> dict[str, list[float]]:
    """从本地 dianxiaomi_order_lines 直接聚合 logistic_fee，不再走 CDP。"""
    if not skus:
        return {}
    placeholders = ",".join(["%s"] * len(skus))
    rows = query(
        f"SELECT product_display_sku, logistic_fee "
        f"FROM dianxiaomi_order_lines "
        f"WHERE product_display_sku IN ({placeholders}) "
        f"  AND logistic_fee IS NOT NULL AND logistic_fee > 0 "
        f"  AND paid_at >= %s AND paid_at <= %s",
        tuple(skus) + (start_time, end_time),
    )
    fees: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        fees[str(r["product_display_sku"])].append(float(r["logistic_fee"]))
    return fees


def update_xmyc_sku_parcel_costs(
    *,
    force: bool = False,
    dry_run: bool = False,
    days: int = 30,
    settlement_delay_days: int = 2,
    now_func: Callable[[], datetime] | None = None,
    **__kwargs,  # 兼容旧 cdp_url / page_provider 参数（已废弃）
) -> dict[str, Any]:
    sku_to_shop, shop_to_skus = _xmyc_skus_with_shop()
    if not sku_to_shop:
        return {"candidates": 0, "shops": 0, "with_fees": 0, "updated": 0}
    now = (now_func or datetime.now)()
    end_time = now - timedelta(days=settlement_delay_days)
    start_time = end_time - timedelta(days=int(days))

    all_skus = {sku for skus in shop_to_skus.values() for sku in skus}
    fees_by_sku = _query_logistic_fees_by_sku(all_skus, start_time, end_time)

    median_by_sku: dict[str, float] = {
        sku: round(statistics.median(sorted(vals)), 2)
        for sku, vals in fees_by_sku.items()
        if vals
    }
    updated = 0
    for sku, median in median_by_sku.items():
        if dry_run:
            log.info("[dry-run] sku=%s median=%.2f", sku, median)
            continue
        if force:
            execute(
                "UPDATE xmyc_storage_skus SET packet_cost_actual_sku=%s WHERE sku=%s",
                (median, sku),
            )
        else:
            execute(
                "UPDATE xmyc_storage_skus "
                "SET packet_cost_actual_sku = COALESCE(packet_cost_actual_sku, %s) "
                "WHERE sku=%s",
                (median, sku),
            )
        updated += 1
    return {
        "candidates": len(sku_to_shop),
        "shops": len(shop_to_skus),
        "with_fees": len(median_by_sku),
        "updated": updated,
        "window_start": start_time.strftime("%Y-%m-%d"),
        "window_end": end_time.strftime("%Y-%m-%d"),
    }
