from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from appcore.db import execute, query


log = logging.getLogger(__name__)


def _shopify_pricing_modes() -> list[dict[str, Any]]:
    rows = query(
        """
        SELECT product_id, lineitem_price, shipping, COUNT(*) AS freq
        FROM shopify_orders
        WHERE product_id IS NOT NULL
          AND lineitem_quantity = 1
          AND lineitem_price IS NOT NULL
        GROUP BY product_id, lineitem_price, shipping
        """
    )
    by_pid: dict[int, dict[str, Any]] = {}
    for r in rows:
        pid = int(r["product_id"])
        bucket = by_pid.setdefault(pid, {"prices": {}, "shipping": {}, "samples": 0})
        price = r["lineitem_price"]
        shipping = r["shipping"]
        freq = int(r["freq"] or 0)
        if price is not None:
            bucket["prices"][price] = bucket["prices"].get(price, 0) + freq
        if shipping is not None:
            bucket["shipping"][shipping] = bucket["shipping"].get(shipping, 0) + freq
        bucket["samples"] += freq
    out: list[dict[str, Any]] = []
    for pid, bucket in by_pid.items():
        if not bucket["prices"]:
            continue
        price_mode = max(bucket["prices"].items(), key=lambda kv: kv[1])[0]
        shipping_mode = None
        if bucket["shipping"]:
            shipping_mode = max(bucket["shipping"].items(), key=lambda kv: kv[1])[0]
        out.append({
            "product_id": pid,
            "price": price_mode,
            "shipping": shipping_mode,
            "sample_size": bucket["samples"],
        })
    return out


def backfill_shopify_fields(*, force: bool = False, dry_run: bool = False) -> dict[str, int]:
    modes = _shopify_pricing_modes()
    updated = 0
    if dry_run:
        for m in modes:
            log.info("[dry-run] product_id=%s price=%s shipping=%s sample=%s",
                     m["product_id"], m["price"], m["shipping"], m["sample_size"])
        return {"candidates": len(modes), "updated": 0}
    for m in modes:
        if force:
            execute(
                "UPDATE media_products SET standalone_price=%s, standalone_shipping_fee=%s WHERE id=%s",
                (m["price"], m["shipping"], m["product_id"]),
            )
        else:
            execute(
                "UPDATE media_products "
                "SET standalone_price = COALESCE(standalone_price, %s), "
                "    standalone_shipping_fee = COALESCE(standalone_shipping_fee, %s) "
                "WHERE id = %s",
                (m["price"], m["shipping"], m["product_id"]),
            )
        updated += 1
    return {"candidates": len(modes), "updated": updated}


def _dianxiaomi_shop_groups(force: bool) -> tuple[dict[int, str], dict[str, set[int]]]:
    where = (
        "(mp.packet_cost_actual IS NULL OR mp.packet_cost_estimated IS NULL)"
        if not force
        else "1 = 1"
    )
    pids_rows = query(
        f"SELECT id FROM media_products mp WHERE mp.deleted_at IS NULL AND {where}"
    )
    pids = {int(r["id"]) for r in pids_rows}
    if not pids:
        return {}, {}
    placeholders = ",".join(["%s"] * len(pids))
    pairs = query(
        f"SELECT product_id, dxm_shop_id, COUNT(*) AS n "
        f"FROM dianxiaomi_order_lines "
        f"WHERE product_id IN ({placeholders}) AND dxm_shop_id IS NOT NULL "
        f"GROUP BY product_id, dxm_shop_id "
        f"ORDER BY n DESC",
        tuple(pids),
    )
    pid_to_shop: dict[int, str] = {}
    shop_to_pids: dict[str, set[int]] = defaultdict(set)
    for r in pairs:
        pid = int(r["product_id"])
        shop = str(r["dxm_shop_id"])
        shop_to_pids[shop].add(pid)
        if pid not in pid_to_shop:
            pid_to_shop[pid] = shop
    return pid_to_shop, shop_to_pids


