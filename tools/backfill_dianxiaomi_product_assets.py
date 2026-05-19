"""Backfill product-page assets for historical Dianxiaomi ranking rows.

Docs-anchor: docs/superpowers/specs/2026-05-19-mingkong-product-library-assets-design.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore.db import execute, query
from tools.dianxiaomi_listing_ranking_sync import (
    enrich_listing_rows,
    guard_against_windows_local_mysql,
    product_code_from_url,
)


OUTPUT_DIR = REPO_ROOT / "output" / "dianxiaomi_product_asset_backfill"


def _missing_assets_predicate() -> str:
    return """
    (
      product_assets_synced_at IS NULL
      OR ((product_code IS NULL OR product_code = '') AND product_url IS NOT NULL AND product_url <> '')
      OR product_main_image_url IS NULL
      OR product_main_image_url = ''
      OR product_main_image_object_key IS NULL
      OR product_main_image_object_key = ''
    )
    """


def select_candidate_products(
    *,
    limit: int,
    query_fn: Callable[[str, tuple], list[dict]] = query,
    force: bool = False,
    snapshot_date_from: str = "",
    snapshot_date_to: str = "",
    shard_index: int = 0,
    shard_count: int = 1,
) -> list[dict[str, Any]]:
    where_parts = ["(product_url IS NOT NULL AND product_url <> '' OR product_id IS NOT NULL AND product_id <> '')"]
    args: list[Any] = []
    if not force:
        where_parts.append(_missing_assets_predicate())
    if snapshot_date_from:
        where_parts.append("snapshot_date >= %s")
        args.append(snapshot_date_from)
    if snapshot_date_to:
        where_parts.append("snapshot_date <= %s")
        args.append(snapshot_date_to)
    if int(shard_count) > 1:
        where_parts.append(
            "MOD(CRC32(COALESCE(NULLIF(product_url, ''), CONCAT('product_id:', product_id))), %s) = %s"
        )
        args.extend([int(shard_count), int(shard_index)])
    where_sql = " AND ".join(f"({part})" for part in where_parts)
    sql = f"""
    SELECT
      MAX(product_id) AS product_id,
      MAX(product_name) AS product_name,
      MAX(COALESCE(NULLIF(product_url, ''), '')) AS product_url,
      COUNT(*) AS ranking_rows,
      MAX(snapshot_date) AS latest_snapshot_date
    FROM dianxiaomi_rankings
    WHERE {where_sql}
    GROUP BY COALESCE(NULLIF(product_url, ''), CONCAT('product_id:', product_id))
    ORDER BY MAX(snapshot_date) DESC, COUNT(*) DESC
    LIMIT %s
    """
    args.append(int(limit))
    rows = query_fn(sql, tuple(args))
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        product_url = str(item.get("product_url") or "").strip()
        item["product_code"] = product_code_from_url(product_url)
        item["ranking_rows"] = int(item.get("ranking_rows") or 0)
        out.append(item)
    return out


def _asset_value(row: dict[str, Any], key: str) -> Any:
    value = row.get(key)
    if value == "":
        return None
    return value


def update_backfilled_product(
    product: dict[str, Any],
    enriched: dict[str, Any],
    *,
    execute_fn: Callable[[str, tuple], int] = execute,
) -> int:
    args = (
        _asset_value(enriched, "product_code"),
        _asset_value(enriched, "product_main_image_url"),
        _asset_value(enriched, "product_main_image_object_key"),
        _asset_value(enriched, "product_detail_images_json"),
        _asset_value(enriched, "product_assets_error"),
        _asset_value(enriched, "product_cn_name"),
        _asset_value(enriched, "mk_first_material_name"),
        _asset_value(enriched, "mk_first_material_path"),
        _asset_value(enriched, "mk_first_material_url"),
        _asset_value(enriched, "mk_material_error"),
    )
    product_url = str(product.get("product_url") or "").strip()
    product_id = str(product.get("product_id") or "").strip()
    if product_url:
        return execute_fn(
            """
            UPDATE dianxiaomi_rankings
            SET product_code=%s,
                product_main_image_url=%s,
                product_main_image_object_key=%s,
                product_detail_images_json=%s,
                product_assets_error=%s,
                product_cn_name=%s,
                mk_first_material_name=%s,
                mk_first_material_path=%s,
                mk_first_material_url=%s,
                mk_material_error=%s,
                product_assets_synced_at=NOW()
            WHERE product_url = %s
            """,
            args + (product_url,),
        )
    if product_id:
        return execute_fn(
            """
            UPDATE dianxiaomi_rankings
            SET product_code=%s,
                product_main_image_url=%s,
                product_main_image_object_key=%s,
                product_detail_images_json=%s,
                product_assets_error=%s,
                product_cn_name=%s,
                mk_first_material_name=%s,
                mk_first_material_path=%s,
                mk_first_material_url=%s,
                mk_material_error=%s,
                product_assets_synced_at=NOW()
            WHERE product_id = %s AND (product_url IS NULL OR product_url = '')
            """,
            args + (product_id,),
        )
    return 0


def _build_enrichment_row(product: dict[str, Any]) -> dict[str, Any]:
    product_url = str(product.get("product_url") or "").strip()
    product_code = str(product.get("product_code") or product_code_from_url(product_url)).strip().lower()
    item = {
        "product_id": str(product.get("product_id") or "").strip(),
        "product_name": str(product.get("product_name") or "").strip(),
        "product_url": product_url,
        "product_code": product_code,
    }
    if not product_url:
        item["product_assets_error"] = "missing product_url"
    return item


def _product_key(product: dict[str, Any]) -> str:
    product_url = str(product.get("product_url") or "").strip()
    if product_url:
        return f"url:{product_url}"
    product_id = str(product.get("product_id") or "").strip()
    if product_id:
        return f"id:{product_id}"
    return ""


def _remaining_limit(limit: int, seen: int, batch_size: int) -> int:
    if limit <= 0:
        return batch_size
    return max(0, min(batch_size, limit - seen))


def _write_report(summary: dict[str, Any]) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUTPUT_DIR / f"dianxiaomi-product-asset-backfill-{stamp}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path)


def run_backfill(
    *,
    limit: int = 0,
    batch_size: int = 50,
    dry_run: bool = False,
    force: bool = False,
    timeout_seconds: int = 20,
    sleep_seconds: float = 0.0,
    snapshot_date_from: str = "",
    snapshot_date_to: str = "",
    shard_index: int = 0,
    shard_count: int = 1,
    select_candidates_fn: Callable[..., list[dict[str, Any]]] = select_candidate_products,
    enrich_rows_fn: Callable[..., list[dict[str, Any]]] = enrich_listing_rows,
    update_product_fn: Callable[[dict[str, Any], dict[str, Any]], int] = update_backfilled_product,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "force": bool(force),
        "limit": int(limit),
        "batch_size": int(batch_size),
        "snapshot_date_from": snapshot_date_from,
        "snapshot_date_to": snapshot_date_to,
        "shard_index": int(shard_index),
        "shard_count": int(shard_count),
        "products_seen": 0,
        "products_updated": 0,
        "products_failed": 0,
        "ranking_rows_matched": 0,
        "ranking_rows_updated": 0,
        "missing_url_products": 0,
        "samples": [],
    }
    seen_keys: set[str] = set()

    while True:
        next_limit = _remaining_limit(int(limit), int(summary["products_seen"]), int(batch_size))
        if next_limit <= 0:
            break
        products = select_candidates_fn(
            limit=next_limit,
            force=force,
            snapshot_date_from=snapshot_date_from,
            snapshot_date_to=snapshot_date_to,
            shard_index=int(shard_index),
            shard_count=int(shard_count),
        )
        products = [product for product in products if _product_key(product) not in seen_keys]
        if not products:
            break
        for product in products:
            key = _product_key(product)
            if key:
                seen_keys.add(key)
            summary["products_seen"] += 1
            summary["ranking_rows_matched"] += int(product.get("ranking_rows") or 0)
            if not str(product.get("product_url") or "").strip():
                summary["missing_url_products"] += 1
            if len(summary["samples"]) < 10:
                summary["samples"].append(
                    {
                        "product_id": product.get("product_id"),
                        "product_code": product.get("product_code"),
                        "product_url": product.get("product_url"),
                        "ranking_rows": product.get("ranking_rows"),
                    }
                )
            if dry_run:
                continue
            try:
                row = _build_enrichment_row(product)
                enriched = enrich_rows_fn([row], timeout_seconds=timeout_seconds)[0]
                if not row.get("product_url") and not enriched.get("product_assets_error"):
                    enriched["product_assets_error"] = "missing product_url"
                changed = update_product_fn(product, enriched)
                summary["products_updated"] += 1
                summary["ranking_rows_updated"] += int(changed or 0)
            except Exception as exc:
                summary["products_failed"] += 1
                product["backfill_error"] = str(exc)[:1000]
                try:
                    failed_row = _build_enrichment_row(product)
                    failed_row["product_assets_error"] = product["backfill_error"]
                    changed = update_product_fn(product, failed_row)
                    summary["ranking_rows_updated"] += int(changed or 0)
                except Exception:
                    pass
            if sleep_seconds > 0:
                time.sleep(float(sleep_seconds))
    summary["report_path"] = _write_report(summary)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill Dianxiaomi ranking product image and Mingkong name fields.")
    parser.add_argument("--limit", type=int, default=0, help="Unique products to process. 0 means all currently pending.")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Reprocess candidates even when product_assets_synced_at is set.")
    parser.add_argument("--snapshot-date-from", default="")
    parser.add_argument("--snapshot-date-to", default="")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    guard_against_windows_local_mysql()
    summary = run_backfill(
        limit=args.limit,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        force=args.force,
        timeout_seconds=args.timeout_seconds,
        sleep_seconds=args.sleep_seconds,
        snapshot_date_from=args.snapshot_date_from,
        snapshot_date_to=args.snapshot_date_to,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 1 if summary.get("products_failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
