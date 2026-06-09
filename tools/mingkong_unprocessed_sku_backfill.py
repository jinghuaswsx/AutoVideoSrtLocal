"""Batch backfill Mingkong SKU/procurement data for unprocessed media products."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import dianxiaomi_mingkong_pairing as pairing
from appcore import medias
from appcore.db import query as db_query


OUTPUT_DIR = REPO_ROOT / "output" / "mingkong_sku_backfill"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _best_value(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


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
        "s.id IS NULL",
        "NULLIF(TRIM(p.product_code), '') IS NOT NULL",
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
        "LEFT JOIN media_product_skus s ON s.product_id=p.id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY p.created_at DESC, p.id DESC"
    )
    if limit and int(limit) > 0:
        sql += " LIMIT %s"
        args.append(int(limit))
    return list(query_fn(sql, tuple(args)) or [])


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
        "skus": [],
    }


def run_product_sync(
    product: dict[str, Any],
    *,
    execute: bool,
    include_live: bool = False,
) -> dict[str, Any]:
    product_id = int(product["id"])
    existing_rows = medias.list_product_skus(product_id)
    if existing_rows:
        return _empty_product_result(
            product,
            "skipped_existing_local_skus",
            f"本地已有 {len(existing_rows)} 条 SKU 行，批量任务不处理",
        )

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
    result: dict[str, Any] = {
        "product_id": product_id,
        "product_code": product.get("product_code") or "",
        "name": product.get("name") or "",
        "status": "dry_run" if not execute else "pending",
        "message": "",
        "local_sku_count": len(pairs),
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

    stats = medias.replace_product_skus(product_id, pairs, source="mingkong_batch_sync")
    result["import"] = {"stats": stats}
    purchase_url = pairing.first_purchase_url_from_targets(product, payload.get("items") or [], targets)
    if purchase_url:
        medias.update_product(product_id, purchase_1688_url=purchase_url)

    product_after_import = medias.get_product(product_id) or product
    local_rows = medias.list_product_skus(product_id)
    replicate_result = pairing.replicate_mingkong_skus_to_dxm03(
        product_after_import,
        local_rows,
        selections=targets,
        replace_product_skus_fn=medias.replace_product_skus,
        update_product_fn=medias.update_product,
        force_isolated_thread=False,
    )
    result["replicate"] = {
        "ok": bool(replicate_result.get("ok")),
        "summary": replicate_result.get("summary") or {},
        "message": replicate_result.get("message") or "",
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
    confirm_result = pairing.confirm_dxm03_pairing(
        product_after_replicate,
        local_rows,
        selections=targets,
        force_isolated_thread=False,
    )
    result["confirm"] = {
        "ok": bool(confirm_result.get("ok")),
        "summary": confirm_result.get("summary") or {},
        "message": confirm_result.get("message") or "",
    }
    result["status"] = "ok" if confirm_result.get("ok") else "confirm_blocked"
    result["message"] = confirm_result.get("message") or result["status"]
    result["skus"] = _sku_report_items(confirm_result.get("items") or local_rows)
    return result


def run_batch(
    *,
    execute: bool,
    limit: int = 0,
    include_archived: bool = False,
    listed_only: bool = True,
    product_id: int | None = None,
    product_code: str = "",
    progress_fn: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="seconds")
    products = find_unprocessed_products(
        limit=limit,
        include_archived=include_archived,
        listed_only=listed_only,
        product_id=product_id,
        product_code=product_code,
    )
    results: list[dict[str, Any]] = []
    for product in products:
        try:
            result = run_product_sync(product, execute=execute)
        except Exception as exc:  # noqa: BLE001 - batch report must continue
            result = _empty_product_result(
                product,
                "error",
                str(exc),
            )
        results.append(result)
        if progress_fn is not None:
            progress_fn(result)
    finished_at = datetime.now().isoformat(timespec="seconds")
    summary = {
        "execute": execute,
        "candidate_count": len(products),
        "processed_count": len(results),
        "ok_count": sum(1 for item in results if item.get("status") == "ok"),
        "dry_run_count": sum(1 for item in results if item.get("status") == "dry_run"),
        "skipped_count": sum(1 for item in results if str(item.get("status") or "").startswith("skipped")),
        "blocked_count": sum(1 for item in results if str(item.get("status") or "").endswith("_blocked")),
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
    parser.add_argument("--product-code", default="")
    args = parser.parse_args(argv)

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
