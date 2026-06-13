from __future__ import annotations

import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable
from urllib.parse import urlparse

from appcore.db import execute, query


log = logging.getLogger(__name__)

PRICE_MIN_USD = Decimal("10.00")
PRICE_MAX_USD = Decimal("50.00")
SHIPPING_FEE_USD = Decimal("7.00")


QueryFn = Callable[[str, tuple], list[dict[str, Any]]]
ExecuteFn = Callable[[str, tuple], int]
ResolveUrlRowsFn = Callable[[dict[str, Any], str], list[dict[str, str]]]
FetchSkuRowsFn = Callable[..., list[dict[str, Any]]]
SleepFn = Callable[[float], None]


def money_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _is_product_url(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        return False
    path_parts = [part for part in parsed.path.split("/") if part]
    return "products" in path_parts


def _canonical_product_url(value: Any) -> str:
    from appcore import product_link_domains

    text = str(value or "").strip()
    if not _is_product_url(text):
        return ""
    return product_link_domains.canonical_product_page_url(text)


def candidate_english_urls(
    product: dict[str, Any],
    *,
    resolve_product_page_url_rows_fn: ResolveUrlRowsFn | None = None,
) -> list[dict[str, str]]:
    """Return English product URLs in the order used for price discovery."""

    from appcore import product_link_domains

    resolve_product_page_url_rows_fn = (
        resolve_product_page_url_rows_fn or product_link_domains.resolve_product_page_url_rows
    )
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def add(source: str, raw_url: Any) -> None:
        url = _canonical_product_url(raw_url)
        if not url or url in seen:
            return
        seen.add(url)
        out.append({"source": source, "url": url})

    add("product_link", product.get("product_link"))
    try:
        for row in resolve_product_page_url_rows_fn(product, "en") or []:
            add(f"resolved_en:{row.get('domain') or ''}", row.get("url"))
    except Exception:
        log.debug("failed to resolve English product URL rows", exc_info=True)
    return out


def _fetch_rows_with_retries(
    product: dict[str, Any],
    url: str,
    *,
    fetch_sku_rows_fn: FetchSkuRowsFn,
    timeout_seconds: int,
    retries: int,
    sleep_fn: SleepFn,
) -> tuple[list[dict[str, Any]], str]:
    last_error = ""
    attempts = max(1, int(retries) + 1)
    for attempt in range(attempts):
        try:
            rows = fetch_sku_rows_fn(
                {**product, "product_link": url},
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            rows = []
        if rows:
            return rows, ""
        if attempt + 1 < attempts:
            sleep_fn(min(0.25 * (attempt + 1), 1.0))
    return [], last_error


def inspect_product_price(
    product: dict[str, Any],
    *,
    price_min: Decimal = PRICE_MIN_USD,
    price_max: Decimal = PRICE_MAX_USD,
    timeout_seconds: int = 8,
    retries: int = 1,
    resolve_product_page_url_rows_fn: ResolveUrlRowsFn | None = None,
    fetch_sku_rows_fn: FetchSkuRowsFn | None = None,
    sleep_fn: SleepFn = time.sleep,
) -> dict[str, Any]:
    """Inspect one product and return a write-ready or skip decision."""

    from appcore.mingkong_product_library import public_shopify_sku_rows_from_product

    fetch_sku_rows_fn = fetch_sku_rows_fn or public_shopify_sku_rows_from_product
    urls = candidate_english_urls(
        product,
        resolve_product_page_url_rows_fn=resolve_product_page_url_rows_fn,
    )
    base = {
        "product_id": int(product.get("id") or 0),
        "product_code": product.get("product_code") or "",
        "status": "",
        "price": None,
        "shipping_fee": str(SHIPPING_FEE_USD),
        "url": "",
        "url_source": "",
        "variant_id": "",
        "sku": "",
        "message": "",
    }
    if not urls:
        return {**base, "status": "missing_url"}

    saw_empty_response = False
    last_error = ""
    for item in urls:
        url = item["url"]
        rows, error = _fetch_rows_with_retries(
            product,
            url,
            fetch_sku_rows_fn=fetch_sku_rows_fn,
            timeout_seconds=timeout_seconds,
            retries=retries,
            sleep_fn=sleep_fn,
        )
        if error:
            last_error = error
        if not rows:
            if not error:
                saw_empty_response = True
            continue
        first = rows[0] or {}
        price = money_decimal(first.get("shopify_price"))
        result = {
            **base,
            "url": url,
            "url_source": item["source"],
            "variant_id": str(first.get("shopify_variant_id") or ""),
            "sku": str(first.get("shopify_sku") or ""),
        }
        if price is None:
            return {**result, "status": "missing_price"}
        result["price"] = str(price)
        if price < price_min or price > price_max:
            return {
                **result,
                "status": "out_of_range",
                "message": f"price {price} outside {price_min}-{price_max}",
            }
        return {**result, "status": "ok"}

    if last_error and not saw_empty_response:
        return {**base, "status": "fetch_error", "message": last_error}
    if last_error:
        return {**base, "status": "no_variants", "message": last_error}
    return {**base, "status": "no_variants"}


def select_candidate_products(
    *,
    force: bool = False,
    limit: int | None = None,
    offset: int = 0,
    product_id: int | None = None,
    query_fn: QueryFn = query,
) -> list[dict[str, Any]]:
    where = ["deleted_at IS NULL"]
    args: list[Any] = []
    if product_id is not None:
        where.append("id = %s")
        args.append(int(product_id))
    elif not force:
        where.append(
            "(standalone_price IS NULL OR tk_sale_price IS NULL OR standalone_shipping_fee IS NULL)"
        )
    sql = (
        "SELECT id, product_code, name, product_link, localized_links_json, "
        "standalone_price, tk_sale_price, standalone_shipping_fee "
        "FROM media_products WHERE "
        + " AND ".join(where)
        + " ORDER BY id ASC"
    )
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        args.extend([int(limit), int(offset)])
    elif offset:
        sql += " LIMIT 18446744073709551615 OFFSET %s"
        args.append(int(offset))
    return query_fn(sql, tuple(args)) or []


def fields_needing_fill(product: dict[str, Any], *, force: bool = False) -> list[str]:
    fields = ("standalone_price", "tk_sale_price", "standalone_shipping_fee")
    if force:
        return list(fields)
    return [field for field in fields if product.get(field) is None]


def write_price_shipping_fields(
    product_id: int,
    price: Decimal,
    *,
    shipping_fee: Decimal = SHIPPING_FEE_USD,
    force: bool = False,
    execute_fn: ExecuteFn = execute,
) -> int:
    if force:
        return execute_fn(
            "UPDATE media_products "
            "SET standalone_price=%s, tk_sale_price=%s, standalone_shipping_fee=%s "
            "WHERE id=%s",
            (price, price, shipping_fee, int(product_id)),
        )
    return execute_fn(
        "UPDATE media_products "
        "SET standalone_price = COALESCE(standalone_price, %s), "
        "    tk_sale_price = COALESCE(tk_sale_price, %s), "
        "    standalone_shipping_fee = COALESCE(standalone_shipping_fee, %s) "
        "WHERE id=%s",
        (price, price, shipping_fee, int(product_id)),
    )


def _inspect_many(
    products: list[dict[str, Any]],
    *,
    max_workers: int,
    timeout_seconds: int,
    retries: int,
    resolve_product_page_url_rows_fn: ResolveUrlRowsFn | None,
    fetch_sku_rows_fn: FetchSkuRowsFn | None,
    sleep_fn: SleepFn,
) -> list[dict[str, Any]]:
    if max_workers <= 1:
        return [
            inspect_product_price(
                product,
                timeout_seconds=timeout_seconds,
                retries=retries,
                resolve_product_page_url_rows_fn=resolve_product_page_url_rows_fn,
                fetch_sku_rows_fn=fetch_sku_rows_fn,
                sleep_fn=sleep_fn,
            )
            for product in products
        ]

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_index = {
            pool.submit(
                inspect_product_price,
                product,
                timeout_seconds=timeout_seconds,
                retries=retries,
                resolve_product_page_url_rows_fn=resolve_product_page_url_rows_fn,
                fetch_sku_rows_fn=fetch_sku_rows_fn,
                sleep_fn=sleep_fn,
            ): index
            for index, product in enumerate(products)
        }
        indexed: list[tuple[int, dict[str, Any]]] = []
        for future in as_completed(future_to_index):
            indexed.append((future_to_index[future], future.result()))
    results = [result for _, result in sorted(indexed, key=lambda item: item[0])]
    return results


def fill_product_price_shipping(
    *,
    force: bool = False,
    dry_run: bool = True,
    limit: int | None = None,
    offset: int = 0,
    product_id: int | None = None,
    max_workers: int = 2,
    timeout_seconds: int = 8,
    retries: int = 1,
    sample_limit: int = 20,
    query_fn: QueryFn = query,
    execute_fn: ExecuteFn = execute,
    resolve_product_page_url_rows_fn: ResolveUrlRowsFn | None = None,
    fetch_sku_rows_fn: FetchSkuRowsFn | None = None,
    sleep_fn: SleepFn = time.sleep,
) -> dict[str, Any]:
    products = select_candidate_products(
        force=force,
        limit=limit,
        offset=offset,
        product_id=product_id,
        query_fn=query_fn,
    )
    by_id = {int(product.get("id") or 0): product for product in products}
    results = _inspect_many(
        products,
        max_workers=max(1, int(max_workers)),
        timeout_seconds=int(timeout_seconds),
        retries=int(retries),
        resolve_product_page_url_rows_fn=resolve_product_page_url_rows_fn,
        fetch_sku_rows_fn=fetch_sku_rows_fn,
        sleep_fn=sleep_fn,
    )

    skipped = Counter()
    ok_samples: list[dict[str, Any]] = []
    skipped_samples: list[dict[str, Any]] = []
    updated = 0
    would_update = 0

    for result in results:
        status = result.get("status") or "unknown"
        if status != "ok":
            skipped[status] += 1
            if len(skipped_samples) < sample_limit:
                skipped_samples.append(result)
            continue

        product = by_id.get(int(result.get("product_id") or 0), {})
        fields = fields_needing_fill(product, force=force)
        if not fields:
            skipped["already_filled"] += 1
            if len(skipped_samples) < sample_limit:
                skipped_samples.append({**result, "status": "already_filled"})
            continue

        price = money_decimal(result.get("price"))
        if price is None:
            skipped["missing_price"] += 1
            continue
        result = {**result, "fields": fields}
        if len(ok_samples) < sample_limit:
            ok_samples.append(result)
        if dry_run:
            would_update += 1
            continue
        write_price_shipping_fields(
            int(result["product_id"]),
            price,
            shipping_fee=SHIPPING_FEE_USD,
            force=force,
            execute_fn=execute_fn,
        )
        updated += 1

    return {
        "dry_run": bool(dry_run),
        "force": bool(force),
        "scanned": len(products),
        "price_candidates": sum(1 for item in results if item.get("status") == "ok"),
        "would_update": would_update,
        "updated": updated,
        "skipped": dict(sorted(skipped.items())),
        "price_range": {"min": str(PRICE_MIN_USD), "max": str(PRICE_MAX_USD)},
        "shipping_fee": str(SHIPPING_FEE_USD),
        "samples": {
            "updates": ok_samples,
            "skipped": skipped_samples,
        },
    }
