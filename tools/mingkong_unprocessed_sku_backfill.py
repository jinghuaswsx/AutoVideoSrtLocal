"""Batch backfill Mingkong SKU/procurement data for unprocessed media products."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import dianxiaomi_mingkong_pairing as pairing
from appcore import dianxiaomi_yuncang
from appcore import medias
from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.db import query_one as db_query_one


OUTPUT_DIR = REPO_ROOT / "output" / "mingkong_sku_backfill"
FULL_SYNC_SOURCE = "mingkong_15d_dxm03_full_sync"
FULL_SYNC_OUTPUT_DIR = REPO_ROOT / "output" / "mingkong_recent_15d_full_sync"

_CONFIGURED_SKU_ROW_SQL = (
    "COALESCE(s.manual_override, 0)=1 "
    "OR s.manual_unit_price_rmb IS NOT NULL "
    "OR NULLIF(TRIM(s.manual_goods_name), '') IS NOT NULL "
    "OR (NULLIF(TRIM(s.dianxiaomi_sku), '') IS NOT NULL AND NOT ("
    "COALESCE(s.source, '') IN ('', 'auto', 'dianxiaomi_sku', 'shopify_public', 'shopify_public_base') "
    "AND TRIM(s.dianxiaomi_sku)=TRIM(COALESCE(s.shopify_variant_id, '')) "
    "AND NULLIF(TRIM(COALESCE(s.dianxiaomi_product_sku, '')), '') IS NULL "
    "AND NULLIF(TRIM(COALESCE(s.dianxiaomi_sku_code, '')), '') IS NULL"
    ")) "
    "OR NULLIF(TRIM(s.dianxiaomi_product_sku), '') IS NOT NULL "
    "OR NULLIF(TRIM(s.dianxiaomi_sku_code), '') IS NOT NULL"
)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _sleep_seconds(seconds: float) -> None:
    if seconds and seconds > 0:
        time.sleep(float(seconds))


def _best_value(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def is_configured_local_sku_row(row: dict[str, Any]) -> bool:
    if int(row.get("manual_override") or 0):
        return True
    if row.get("manual_unit_price_rmb") is not None:
        return True
    for key in ("manual_goods_name", "dianxiaomi_product_sku", "dianxiaomi_sku_code"):
        if _clean_text(row.get(key)):
            return True
    sku = _clean_text(row.get("dianxiaomi_sku"))
    if sku and not (
        _clean_text(row.get("source")).lower()
        in {"", "auto", "dianxiaomi_sku", "shopify_public", "shopify_public_base"}
        and sku == _clean_text(row.get("shopify_variant_id"))
    ):
        return True
    return False


def configured_local_sku_row_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows or [] if is_configured_local_sku_row(row))


def _sku_lookup_keys(product: dict[str, Any], sku_rows: list[dict[str, Any]]) -> list[str]:
    keys = set()
    for value in (product.get("shopifyid"),):
        text = _clean_text(value)
        if text:
            keys.add(text)
    for row in sku_rows or []:
        for key in (
            "shopify_variant_id",
            "shopify_sku",
            "dianxiaomi_sku",
            "dianxiaomi_product_sku",
            "dianxiaomi_sku_code",
        ):
            text = _clean_text(row.get(key))
            if text:
                keys.add(text)
    return sorted(keys)


def product_order_summary(
    product: dict[str, Any],
    sku_rows: list[dict[str, Any]],
    *,
    query_one_fn: Callable[..., dict | None] = db_query_one,
) -> dict[str, Any]:
    product_id = int(product["id"])
    product_code = _clean_text(product.get("product_code"))
    shopifyid = _clean_text(product.get("shopifyid"))
    dxm_exact = query_one_fn(
        "SELECT COUNT(*) AS c, MAX(order_created_at) AS latest "
        "FROM dianxiaomi_order_lines "
        "WHERE product_id=%s OR product_code=%s OR shopify_product_id=%s",
        (product_id, product_code, shopifyid),
    ) or {}
    dxm_raw = query_one_fn(
        "SELECT COUNT(*) AS c, MAX(order_created_at) AS latest "
        "FROM dianxiaomi_order_lines "
        "WHERE raw_line_json LIKE %s OR raw_order_json LIKE %s",
        (f"%{product_code}%", f"%{product_code}%"),
    ) or {}
    sku_keys = _sku_lookup_keys(product, sku_rows)
    dxm_sku = {"c": 0, "latest": None}
    shopify_sku = {"c": 0, "latest": None}
    if sku_keys:
        placeholders = ",".join(["%s"] * len(sku_keys))
        dxm_sku = query_one_fn(
            "SELECT COUNT(*) AS c, MAX(order_created_at) AS latest "
            "FROM dianxiaomi_order_lines "
            f"WHERE product_sku IN ({placeholders}) "
            f"OR product_sub_sku IN ({placeholders}) "
            f"OR product_display_sku IN ({placeholders})",
            tuple(sku_keys * 3),
        ) or dxm_sku
        shopify_sku = query_one_fn(
            "SELECT COUNT(*) AS c, MAX(created_at_order) AS latest "
            "FROM shopify_orders "
            f"WHERE lineitem_sku IN ({placeholders})",
            tuple(sku_keys),
        ) or shopify_sku
    shopify_exact = query_one_fn(
        "SELECT COUNT(*) AS c, MAX(created_at_order) AS latest "
        "FROM shopify_orders WHERE product_id=%s",
        (product_id,),
    ) or {}
    counts = {
        "dianxiaomi_exact": int(dxm_exact.get("c") or 0),
        "dianxiaomi_raw": int(dxm_raw.get("c") or 0),
        "dianxiaomi_sku": int(dxm_sku.get("c") or 0),
        "shopify_product_id": int(shopify_exact.get("c") or 0),
        "shopify_sku": int(shopify_sku.get("c") or 0),
    }
    latest_values = [
        row.get("latest")
        for row in (dxm_exact, dxm_raw, dxm_sku, shopify_exact, shopify_sku)
        if row.get("latest") is not None
    ]
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "latest": max(latest_values) if latest_values else None,
        "sku_key_count": len(sku_keys),
    }


def delete_local_product_skus(product_id: int, *, execute_fn: Callable[..., Any] = db_execute) -> int:
    return int(execute_fn("DELETE FROM media_product_skus WHERE product_id=%s", (int(product_id),)) or 0)


def configured_variant_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {
        _clean_text(row.get("shopify_variant_id"))
        for row in rows or []
        if _clean_text(row.get("shopify_variant_id")) and is_configured_local_sku_row(row)
    }


def preserve_configured_pair_fields(
    pairs: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    existing_by_variant = {
        _clean_text(row.get("shopify_variant_id")): row
        for row in existing_rows or []
        if _clean_text(row.get("shopify_variant_id"))
    }
    protected = configured_variant_ids(existing_rows)
    out: list[dict[str, Any]] = []
    for pair in pairs or []:
        variant_id = _clean_text(pair.get("shopify_variant_id"))
        if variant_id not in protected:
            out.append(pair)
            continue
        existing = existing_by_variant.get(variant_id) or {}
        merged = dict(pair)
        for key in (
            "dianxiaomi_sku",
            "dianxiaomi_product_sku",
            "dianxiaomi_sku_code",
            "dianxiaomi_name",
        ):
            if key in existing:
                merged[key] = existing.get(key)
        out.append(merged)
    return out, protected


def _sku_pair_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "shopify_product_id": row.get("shopify_product_id"),
        "shopify_variant_id": row.get("shopify_variant_id"),
        "shopify_sku": row.get("shopify_sku"),
        "shopify_price": row.get("shopify_price"),
        "shopify_compare_at_price": row.get("shopify_compare_at_price"),
        "shopify_currency": row.get("shopify_currency") or "USD",
        "shopify_inventory_quantity": row.get("shopify_inventory_quantity"),
        "shopify_weight_grams": row.get("shopify_weight_grams"),
        "shopify_variant_title": row.get("shopify_variant_title") or row.get("variant_title"),
        "dianxiaomi_sku": row.get("dianxiaomi_sku"),
        "dianxiaomi_product_sku": row.get("dianxiaomi_product_sku"),
        "dianxiaomi_sku_code": row.get("dianxiaomi_sku_code"),
        "dianxiaomi_name": row.get("dianxiaomi_name"),
    }


def protective_replace_product_skus(product_id: int, action_pairs: list[dict[str, Any]], *, source: str) -> dict[str, int]:
    current_rows = medias.list_product_skus(int(product_id))
    merged_by_variant = {
        _clean_text(row.get("shopify_variant_id")): _sku_pair_from_row(row)
        for row in current_rows
        if _clean_text(row.get("shopify_variant_id"))
    }
    for pair in action_pairs or []:
        variant_id = _clean_text(pair.get("shopify_variant_id"))
        if not variant_id:
            continue
        merged = dict(merged_by_variant.get(variant_id) or {})
        merged.update(_sku_pair_from_row(pair))
        merged["shopify_variant_id"] = variant_id
        merged_by_variant[variant_id] = merged
    return medias.replace_product_skus(
        int(product_id),
        list(merged_by_variant.values()),
        source=source,
    )


def _upsert_product_sku_pairs(
    *,
    product_id: int,
    pairs: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
    protected_variant_ids: set[str],
    execute_fn: Callable[..., Any] = db_execute,
    source: str,
) -> dict[str, int]:
    existing_by_variant = {
        _clean_text(row.get("shopify_variant_id")): row
        for row in existing_rows or []
        if _clean_text(row.get("shopify_variant_id"))
    }
    protected = {_clean_text(value) for value in protected_variant_ids or set() if _clean_text(value)}
    updated = 0
    inserted = 0
    skipped_protected = 0
    for raw_pair in pairs or []:
        pair = _sku_pair_from_row(raw_pair or {})
        variant_id = _clean_text(pair.get("shopify_variant_id"))
        if not variant_id:
            continue
        if variant_id in protected:
            skipped_protected += 1
            continue
        values = (
            pair.get("shopify_product_id"),
            pair.get("shopify_sku"),
            pair.get("shopify_price"),
            pair.get("shopify_compare_at_price"),
            pair.get("shopify_currency") or "USD",
            pair.get("shopify_inventory_quantity"),
            pair.get("shopify_weight_grams"),
            pair.get("shopify_variant_title"),
            pair.get("dianxiaomi_sku"),
            pair.get("dianxiaomi_product_sku"),
            pair.get("dianxiaomi_sku_code"),
            pair.get("dianxiaomi_name"),
            source,
        )
        if variant_id in existing_by_variant:
            execute_fn(
                "UPDATE media_product_skus SET "
                "shopify_product_id=%s, shopify_sku=%s, "
                "shopify_price=%s, shopify_compare_at_price=%s, "
                "shopify_currency=%s, shopify_inventory_quantity=%s, "
                "shopify_weight_grams=%s, shopify_variant_title=%s, "
                "dianxiaomi_sku=%s, dianxiaomi_product_sku=%s, dianxiaomi_sku_code=%s, "
                "dianxiaomi_name=%s, source=%s "
                "WHERE product_id=%s AND shopify_variant_id=%s",
                (*values, int(product_id), variant_id),
            )
            updated += 1
            continue
        execute_fn(
            "INSERT INTO media_product_skus "
            "(product_id, shopify_product_id, shopify_variant_id, "
            " shopify_sku, shopify_price, shopify_compare_at_price, "
            " shopify_currency, shopify_inventory_quantity, "
            " shopify_weight_grams, shopify_variant_title, "
            " dianxiaomi_sku, dianxiaomi_product_sku, dianxiaomi_sku_code, dianxiaomi_name, source) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (int(product_id), pair.get("shopify_product_id"), variant_id, *values[1:]),
        )
        inserted += 1
    return {"updated": updated, "inserted": inserted, "skipped_protected": skipped_protected}


def protective_upsert_product_skus(
    *,
    product_id: int,
    pairs: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
    protected_variant_ids: set[str],
    source: str,
    execute_fn: Callable[..., Any] = db_execute,
) -> dict[str, int]:
    return _upsert_product_sku_pairs(
        product_id=product_id,
        pairs=pairs,
        existing_rows=existing_rows,
        protected_variant_ids=protected_variant_ids,
        execute_fn=execute_fn,
        source=source,
    )


def find_unprocessed_products(
    *,
    limit: int = 0,
    include_archived: bool = False,
    listed_only: bool = True,
    product_id: int | None = None,
    product_code: str = "",
    query_fn: Callable[..., list[dict]] = db_query,
) -> list[dict]:
    where = [
        "p.deleted_at IS NULL",
        "NULLIF(TRIM(p.product_code), '') IS NOT NULL",
        "NOT EXISTS ("
        "SELECT 1 FROM media_product_skus s "
        "WHERE s.product_id=p.id "
        f"AND ({_CONFIGURED_SKU_ROW_SQL})"
        ")",
    ]
    args: list[Any] = []
    if product_id is not None:
        where.append("p.id=%s")
        args.append(int(product_id))
    if product_code:
        where.append("p.product_code=%s")
        args.append(product_code)
    if not include_archived:
        where.append("COALESCE(p.archived, 0)=0")
    if listed_only:
        where.append("(p.listing_status IS NULL OR p.listing_status=%s)")
        args.append(medias.LISTING_STATUS_ON)
    sql = (
        "SELECT p.* FROM media_products p "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY p.created_at DESC, p.id DESC"
    )
    if limit and int(limit) > 0:
        sql += " LIMIT %s"
        args.append(int(limit))
    return list(query_fn(sql, tuple(args)) or [])


def list_recent_products_for_full_sync(
    *,
    days: int = 15,
    limit: int = 0,
    include_archived: bool = False,
    listed_only: bool = True,
    query_fn: Callable[..., list[dict]] = db_query,
    now_fn: Callable[[], datetime] = datetime.now,
) -> list[dict]:
    cutoff = now_fn() - timedelta(days=int(days))
    where = [
        "mp.deleted_at IS NULL",
        "mp.created_at >= %s",
        "COALESCE(mp.created_at, '') <> ''",
        "("
        "COALESCE(mp.product_code, '') <> '' "
        "OR COALESCE(mp.product_link, '') <> '' "
        "OR COALESCE(mp.shopifyid, '') <> ''"
        ")",
    ]
    args: list[Any] = [cutoff.strftime("%Y-%m-%d %H:%M:%S")]
    if not include_archived:
        where.append("COALESCE(mp.archived, 0)=0")
    if listed_only:
        where.append("(mp.listing_status IS NULL OR mp.listing_status=%s)")
        args.append(medias.LISTING_STATUS_ON)
    sql = (
        "SELECT mp.* FROM media_products mp "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY mp.created_at DESC, mp.id DESC"
    )
    if limit and int(limit) > 0:
        sql += " LIMIT %s"
        args.append(int(limit))
    return list(query_fn(sql, tuple(args)) or [])


def list_products_by_ids(
    product_ids: list[int],
    *,
    include_archived: bool = False,
    listed_only: bool = True,
    query_fn: Callable[..., list[dict]] = db_query,
) -> list[dict]:
    cleaned = [int(pid) for pid in product_ids if int(pid) > 0]
    if not cleaned:
        return []
    placeholders = ",".join(["%s"] * len(cleaned))
    where = ["p.deleted_at IS NULL", f"p.id IN ({placeholders})"]
    args: list[Any] = list(cleaned)
    if not include_archived:
        where.append("COALESCE(p.archived, 0)=0")
    if listed_only:
        where.append("(p.listing_status IS NULL OR p.listing_status=%s)")
        args.append(medias.LISTING_STATUS_ON)
    rows = query_fn(
        "SELECT p.* FROM media_products p "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY FIELD(p.id, {placeholders})",
        tuple(args + cleaned),
    )
    return list(rows or [])


def build_default_targets(workbench_payload: dict[str, Any]) -> list[dict[str, Any]]:
    product = workbench_payload.get("product") or {}
    targets: list[dict[str, Any]] = []
    for item in workbench_payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        dxm03 = item.get("dxm03") or {}
        commodity = dxm03.get("commodity") or {}
        existing_pair = dxm03.get("pairing") or {}
        mingkong = item.get("mingkong") or {}
        purchase_url = _best_value(
            mingkong.get("purchase_1688_url"),
            item.get("purchase_1688_url"),
        )
        product_id_alibaba = _best_value(
            mingkong.get("alibaba_product_id"),
            existing_pair.get("alibaba_product_id"),
            item.get("alibaba_product_id"),
            pairing.normalize_1688_offer_id(purchase_url),
        )
        targets.append({
            "shopify_product_id": _best_value(
                item.get("shopify_product_id"),
                product.get("shopifyid"),
                mingkong.get("shopify_product_id"),
            ),
            "shopify_variant_id": _best_value(
                item.get("shopify_variant_id"),
                mingkong.get("shopify_variant_id"),
            ),
            "shopify_sku": _best_value(item.get("shopify_sku")),
            "shopify_currency": _best_value(item.get("shopify_currency"), "USD"),
            "variant_title": _best_value(mingkong.get("variant_title"), item.get("variant_title")),
            "dianxiaomi_sku": _best_value(mingkong.get("sku"), item.get("dianxiaomi_sku")),
            "dianxiaomi_product_sku": _best_value(
                mingkong.get("product_sku"),
                item.get("dianxiaomi_product_sku"),
            ),
            "dianxiaomi_sku_code": _best_value(
                mingkong.get("sku_code"),
                item.get("dianxiaomi_sku_code"),
                commodity.get("sku_code"),
            ),
            "dianxiaomi_name": _best_value(
                mingkong.get("name"),
                item.get("dianxiaomi_name"),
                commodity.get("name"),
            ),
            "purchase_1688_url": purchase_url,
            "product_id_alibaba": product_id_alibaba,
            "sku_id_alibaba": _best_value(
                mingkong.get("sku_id_alibaba"),
                existing_pair.get("sku_id_alibaba"),
            ),
            "image_url": _best_value(
                mingkong.get("image_url"),
                item.get("image_url"),
                commodity.get("image_url"),
            ),
        })
    return targets


def _sku_report_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items or []:
        out.append({
            "shopify_variant_id": item.get("shopify_variant_id") or "",
            "variant_title": item.get("variant_title") or item.get("shopify_variant_title") or "",
            "dianxiaomi_sku": item.get("dianxiaomi_sku") or "",
            "dianxiaomi_sku_code": item.get("dianxiaomi_sku_code")
            or item.get("dxm03_sku_code")
            or "",
            "purchase_1688_url": item.get("purchase_1688_url") or "",
            "sku_id_alibaba": item.get("sku_id_alibaba") or "",
            "status": item.get("status") or "",
            "error": item.get("error") or "",
            "message": item.get("message") or "",
        })
    return out


def _empty_product_result(product: dict[str, Any], status: str, message: str) -> dict[str, Any]:
    return {
        "product_id": product.get("id"),
        "product_code": product.get("product_code") or "",
        "name": product.get("name") or "",
        "status": status,
        "message": message,
        "local_sku_count": 0,
        "import": {},
        "replicate": {},
        "confirm": {},
        "yuncang": {},
        "skus": [],
    }


def _summary_int(summary: dict[str, Any], *keys: str) -> int:
    return sum(int(summary.get(key) or 0) for key in keys)


def _empty_full_sync_summary() -> dict[str, int]:
    return {
        "candidate_product_count": 0,
        "scanned_product_count": 0,
        "completed_product_count": 0,
        "already_configured_product_count": 0,
        "partial_product_count": 0,
        "suspended_product_count": 0,
        "failed_product_count": 0,
        "synced_sku_count": 0,
        "protected_sku_count": 0,
        "dxm03_replicated_sku_count": 0,
        "dxm03_existing_sku_count": 0,
        "dxm03_confirmed_sku_count": 0,
        "yuncang_added_sku_count": 0,
        "yuncang_existing_sku_count": 0,
        "purchase_price_updated_product_count": 0,
        "purchase_price_missing_product_count": 0,
        "logistics_packaging_updated_sku_count": 0,
        "logistics_packaging_skipped_sku_count": 0,
    }


def _iter_logistics_packaging_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for source in (
        (result.get("replicate") or {}).get("items") or [],
        result.get("skus") or [],
    ):
        for item in source or []:
            logistics = item.get("logistics_packaging") if isinstance(item, dict) else None
            if isinstance(logistics, dict):
                items.append(logistics)
    return items


def _accumulate_full_sync_result(summary: dict[str, int], result: dict[str, Any]) -> None:
    status = _clean_text(result.get("status"))
    if status == "ok":
        summary["completed_product_count"] += 1
    elif status == "already_configured":
        summary["already_configured_product_count"] += 1
    elif status == "error":
        summary["failed_product_count"] += 1
    elif status.endswith("_blocked") or status.startswith("blocked_") or status.startswith("skipped"):
        summary["suspended_product_count"] += 1
    elif status == "dry_run":
        pass
    else:
        summary["partial_product_count"] += 1

    summary["synced_sku_count"] += int(result.get("new_fillable_sku_count") or 0)
    summary["protected_sku_count"] += int(result.get("protected_local_sku_count") or 0)
    replicate_summary = (result.get("replicate") or {}).get("summary") or {}
    confirm_summary = (result.get("confirm") or {}).get("summary") or {}
    yuncang = result.get("yuncang") or {}
    yuncang_summary = yuncang.get("summary") or {}
    summary["dxm03_replicated_sku_count"] += _summary_int(
        replicate_summary,
        "created_count",
        "replicated_count",
    )
    summary["dxm03_existing_sku_count"] += _summary_int(
        replicate_summary,
        "existing_count",
        "already_exists_count",
    )
    summary["dxm03_confirmed_sku_count"] += _summary_int(confirm_summary, "confirmed_count")
    summary["yuncang_added_sku_count"] += _summary_int(yuncang_summary, "added_count")
    summary["yuncang_existing_sku_count"] += _summary_int(
        yuncang_summary,
        "existing_count",
        "already_exists_count",
    )
    purchase_status = _clean_text(yuncang.get("purchase_price_status"))
    if purchase_status == "updated":
        summary["purchase_price_updated_product_count"] += 1
    elif purchase_status == "purchase_price_missing":
        summary["purchase_price_missing_product_count"] += 1
    for logistics in _iter_logistics_packaging_items(result):
        logistics_status = _clean_text(logistics.get("status"))
        if logistics_status == "updated":
            summary["logistics_packaging_updated_sku_count"] += 1
        elif logistics_status and logistics_status != "already_complete":
            summary["logistics_packaging_skipped_sku_count"] += 1


def run_recent_15d_full_sync_batch(
    *,
    days: int = 15,
    limit: int = 0,
    include_archived: bool = False,
    listed_only: bool = True,
    execute: bool = False,
    force_refresh_mingkong: bool = False,
    overwrite_existing_pairing: bool = False,
    product_delay_seconds: float = 0,
    progress_fn: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    products = list_recent_products_for_full_sync(
        days=days,
        limit=limit,
        include_archived=include_archived,
        listed_only=listed_only,
    )
    summary = _empty_full_sync_summary()
    summary["candidate_product_count"] = len(products)
    product_results: list[dict[str, Any]] = []
    started_at = datetime.now().isoformat(timespec="seconds")
    for index, product in enumerate(products, start=1):
        try:
            result = run_product_sync(
                product,
                execute=execute,
                force_refresh_mingkong=force_refresh_mingkong,
                overwrite_existing_pairing=overwrite_existing_pairing,
                protect_configured_local_skus=True,
            )
        except Exception as exc:  # noqa: BLE001 - full-sync report must continue
            result = _empty_product_result(product, "error", str(exc))
        product_results.append(result)
        summary["scanned_product_count"] += 1
        _accumulate_full_sync_result(summary, result)
        if progress_fn:
            progress_fn({
                "event": "product_done",
                "index": index,
                "total": len(products),
                "result": result,
            })
        if index < len(products):
            _sleep_seconds(product_delay_seconds)
    return {
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "execute" if execute else "plan",
        "criteria": {
            "days": int(days),
            "limit": int(limit or 0),
            "include_archived": bool(include_archived),
            "listed_only": bool(listed_only),
            "force_refresh_mingkong": bool(force_refresh_mingkong),
            "overwrite_existing_pairing": bool(overwrite_existing_pairing),
        },
        "summary": summary,
        "products": product_results,
    }


def write_recent_full_sync_report(
    report: dict[str, Any],
    *,
    output_dir: Path = FULL_SYNC_OUTPUT_DIR,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    mode = _clean_text(report.get("mode")) or "plan"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"mingkong-recent-15d-full-sync-{mode}-{stamp}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def run_product_sync(
    product: dict[str, Any],
    *,
    execute: bool,
    include_live: bool = False,
    force_reset_no_orders: bool = False,
    force_refresh_mingkong: bool = False,
    overwrite_existing_pairing: bool = False,
    protect_configured_local_skus: bool = False,
) -> dict[str, Any]:
    product_id = int(product["id"])
    existing_rows = medias.list_product_skus(product_id)
    order_summary = product_order_summary(product, existing_rows)
    if force_reset_no_orders and int(order_summary.get("total") or 0) > 0:
        result = _empty_product_result(
            product,
            "skipped_has_orders",
            "本地订单系统已同步到该产品订单，强制重置已跳过",
        )
        result["order_summary"] = order_summary
        return result
    configured_count = configured_local_sku_row_count(existing_rows)
    if configured_count and not force_reset_no_orders and not protect_configured_local_skus:
        return _empty_product_result(
            product,
            "skipped_configured_local_skus",
            f"本地已有 {configured_count} 条已配置 SKU 行，批量任务不处理",
        )

    refresh_summary = None
    if force_refresh_mingkong:
        refresh_summary = pairing.mingkong_product_library.refresh_product_from_dxm02(product)

    payload = pairing.build_workbench_payload(
        product,
        [],
        include_live=include_live,
        include_mingkong_reference=True,
    )
    targets = build_default_targets(payload)
    pairs = pairing.build_target_sku_import_pairs(
        product,
        payload.get("items") or [],
        targets,
    )
    protected_variant_ids: set[str] = set()
    if protect_configured_local_skus:
        pairs, protected_variant_ids = preserve_configured_pair_fields(pairs, existing_rows)
    action_variant_ids = {
        _clean_text(pair.get("shopify_variant_id"))
        for pair in pairs
        if _clean_text(pair.get("shopify_variant_id"))
        and _clean_text(pair.get("shopify_variant_id")) not in protected_variant_ids
        and _clean_text(pair.get("dianxiaomi_sku"))
    }
    result: dict[str, Any] = {
        "product_id": product_id,
        "product_code": product.get("product_code") or "",
        "name": product.get("name") or "",
        "status": "dry_run" if not execute else "pending",
        "message": "",
        "local_sku_count": len(pairs),
        "existing_empty_base_count": len(existing_rows),
        "configured_local_sku_count": configured_count,
        "protected_local_sku_count": len(protected_variant_ids),
        "new_fillable_sku_count": len(action_variant_ids),
        "order_summary": order_summary,
        "mingkong_refresh": refresh_summary,
        "workbench_source": (payload.get("summary") or {}).get("source") or "",
        "import": {"stats": {}},
        "replicate": {},
        "confirm": {},
        "skus": _sku_report_items(targets),
    }
    if not pairs:
        result.update({
            "status": "skipped_no_sku_pairs",
            "message": "未生成可写入的 SKU 目标行",
        })
        return result
    if not execute:
        result["message"] = f"dry-run：将写入 {len(pairs)} 条本地 SKU"
        return result

    if force_reset_no_orders:
        result["force_deleted_local_skus"] = delete_local_product_skus(product_id)
    if protect_configured_local_skus:
        stats = protective_upsert_product_skus(
            product_id=product_id,
            pairs=pairs,
            existing_rows=existing_rows,
            protected_variant_ids=protected_variant_ids,
            source=FULL_SYNC_SOURCE,
        )
    else:
        stats = medias.replace_product_skus(product_id, pairs, source="mingkong_batch_sync")
    result["import"] = {"stats": stats}
    if protect_configured_local_skus and not action_variant_ids:
        result.update({
            "status": "ok",
            "message": "保护性同步完成：没有新增可写入 DXM03 的明空 SKU，已有配置已保留",
        })
        return result
    if not any(pair.get("dianxiaomi_sku") for pair in pairs):
        result.update({
            "status": "blocked_no_mingkong_skus",
            "message": "强制刷新后仍未拿到明空店小秘 SKU，只保留全量 Shopify SKU 基底",
        })
        return result
    purchase_url = pairing.first_purchase_url_from_targets(product, payload.get("items") or [], targets)
    if purchase_url:
        medias.update_product(product_id, purchase_1688_url=purchase_url)

    product_after_import = medias.get_product(product_id) or product
    local_rows = medias.list_product_skus(product_id)
    if protect_configured_local_skus:
        local_rows = [
            row for row in local_rows
            if _clean_text(row.get("shopify_variant_id")) in action_variant_ids
        ]
        targets = [
            target for target in targets
            if _clean_text(target.get("shopify_variant_id")) in action_variant_ids
        ]
    replicate_result = pairing.replicate_mingkong_skus_to_dxm03(
        product_after_import,
        local_rows,
        selections=targets,
        replace_product_skus_fn=(
            protective_replace_product_skus
            if protect_configured_local_skus
            else medias.replace_product_skus
        ),
        update_product_fn=medias.update_product,
        force_isolated_thread=False,
    )
    result["replicate"] = {
        "ok": bool(replicate_result.get("ok")),
        "summary": replicate_result.get("summary") or {},
        "message": replicate_result.get("message") or "",
        "items": replicate_result.get("items") or [],
    }
    if not replicate_result.get("ok"):
        result.update({
            "status": "replicate_blocked",
            "message": replicate_result.get("message") or "复刻 DXM03 SKU 未完成",
            "skus": _sku_report_items(replicate_result.get("items") or []),
        })
        return result

    product_after_replicate = medias.get_product(product_id) or product_after_import
    local_rows = medias.list_product_skus(product_id)
    if protect_configured_local_skus:
        local_rows = [
            row for row in local_rows
            if _clean_text(row.get("shopify_variant_id")) in action_variant_ids
        ]
    confirm_result = pairing.confirm_dxm03_pairing(
        product_after_replicate,
        local_rows,
        selections=targets,
        preserve_existing_pairing=not overwrite_existing_pairing,
        force_isolated_thread=False,
    )
    result["confirm"] = {
        "ok": bool(confirm_result.get("ok")),
        "summary": confirm_result.get("summary") or {},
        "message": confirm_result.get("message") or "",
        "items": confirm_result.get("items") or [],
    }
    if not confirm_result.get("ok"):
        result["status"] = "confirm_blocked"
        result["message"] = confirm_result.get("message") or result["status"]
        result["skus"] = _sku_report_items(confirm_result.get("items") or local_rows)
        return result

    yuncang_result = dianxiaomi_yuncang.add_product_skus_to_yuncang(
        product_after_replicate,
        local_rows,
        pairing_items=confirm_result.get("items") or local_rows,
        force_isolated_thread=False,
    )
    result["yuncang"] = {
        "ok": bool(yuncang_result.get("ok")),
        "summary": yuncang_result.get("summary") or {},
        "message": yuncang_result.get("message") or "",
        "purchase_price_status": yuncang_result.get("purchase_price_status") or "",
        "local_refresh": yuncang_result.get("local_refresh") or {},
    }
    result["status"] = "ok" if yuncang_result.get("ok") else "yuncang_blocked"
    result["message"] = (
        f"{confirm_result.get('message') or 'confirmed'}；{yuncang_result.get('message')}"
        if yuncang_result.get("ok")
        else yuncang_result.get("message") or result["status"]
    )
    result["skus"] = _sku_report_items(yuncang_result.get("items") or confirm_result.get("items") or local_rows)
    return result


def run_batch(
    *,
    execute: bool,
    limit: int = 0,
    include_archived: bool = False,
    listed_only: bool = True,
    product_id: int | None = None,
    product_code: str = "",
    product_ids: list[int] | None = None,
    force_reset_no_orders: bool = False,
    force_refresh_mingkong: bool = False,
    overwrite_existing_pairing: bool = False,
    protect_configured_local_skus: bool = False,
    product_delay_seconds: float = 0.0,
    progress_fn: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="seconds")
    if product_ids:
        products = list_products_by_ids(
            product_ids,
            include_archived=include_archived,
            listed_only=listed_only,
        )
    else:
        products = find_unprocessed_products(
            limit=limit,
            include_archived=include_archived,
            listed_only=listed_only,
            product_id=product_id,
            product_code=product_code,
        )
    results: list[dict[str, Any]] = []
    for index, product in enumerate(products):
        try:
            result = run_product_sync(
                product,
                execute=execute,
                force_reset_no_orders=force_reset_no_orders,
                force_refresh_mingkong=force_refresh_mingkong,
                overwrite_existing_pairing=overwrite_existing_pairing,
                protect_configured_local_skus=protect_configured_local_skus,
            )
        except Exception as exc:  # noqa: BLE001 - batch report must continue
            result = _empty_product_result(
                product,
                "error",
                str(exc),
            )
        results.append(result)
        if progress_fn is not None:
            progress_fn(result)
        if index < len(products) - 1:
            _sleep_seconds(product_delay_seconds)
    finished_at = datetime.now().isoformat(timespec="seconds")
    summary = {
        "execute": execute,
        "candidate_count": len(products),
        "processed_count": len(results),
        "ok_count": sum(1 for item in results if item.get("status") == "ok"),
        "dry_run_count": sum(1 for item in results if item.get("status") == "dry_run"),
        "skipped_count": sum(1 for item in results if str(item.get("status") or "").startswith("skipped")),
        "blocked_count": sum(
            1
            for item in results
            if str(item.get("status") or "").endswith("_blocked")
            or str(item.get("status") or "").startswith("blocked_")
        ),
        "error_count": sum(1 for item in results if item.get("status") == "error"),
    }
    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "summary": summary,
        "results": results,
    }


def write_report(report: dict[str, Any], *, output_dir: Path = OUTPUT_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "execute" if (report.get("summary") or {}).get("execute") else "dry-run"
    path = output_dir / f"mingkong-unprocessed-sku-backfill-{suffix}-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill Mingkong SKU data for unprocessed products.")
    parser.add_argument("--execute", action="store_true", help="actually write local DB and DXM03")
    parser.add_argument("--limit", type=int, default=0, help="0 means no limit")
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--include-unlisted", action="store_true")
    parser.add_argument("--product-id", type=int)
    parser.add_argument("--product-ids", default="", help="comma-separated media product ids")
    parser.add_argument("--product-code", default="")
    parser.add_argument("--force-reset-no-orders", action="store_true")
    parser.add_argument("--force-refresh-mingkong", action="store_true")
    parser.add_argument("--overwrite-existing-pairing", action="store_true")
    parser.add_argument("--protect-configured-local-skus", action="store_true")
    parser.add_argument("--product-delay-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)
    product_ids = [
        int(part.strip())
        for part in str(args.product_ids or "").split(",")
        if part.strip().isdigit()
    ]

    def print_progress(item: dict[str, Any]) -> None:
        print(json.dumps({
            "event": "product_done",
            "product_id": item.get("product_id"),
            "product_code": item.get("product_code"),
            "name": item.get("name"),
            "status": item.get("status"),
            "message": item.get("message"),
            "local_sku_count": item.get("local_sku_count"),
        }, ensure_ascii=False), flush=True)

    report = run_batch(
        execute=bool(args.execute),
        limit=int(args.limit or 0),
        include_archived=bool(args.include_archived),
        listed_only=not bool(args.include_unlisted),
        product_id=args.product_id,
        product_code=args.product_code.strip(),
        product_ids=product_ids,
        force_reset_no_orders=bool(args.force_reset_no_orders),
        force_refresh_mingkong=bool(args.force_refresh_mingkong),
        overwrite_existing_pairing=bool(args.overwrite_existing_pairing),
        protect_configured_local_skus=bool(args.protect_configured_local_skus),
        product_delay_seconds=float(args.product_delay_seconds or 0),
        progress_fn=print_progress,
    )
    path = write_report(report)
    print(json.dumps({
        "ok": report["summary"]["error_count"] == 0 and report["summary"]["blocked_count"] == 0,
        "report": str(path),
        "summary": report["summary"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