def _sku_to_pid_map(pids: set[int]) -> dict[str, int]:
    if not pids:
        return {}
    placeholders = ",".join(["%s"] * len(pids))
    rows = query(
        f"SELECT product_id, product_sku, product_display_sku, COUNT(*) AS n "
        f"FROM dianxiaomi_order_lines "
        f"WHERE product_id IN ({placeholders}) AND (product_sku IS NOT NULL OR product_display_sku IS NOT NULL) "
        f"GROUP BY product_id, product_sku, product_display_sku",
        tuple(pids),
    )
    sku_to_pid: dict[str, int] = {}
    for r in rows:
        pid = int(r["product_id"])
        for key in ("product_sku", "product_display_sku"):
            sku = r.get(key)
            if sku and sku not in sku_to_pid:
                sku_to_pid[str(sku)] = pid
    return sku_to_pid


def _extract_logistic_fees(orders: list[dict[str, Any]], sku_to_pid: dict[str, int]) -> dict[int, list[float]]:
    fees_by_pid: dict[int, list[float]] = defaultdict(list)
    for o in orders:
        raw_fee = o.get("logisticFee")
        if raw_fee in (None, "", "--", "-"):
            continue
        try:
            fee = float(raw_fee)
        except (TypeError, ValueError):
            continue
        for line in (o.get("productList") or []):
            if not isinstance(line, dict):
                continue
            for key in ("productSku", "displaySku", "sku"):
                sku = line.get(key)
                if sku and sku in sku_to_pid:
                    fees_by_pid[sku_to_pid[sku]].append(fee)
                    break
            else:
                continue
            break
    return fees_by_pid


def _write_parcel_costs(median_by_pid: dict[int, float], *, force: bool, dry_run: bool) -> int:
    updated = 0
    for pid, median in median_by_pid.items():
        if dry_run:
            log.info("[dry-run] product_id=%s median=%.2f", pid, median)
            continue
        if force:
            execute(
                "UPDATE media_products SET packet_cost_estimated=%s, packet_cost_actual=%s WHERE id=%s",
                (median, median, pid),
            )
        else:
            execute(
                "UPDATE media_products "
                "SET packet_cost_estimated = COALESCE(packet_cost_estimated, %s), "
                "    packet_cost_actual = COALESCE(packet_cost_actual, %s) "
                "WHERE id = %s",
                (median, median, pid),
            )
        updated += 1
    return updated


def backfill_parcel_costs_via_dxm(
    *,
    force: bool = False,
    dry_run: bool = False,
    days: int = 30,
    settlement_delay_days: int = 2,
    cdp_url: str | None = None,
    now_func: Callable[[], datetime] | None = None,
    page_provider: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    pid_to_shop, shop_to_pids = _dianxiaomi_shop_groups(force=force)
    if not pid_to_shop:
        return {"candidates": 0, "shops": 0, "with_fees": 0, "updated": 0}
    sku_to_pid = _sku_to_pid_map(set(pid_to_shop.keys()))
    now = (now_func or datetime.now)()
    end_time = now - timedelta(days=settlement_delay_days)
    start_time = end_time - timedelta(days=int(days))

    from appcore import parcel_cost_suggest as pcs

    page_cm = page_provider() if page_provider else pcs.open_dxm_page(cdp_url or pcs.DEFAULT_DXM_CDP_URL)
    fees_by_pid: dict[int, list[float]] = defaultdict(list)
    with page_cm as page:
        for shop_id in shop_to_pids:
            log.info("[roas-backfill] shop=%s window=%s~%s",
                     shop_id, start_time.date(), end_time.date())
            orders = pcs.fetch_orders_in_window(
                page,
                shop_id=str(shop_id),
                start_time=start_time,
                end_time=end_time,
            )
            shop_fees = _extract_logistic_fees(orders, sku_to_pid)
            for pid, vals in shop_fees.items():
                fees_by_pid[pid].extend(vals)
    median_by_pid: dict[int, float] = {}
    for pid, vals in fees_by_pid.items():
        if not vals:
            continue
        median_by_pid[pid] = round(statistics.median(sorted(vals)), 2)
    updated = _write_parcel_costs(median_by_pid, force=force, dry_run=dry_run)
    return {
        "candidates": len(pid_to_shop),
        "shops": len(shop_to_pids),
        "with_fees": len(median_by_pid),
        "updated": updated,
        "window_start": start_time.strftime("%Y-%m-%d"),
        "window_end": end_time.strftime("%Y-%m-%d"),
    }
