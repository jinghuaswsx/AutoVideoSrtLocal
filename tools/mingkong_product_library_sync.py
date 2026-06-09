"""Sync DXM02-MK product, SKU, procurement, and combo data into local DB."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import mingkong_product_library as library
from appcore.browser_automation_lock import browser_automation_lock
from appcore.db import execute, query
from tools.dianxiaomi_sku_sync import build_dxm_payload, build_shopify_payload


DXM_BASE_URL = "https://www.dianxiaomi.com"
SHOPIFY_API = "/api/shopifyProduct/pageList.json"
DXM_PRODUCT_API = "/api/dxmCommodityProduct/pageList.json"
PAIR_API = "/api/dxmAlibabaProductPair/alibabaProductPairPageList.json"
CHILD_SKU_API = "/api/dxmCommodityProduct/getChildSkuInfo.json"
DEFAULT_DXM02_CDP_URL = "http://127.0.0.1:9223"
MIGRATION = REPO_ROOT / "db/migrations/2026_06_09_mingkong_product_library.sql"


def _exec_sql_file(path: Path) -> None:
    body = path.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in body.splitlines()
        if not line.strip().startswith("--")
    )
    for statement in body.split(";"):
        sql = statement.strip()
        if not sql:
            continue
        execute(sql)


def _post_form(context, path: str, payload: dict[str, Any], *, timeout_ms: int) -> dict[str, Any]:
    response = context.request.post(
        f"{DXM_BASE_URL}{path}",
        form={key: "" if value is None else str(value) for key, value in payload.items()},
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": DXM_BASE_URL,
        },
        timeout=timeout_ms,
    )
    text = response.text()
    if response.status >= 400:
        raise RuntimeError(f"DXM HTTP {response.status}: {text[:200]}")
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"DXM returned non-JSON: {text[:200]}") from exc
    code = data.get("code")
    if code not in (0, "0", None):
        raise RuntimeError(f"DXM API failed: {data.get('msg') or code}")
    return data


def _page(payload: dict[str, Any]) -> dict[str, Any]:
    return ((payload.get("data") or {}).get("page") or {})


def _fetch_paginated(
    context,
    path: str,
    base_payload: dict[str, Any],
    *,
    page_key: str,
    timeout_ms: int,
    max_pages: int,
) -> list[Any]:
    out: list[Any] = []
    page_no = 1
    total_page = 1
    while True:
        payload = dict(base_payload)
        payload[page_key] = page_no
        data = _post_form(context, path, payload, timeout_ms=timeout_ms)
        page = _page(data)
        out.extend(page.get("list") or [])
        total_page = int(page.get("totalPage") or total_page or 1)
        if page_no >= total_page:
            break
        if max_pages and page_no >= max_pages:
            break
        page_no += 1
    return out


def _local_search_terms(product_code: str) -> list[str]:
    code = library.normalize_product_code(product_code)
    terms: list[str] = [code]
    for row in query(
        """
        SELECT product_code, name, shopify_title, product_link
        FROM media_products
        WHERE product_code IN (%s, %s) OR product_link LIKE %s
        LIMIT 20
        """,
        (code, f"{code}-rjc", f"%/{code}%"),
    ):
        terms.extend([row.get("product_code"), row.get("name"), row.get("shopify_title"), row.get("product_link")])
    for row in query(
        """
        SELECT product_code, product_name, product_url, mk_product_name, mk_product_link, shopify_product_id
        FROM mingkong_material_products
        WHERE product_code=%s OR mk_product_link LIKE %s OR product_url LIKE %s
        ORDER BY id DESC
        LIMIT 20
        """,
        (code, f"%/{code}%", f"%/{code}%"),
    ):
        terms.extend([
            row.get("product_code"),
            row.get("product_name"),
            row.get("product_url"),
            row.get("mk_product_name"),
            row.get("mk_product_link"),
            row.get("shopify_product_id"),
        ])
    for row in query(
        """
        SELECT product_code, product_name, product_url, product_cn_name, product_english_title, product_id
        FROM dianxiaomi_product_assets
        WHERE product_code=%s OR product_url LIKE %s
        ORDER BY id DESC
        LIMIT 20
        """,
        (code, f"%/{code}%"),
    ):
        terms.extend([
            row.get("product_code"),
            row.get("product_name"),
            row.get("product_url"),
            row.get("product_cn_name"),
            row.get("product_english_title"),
            row.get("product_id"),
        ])
    clean: list[str] = []
    seen: set[str] = set()
    for term in terms:
        text = str(term or "").strip()
        if not text:
            continue
        if "/products/" in text:
            text = text.rsplit("/products/", 1)[-1].strip("/")
        if text.endswith("-rjc"):
            text = text[:-4]
        if text not in seen:
            clean.append(text)
            seen.add(text)
    return clean


def fetch_shopify_rows(
    context,
    *,
    product_code: str,
    max_pages: int,
    timeout_ms: int,
    days: int,
) -> list[dict[str, Any]]:
    rows_by_id: dict[str, dict[str, Any]] = {}
    if product_code:
        search_terms = _local_search_terms(product_code)
        for term in search_terms:
            payload = build_shopify_payload(1, searchValue=term)
            for row in _fetch_paginated(
                context,
                SHOPIFY_API,
                payload,
                page_key="pageNo",
                timeout_ms=timeout_ms,
                max_pages=max_pages or 5,
            ):
                product_id = str(row.get("shopifyProductId") or "").strip()
                if product_id:
                    rows_by_id[product_id] = row
        code = library.normalize_product_code(product_code)
        search_term_set = set(search_terms)
        return [
            row for row in rows_by_id.values()
            if library.product_code_from_handle(row.get("handle")) == code
            or str(row.get("title") or "").strip() in search_term_set
            or str(row.get("shopifyProductId") or "").strip() in search_term_set
        ]

    cutoff = (datetime.utcnow() - timedelta(days=days)) if days and days > 0 else None
    payload = build_shopify_payload(1)
    for row in _fetch_paginated(
        context,
        SHOPIFY_API,
        payload,
        page_key="pageNo",
        timeout_ms=timeout_ms,
        max_pages=max_pages,
    ):
        created_text = library.parse_dxm_millis(row.get("shopiyfCreateTime"))
        if cutoff and created_text:
            try:
                created_at = datetime.strptime(created_text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                created_at = None
            if created_at and created_at < cutoff:
                continue
        product_id = str(row.get("shopifyProductId") or "").strip()
        if product_id:
            rows_by_id[product_id] = row
    return list(rows_by_id.values())


def _iter_dxm_items(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for group in groups:
        if isinstance(group, dict):
            out.extend(group.get("dxmCommodityProductList") or [])
    return out


def fetch_erp_index(
    context,
    *,
    skus: set[str],
    max_pages: int,
    timeout_ms: int,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    if skus:
        for sku in sorted(skus):
            groups = _fetch_paginated(
                context,
                DXM_PRODUCT_API,
                build_dxm_payload(1, searchValue=sku, pageSize=20),
                page_key="pageNo",
                timeout_ms=timeout_ms,
                max_pages=1,
            )
            items.extend(_iter_dxm_items(groups))
    else:
        groups = _fetch_paginated(
            context,
            DXM_PRODUCT_API,
            build_dxm_payload(1),
            page_key="pageNo",
            timeout_ms=timeout_ms,
            max_pages=max_pages,
        )
        items.extend(_iter_dxm_items(groups))
    index: dict[str, dict[str, Any]] = {}
    for item in items:
        payload = library.erp_payload_from_dxm_item(item)
        sku = payload.get("dxm_sku")
        if not sku:
            continue
        existing = index.get(sku)
        if existing and existing.get("relation_flag") and not payload.get("relation_flag"):
            continue
        index[sku] = payload
    return index, items


def fetch_pairing_rows(
    context,
    *,
    skus: set[str],
    max_pages: int,
    timeout_ms: int,
) -> list[dict[str, Any]]:
    rows_by_id: dict[str, dict[str, Any]] = {}
    if skus:
        for sku in sorted(skus):
            rows = _fetch_paginated(
                context,
                PAIR_API,
                {
                    "pageNo": 1,
                    "pageSize": 20,
                    "status": "",
                    "searchType": 1,
                    "searchValue": sku,
                    "searchMode": 1,
                },
                page_key="pageNo",
                timeout_ms=timeout_ms,
                max_pages=1,
            )
            for row in rows:
                if str(row.get("sku") or "").strip() == sku and row.get("id"):
                    rows_by_id[str(row["id"])] = row
        return list(rows_by_id.values())

    rows = _fetch_paginated(
        context,
        PAIR_API,
        {
            "pageNo": 1,
            "pageSize": 100,
            "status": "",
            "searchType": 1,
            "searchValue": "",
            "searchMode": 1,
        },
        page_key="pageNo",
        timeout_ms=timeout_ms,
        max_pages=max_pages,
    )
    for row in rows:
        if row.get("id"):
            rows_by_id[str(row["id"])] = row
    return list(rows_by_id.values())


def fetch_child_sku_info(context, product_id: str, *, timeout_ms: int) -> list[dict[str, Any]]:
    data = _post_form(context, CHILD_SKU_API, {"id": product_id}, timeout_ms=timeout_ms)
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    code = nested.get("code")
    if code not in (0, "0", None):
        raise RuntimeError(nested.get("msg") or "getChildSkuInfo failed")
    return nested.get("data") or []


def run_sync(args: argparse.Namespace) -> dict[str, Any]:
    _exec_sql_file(MIGRATION)
    window_start = (
        (datetime.utcnow() - timedelta(days=args.days)).strftime("%Y-%m-%d %H:%M:%S")
        if args.days and args.days > 0
        else None
    )
    run_id = library.start_sync_run(
        window_start=window_start,
        window_end=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )
    summary: dict[str, Any] = {}
    from playwright.sync_api import sync_playwright

    try:
        with browser_automation_lock(
            task_code=library.SYNC_TASK_CODE,
            timeout_seconds=args.lock_timeout,
            command=args.product_code or (f"days={args.days}" if args.days and args.days > 0 else "all"),
        ):
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(args.cdp_url)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                timeout_ms = int(args.timeout_seconds * 1000)
                shopify_rows = fetch_shopify_rows(
                    context,
                    product_code=args.product_code,
                    max_pages=args.max_pages,
                    timeout_ms=timeout_ms,
                    days=args.days,
                )
                variant_payloads = [
                    variant
                    for row in shopify_rows
                    for variant in library.variant_payloads_from_shopify_row(row)
                ]
                pair_keys = {
                    str(variant.get("pair_key") or "").strip()
                    for variant in variant_payloads
                    if str(variant.get("pair_key") or "").strip()
                }
                erp_index, erp_items = fetch_erp_index(
                    context,
                    skus=pair_keys if args.product_code else set(),
                    max_pages=args.max_pages,
                    timeout_ms=timeout_ms,
                )
                upserted_variant_by_sku: dict[str, int] = {}
                combo_jobs: list[tuple[int, str, str]] = []
                for row in shopify_rows:
                    product_id = library.upsert_product(row)
                    for variant in library.variant_payloads_from_shopify_row(row):
                        variant_id = library.upsert_variant(
                            mingkong_product_id=product_id,
                            variant=variant,
                            erp_index=erp_index,
                        )
                        sku = str((erp_index.get(str(variant.get("pair_key") or "")) or {}).get("dxm_sku") or variant.get("pair_key") or "").strip()
                        if sku:
                            upserted_variant_by_sku[sku] = variant_id
                        erp = erp_index.get(str(variant.get("pair_key") or "")) or {}
                        if erp.get("is_combo") and erp.get("dxm_product_id"):
                            combo_jobs.append((variant_id, str(erp["dxm_product_id"]), str(erp.get("dxm_sku") or "")))

                pairing_skus = set(upserted_variant_by_sku)
                for erp in erp_index.values():
                    if erp.get("is_combo"):
                        continue
                    if erp.get("dxm_sku") in upserted_variant_by_sku:
                        pairing_skus.add(str(erp["dxm_sku"]))
                pairing_rows = fetch_pairing_rows(
                    context,
                    skus=pairing_skus if args.product_code else set(),
                    max_pages=args.max_pages,
                    timeout_ms=timeout_ms,
                )
                for row in pairing_rows:
                    library.upsert_procurement_link(row, variant_id_by_sku=upserted_variant_by_sku)

                combo_count = 0
                if args.include_combo_components:
                    for variant_id, product_id, sku in combo_jobs:
                        children = fetch_child_sku_info(context, product_id, timeout_ms=timeout_ms)
                        for child in children:
                            library.upsert_combo_component(
                                child,
                                mingkong_variant_id=variant_id,
                                combo_dxm_product_id=product_id,
                                combo_dxm_sku=sku,
                            )
                            combo_count += 1
                            child_sku = str(child.get("sku") or "").strip()
                            if child_sku:
                                for pair in fetch_pairing_rows(
                                    context,
                                    skus={child_sku},
                                    max_pages=1,
                                    timeout_ms=timeout_ms,
                                ):
                                    library.upsert_procurement_link(pair, variant_id_by_sku={})

                summary = {
                    "products_seen": len(shopify_rows),
                    "variants_seen": len(variant_payloads),
                    "erp_skus_seen": len(erp_items),
                    "procurement_links_seen": len(pairing_rows),
                    "combo_components_seen": combo_count,
                    "product_code": args.product_code,
                }
                library.finish_sync_run(run_id, status="success", summary=summary)
                return summary
    except Exception as exc:
        library.finish_sync_run(run_id, status="failed", summary=summary, error_message=str(exc))
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步明空 DXM02 商品/SKU/采购配对到本地库")
    parser.add_argument("--cdp-url", default=os.getenv("DXM02_MINGKONG_CDP_URL", DEFAULT_DXM02_CDP_URL))
    parser.add_argument("--days", type=int, default=0, help="0 表示全量同步；大于 0 时仅保留该创建时间窗口")
    parser.add_argument("--product-code", default="")
    parser.add_argument("--max-pages", type=int, default=0, help="0 表示不限制页数；单品搜索默认每个搜索词最多 5 页")
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--lock-timeout", type=int, default=21600)
    parser.add_argument("--include-combo-components", action="store_true", default=True)
    args = parser.parse_args(argv)
    summary = run_sync(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
