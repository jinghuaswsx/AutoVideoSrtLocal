from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from appcore.db import execute, query


log = logging.getLogger(__name__)

DOCS_ANCHOR = "docs/superpowers/specs/2026-06-12-product-roas-completion-design.md"
DEFAULT_STANDALONE_SHIPPING_FEE = Decimal("6.99")
DEFAULT_STANDALONE_PRICE = Decimal("19.99")
DEFAULT_PACKAGE_DIMENSIONS = {
    "package_length_cm": Decimal("10"),
    "package_width_cm": Decimal("5"),
    "package_height_cm": Decimal("5"),
}
PURCHASE_ESTIMATE_RATE = Decimal("0.10")
PACKET_ESTIMATE_RATE = Decimal("0.20")
SHOPIFY_PRODUCT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        decimal_value = Decimal(str(value))
    except Exception:
        return None
    return decimal_value if decimal_value > 0 else None


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _source_entry(basis: str, source: str, **meta: Any) -> dict[str, Any]:
    out = {"basis": basis, "source": source}
    for key, value in meta.items():
        if value is not None:
            out[key] = _jsonable(value)
    return out


def _load_source_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


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


def _active_roas_products(
    *,
    product_ids: list[int] | tuple[int, ...] | None = None,
    product_codes: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    where = ["deleted_at IS NULL"]
    params: list[Any] = []
    cleaned_ids = [int(value) for value in (product_ids or []) if int(value) > 0]
    cleaned_codes = [str(value).strip() for value in (product_codes or []) if str(value).strip()]
    if cleaned_ids:
        placeholders = ",".join(["%s"] * len(cleaned_ids))
        where.append(f"id IN ({placeholders})")
        params.extend(cleaned_ids)
    if cleaned_codes:
        placeholders = ",".join(["%s"] * len(cleaned_codes))
        where.append(f"product_code IN ({placeholders})")
        params.extend(cleaned_codes)
    return list(query(
        f"""
        SELECT id, product_code, product_link, localized_links_json,
               standalone_price, standalone_shipping_fee,
               purchase_price, packet_cost_estimated, packet_cost_actual,
               package_length_cm, package_width_cm, package_height_cm,
               roas_inputs_source_json
        FROM media_products
        WHERE {' AND '.join(where)}
        ORDER BY id ASC
        """,
        tuple(params),
    ) or [])


def _shopify_price_modes_by_pid() -> dict[int, dict[str, Any]]:
    return {
        int(row["product_id"]): row
        for row in _shopify_pricing_modes()
        if row.get("product_id") is not None
    }


def _variant_prices_by_pid(product_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"""
        SELECT product_id, shopify_price, shopify_variant_id, shopify_sku
        FROM media_product_skus
        WHERE product_id IN ({placeholders})
          AND shopify_price IS NOT NULL
          AND shopify_price > 0
        ORDER BY product_id ASC, shopify_variant_id ASC, id ASC
        """,
        tuple(sorted(product_ids)),
    )
    out: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        pid = int(row["product_id"])
        out.setdefault(pid, dict(row))
    return out


def _median(values: list[Decimal]) -> Decimal | None:
    cleaned = sorted(value for value in values if value > 0)
    if not cleaned:
        return None
    return _money(Decimal(str(statistics.median(cleaned))))


def _purchase_costs_by_pid(product_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    yuncang_rows = query(
        f"""
        SELECT mps.product_id, y.unit_price
        FROM media_product_skus mps
        JOIN dianxiaomi_yuncang_skus y ON y.sku = mps.dianxiaomi_sku
        WHERE mps.product_id IN ({placeholders})
          AND y.unit_price IS NOT NULL
          AND y.unit_price > 0
        """,
        tuple(sorted(product_ids)),
    )
    by_pid: dict[int, list[Decimal]] = defaultdict(list)
    for row in yuncang_rows or []:
        value = _decimal_or_none(row.get("unit_price"))
        if value is not None:
            by_pid[int(row["product_id"])].append(value)

    out: dict[int, dict[str, Any]] = {}
    for pid, values in by_pid.items():
        median = _median(values)
        if median is not None:
            out[pid] = {
                "value": median,
                "basis": "actual",
                "source": "dianxiaomi_yuncang_skus_median",
                "sample_size": len(values),
            }

    missing = sorted(set(product_ids) - set(out))
    if not missing:
        return out
    missing_placeholders = ",".join(["%s"] * len(missing))
    order_rows = query(
        f"""
        SELECT product_id, purchase_price_cny
        FROM dianxiaomi_order_lines
        WHERE product_id IN ({missing_placeholders})
          AND purchase_price_cny IS NOT NULL
          AND purchase_price_cny > 0
        """,
        tuple(missing),
    )
    order_by_pid: dict[int, list[Decimal]] = defaultdict(list)
    for row in order_rows or []:
        value = _decimal_or_none(row.get("purchase_price_cny"))
        if value is not None:
            order_by_pid[int(row["product_id"])].append(value)
    for pid, values in order_by_pid.items():
        median = _median(values)
        if median is not None:
            out[pid] = {
                "value": median,
                "basis": "actual",
                "source": "dianxiaomi_order_purchase_median",
                "sample_size": len(values),
            }
    return out


def _logistic_costs_by_pid(
    product_ids: set[int],
    *,
    days: int,
    settlement_delay_days: int,
    now_func: Callable[[], datetime] | None = None,
) -> dict[int, dict[str, Any]]:
    if not product_ids:
        return {}
    now = (now_func or datetime.now)()
    end_time = now - timedelta(days=int(settlement_delay_days))
    start_time = end_time - timedelta(days=int(days))
    fees_by_pid = _query_logistic_fees_by_pid(product_ids, start_time, end_time)
    out: dict[int, dict[str, Any]] = {}
    for pid, values in fees_by_pid.items():
        decimals = [Decimal(str(v)) for v in values if v is not None]
        median = _median(decimals)
        if median is not None:
            out[int(pid)] = {
                "value": median,
                "basis": "actual",
                "source": "dianxiaomi_logistic_fee_median",
                "sample_size": len(decimals),
                "window_start": start_time.strftime("%Y-%m-%d"),
                "window_end": end_time.strftime("%Y-%m-%d"),
            }
    return out


def _shipping_fees_by_pid(product_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    shopify_rows = query(
        f"""
        SELECT product_id, AVG(shipping) AS avg_shipping, COUNT(*) AS sample_size
        FROM shopify_orders
        WHERE product_id IN ({placeholders})
          AND shipping IS NOT NULL
          AND shipping > 0
        GROUP BY product_id
        """,
        tuple(sorted(product_ids)),
    )
    out: dict[int, dict[str, Any]] = {}
    for row in shopify_rows or []:
        value = _decimal_or_none(row.get("avg_shipping"))
        if value is not None:
            out[int(row["product_id"])] = {
                "value": _money(value),
                "basis": "actual",
                "source": "shopify_orders_average_shipping",
                "sample_size": int(row.get("sample_size") or 0),
            }

    missing = sorted(set(product_ids) - set(out))
    if not missing:
        return out
    missing_placeholders = ",".join(["%s"] * len(missing))
    dxm_rows = query(
        f"""
        SELECT product_id, AVG(ship_amount) AS avg_shipping, COUNT(*) AS sample_size
        FROM dianxiaomi_order_lines
        WHERE product_id IN ({missing_placeholders})
          AND ship_amount IS NOT NULL
          AND ship_amount > 0
        GROUP BY product_id
        """,
        tuple(missing),
    )
    for row in dxm_rows or []:
        value = _decimal_or_none(row.get("avg_shipping"))
        if value is not None:
            out[int(row["product_id"])] = {
                "value": _money(value),
                "basis": "actual",
                "source": "dianxiaomi_order_average_shipping",
                "sample_size": int(row.get("sample_size") or 0),
            }
    return out


def _shopify_product_js_url_from_page_url(product_page_url: str) -> str:
    parsed = urlparse(str(product_page_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    path = parsed.path.rstrip("/")
    if not path.endswith(".js"):
        path = f"{path}.js"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _shopify_product_js_urls(product: dict[str, Any]) -> list[str]:
    product_code = str(product.get("product_code") or "").strip()
    if not product_code:
        return []
    urls: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        js_url = _shopify_product_js_url_from_page_url(str(value or ""))
        if js_url and js_url not in seen:
            seen.add(js_url)
            urls.append(js_url)

    add(product.get("product_link"))
    try:
        from appcore import product_link_domains

        for row in product_link_domains.resolve_product_page_url_rows(product, "en"):
            add((row or {}).get("url"))
    except Exception:
        pass

    localized_links = _load_source_json(product.get("localized_links_json"))
    for raw_link in localized_links.values():
        if isinstance(raw_link, dict):
            for url_value in raw_link.values():
                add(url_value)
        else:
            add(raw_link)

    add(f"https://newjoyloo.com/products/{product_code}")
    return urls


def _shopify_product_js_url(product: dict[str, Any]) -> str:
    urls = _shopify_product_js_urls(product)
    return urls[0] if urls else ""


def _normalize_shopify_variant_price(value: Any) -> Decimal | None:
    price = _decimal_or_none(value)
    if price is None:
        return None
    text = str(value)
    if "." not in text and price >= Decimal("1000"):
        price = price / Decimal("100")
    return _money(price)


def fetch_first_shopify_variant_price(url: str, *, timeout_s: int = 12) -> dict[str, Any] | None:
    if not url:
        return None
    req = Request(
        url,
        headers={
            "User-Agent": SHOPIFY_PRODUCT_USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(req, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    product = payload.get("product") if isinstance(payload, dict) and isinstance(payload.get("product"), dict) else payload
    if not isinstance(product, dict):
        return None
    variants = product.get("variants")
    if not isinstance(variants, list) or not variants:
        return None
    first = variants[0] if isinstance(variants[0], dict) else {}
    price = _normalize_shopify_variant_price(first.get("price"))
    if price is None:
        return None
    return {
        "value": price,
        "basis": "actual",
        "source": "shopify_product_js_first_variant",
        "url": url,
        "variant_id": first.get("id"),
        "sku": first.get("sku"),
    }


def _existing_source(sources: dict[str, Any], field: str, source: str = "existing_product_field") -> dict[str, Any]:
    existing = sources.get(field)
    if isinstance(existing, dict) and existing.get("basis"):
        return dict(existing)
    return _source_entry("actual", source)


def _resolve_price(
    product: dict[str, Any],
    *,
    force: bool,
    sources: dict[str, Any],
    variant_prices: dict[int, dict[str, Any]],
    order_price_modes: dict[int, dict[str, Any]],
    price_fallback: dict[str, Any] | None,
    fetch_price_fn: Callable[..., dict[str, Any] | None],
    shopify_timeout_s: int,
) -> tuple[Decimal | None, dict[str, Any] | None, str | None]:
    current = _decimal_or_none(product.get("standalone_price"))
    if current is not None and not force:
        return current, _existing_source(sources, "standalone_price"), None

    for url in _shopify_product_js_urls(product):
        try:
            fetched = fetch_price_fn(url, timeout_s=shopify_timeout_s)
        except Exception as exc:
            fetched = None
            log.info(
                "[roas-completion] shopify price fetch failed product_id=%s url=%s error=%s",
                product.get("id"),
                url,
                exc,
            )
        if fetched and _decimal_or_none(fetched.get("value")) is not None:
            return _money(_decimal_or_none(fetched["value"])), _source_entry(
                "actual",
                fetched.get("source") or "shopify_product_js_first_variant",
                url=fetched.get("url") or url,
                variant_id=fetched.get("variant_id"),
                sku=fetched.get("sku"),
            ), None

    pid = int(product["id"])
    variant = variant_prices.get(pid)
    variant_price = _decimal_or_none((variant or {}).get("shopify_price"))
    if variant_price is not None:
        return _money(variant_price), _source_entry(
            "actual",
            "media_product_skus_first_variant",
            variant_id=(variant or {}).get("shopify_variant_id"),
            sku=(variant or {}).get("shopify_sku"),
        ), None

    order_mode = order_price_modes.get(pid)
    order_price = _decimal_or_none((order_mode or {}).get("price"))
    if order_price is not None:
        return _money(order_price), _source_entry(
            "actual",
            "shopify_orders_price_mode",
            sample_size=(order_mode or {}).get("sample_size"),
        ), None

    fallback_price = _decimal_or_none((price_fallback or {}).get("value"))
    if fallback_price is not None:
        return _money(fallback_price), _source_entry(
            (price_fallback or {}).get("basis") or "estimated",
            (price_fallback or {}).get("source") or "active_product_price_median",
            sample_size=(price_fallback or {}).get("sample_size"),
        ), None

    return None, None, "missing_price"


def _standalone_price_fallback(
    products: list[dict[str, Any]],
    *,
    variant_prices: dict[int, dict[str, Any]],
    order_price_modes: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    product_ids = {int(row["id"]) for row in products}
    values: list[Decimal] = []
    for product in products:
        value = _decimal_or_none(product.get("standalone_price"))
        if value is not None:
            values.append(value)
    for row in variant_prices.values():
        value = _decimal_or_none(row.get("shopify_price"))
        if value is not None:
            values.append(value)
    for pid, row in order_price_modes.items():
        if pid not in product_ids:
            continue
        value = _decimal_or_none(row.get("price"))
        if value is not None:
            values.append(value)
    median = _median(values)
    if median is not None:
        return {
            "value": median,
            "basis": "estimated",
            "source": "active_product_price_median",
            "sample_size": len(values),
        }
    return {
        "value": DEFAULT_STANDALONE_PRICE,
        "basis": "estimated",
        "source": "default_19_99",
    }


def _apply_field(
    updates: dict[str, Any],
    values: dict[str, Decimal | None],
    sources: dict[str, Any],
    field: str,
    value: Decimal | None,
    source: dict[str, Any] | None,
    *,
    current: Any,
    force: bool,
) -> None:
    if value is None:
        return
    current_decimal = _decimal_or_none(current)
    values[field] = _money(value)
    if force or current_decimal is None:
        updates[field] = _money(value)
    if source is not None:
        sources[field] = source


def _roas_completion_calc(values: dict[str, Decimal | None], *, rmb_per_usd: Any) -> dict[str, Any]:
    from appcore import product_roas

    calc = product_roas.calculate_break_even_roas(
        purchase_price=values.get("purchase_price"),
        estimated_packet_cost=values.get("packet_cost_estimated"),
        actual_packet_cost=values.get("packet_cost_actual"),
        standalone_price=values.get("standalone_price"),
        standalone_shipping_fee=values.get("standalone_shipping_fee"),
        rmb_per_usd=rmb_per_usd,
    )
    return _jsonable(calc)


def backfill_complete_product_roas(
    *,
    force: bool = False,
    dry_run: bool = False,
    days: int = 30,
    settlement_delay_days: int = 2,
    rmb_per_usd: Any | None = None,
    product_ids: list[int] | tuple[int, ...] | None = None,
    product_codes: list[str] | tuple[str, ...] | None = None,
    include_products: bool = False,
    progress_fn: Callable[[dict[str, Any]], None] | None = None,
    now_func: Callable[[], datetime] | None = None,
    fetch_price_fn: Callable[..., dict[str, Any] | None] = fetch_first_shopify_variant_price,
    shopify_timeout_s: int = 12,
) -> dict[str, Any]:
    """补齐所有产品级 ROAS 输入。

    Docs-anchor: docs/superpowers/specs/2026-06-12-product-roas-completion-design.md
    """
    from appcore import product_roas

    rate = product_roas.normalize_rmb_per_usd(rmb_per_usd) if rmb_per_usd is not None else product_roas.get_configured_rmb_per_usd()
    products = _active_roas_products(
        product_ids=product_ids,
        product_codes=product_codes,
    )
    product_ids = {int(row["id"]) for row in products}
    variant_prices = _variant_prices_by_pid(product_ids)
    order_price_modes = _shopify_price_modes_by_pid()
    price_fallback = _standalone_price_fallback(
        products,
        variant_prices=variant_prices,
        order_price_modes=order_price_modes,
    )
    purchase_costs = _purchase_costs_by_pid(product_ids)
    logistic_costs = _logistic_costs_by_pid(
        product_ids,
        days=days,
        settlement_delay_days=settlement_delay_days,
        now_func=now_func,
    )
    shipping_fees = _shipping_fees_by_pid(product_ids)

    stats: dict[str, Any] = {
        "docs_anchor": DOCS_ANCHOR,
        "products_total": len(products),
        "completed": 0,
        "updated": 0,
        "missing_price": 0,
        "non_positive_margin": 0,
        "estimated_price": 0,
        "estimated_purchase": 0,
        "estimated_packet": 0,
        "estimated_shipping": 0,
        "default_dimensions": 0,
        "dry_run": bool(dry_run),
        "missing_price_products": [],
    }
    product_results: list[dict[str, Any]] = []

    required_fields = (
        "standalone_price",
        "purchase_price",
        "packet_cost_estimated",
        "standalone_shipping_fee",
        "package_length_cm",
        "package_width_cm",
        "package_height_cm",
    )

    for product in products:
        pid = int(product["id"])
        sources = _load_source_json(product.get("roas_inputs_source_json"))
        values: dict[str, Decimal | None] = {
            "standalone_price": _decimal_or_none(product.get("standalone_price")),
            "purchase_price": _decimal_or_none(product.get("purchase_price")),
            "packet_cost_estimated": _decimal_or_none(product.get("packet_cost_estimated")),
            "packet_cost_actual": _decimal_or_none(product.get("packet_cost_actual")),
            "standalone_shipping_fee": _decimal_or_none(product.get("standalone_shipping_fee")),
            "package_length_cm": _decimal_or_none(product.get("package_length_cm")),
            "package_width_cm": _decimal_or_none(product.get("package_width_cm")),
            "package_height_cm": _decimal_or_none(product.get("package_height_cm")),
        }
        updates: dict[str, Any] = {}

        price, price_source, price_error = _resolve_price(
            product,
            force=force,
            sources=sources,
            variant_prices=variant_prices,
            order_price_modes=order_price_modes,
            price_fallback=price_fallback,
            fetch_price_fn=fetch_price_fn,
            shopify_timeout_s=shopify_timeout_s,
        )
        if price_error:
            stats["missing_price"] += 1
            if len(stats["missing_price_products"]) < 100:
                stats["missing_price_products"].append({
                    "id": pid,
                    "product_code": product.get("product_code") or "",
                })
        if (
            price_source
            and price_source.get("basis") == "estimated"
            and (force or _decimal_or_none(product.get("standalone_price")) is None)
        ):
            stats["estimated_price"] += 1
        _apply_field(
            updates,
            values,
            sources,
            "standalone_price",
            price,
            price_source,
            current=product.get("standalone_price"),
            force=force,
        )

        purchase = _decimal_or_none(product.get("purchase_price"))
        if purchase is not None and not force:
            sources["purchase_price"] = _existing_source(sources, "purchase_price")
        else:
            actual_purchase = purchase_costs.get(pid)
            if actual_purchase:
                _apply_field(
                    updates,
                    values,
                    sources,
                    "purchase_price",
                    actual_purchase["value"],
                    _source_entry(
                        actual_purchase["basis"],
                        actual_purchase["source"],
                        sample_size=actual_purchase.get("sample_size"),
                    ),
                    current=product.get("purchase_price"),
                    force=force,
                )
            elif values.get("standalone_price") is not None:
                estimated = _money(values["standalone_price"] * rate * PURCHASE_ESTIMATE_RATE)
                _apply_field(
                    updates,
                    values,
                    sources,
                    "purchase_price",
                    estimated,
                    _source_entry(
                        "estimated",
                        "standalone_price_10pct",
                        rmb_per_usd=rate,
                        standalone_price=values["standalone_price"],
                    ),
                    current=product.get("purchase_price"),
                    force=force,
                )
                stats["estimated_purchase"] += 1

        actual_packet = _decimal_or_none(product.get("packet_cost_actual"))
        logistic = logistic_costs.get(pid)
        if actual_packet is not None and not force:
            sources["packet_cost_actual"] = _existing_source(sources, "packet_cost_actual")
        elif logistic:
            _apply_field(
                updates,
                values,
                sources,
                "packet_cost_actual",
                logistic["value"],
                _source_entry(
                    logistic["basis"],
                    logistic["source"],
                    sample_size=logistic.get("sample_size"),
                    window_start=logistic.get("window_start"),
                    window_end=logistic.get("window_end"),
                ),
                current=product.get("packet_cost_actual"),
                force=force,
            )

        estimated_packet = _decimal_or_none(product.get("packet_cost_estimated"))
        if estimated_packet is not None and not force:
            sources["packet_cost_estimated"] = _existing_source(sources, "packet_cost_estimated")
        elif values.get("packet_cost_actual") is not None:
            _apply_field(
                updates,
                values,
                sources,
                "packet_cost_estimated",
                values["packet_cost_actual"],
                _source_entry("actual", "packet_cost_actual_mirror"),
                current=product.get("packet_cost_estimated"),
                force=force,
            )
        elif values.get("standalone_price") is not None:
            estimated = _money(values["standalone_price"] * rate * PACKET_ESTIMATE_RATE)
            _apply_field(
                updates,
                values,
                sources,
                "packet_cost_estimated",
                estimated,
                _source_entry(
                    "estimated",
                    "standalone_price_20pct",
                    rmb_per_usd=rate,
                    standalone_price=values["standalone_price"],
                ),
                current=product.get("packet_cost_estimated"),
                force=force,
            )
            stats["estimated_packet"] += 1

        shipping = _decimal_or_none(product.get("standalone_shipping_fee"))
        if shipping is not None and not force:
            sources["standalone_shipping_fee"] = _existing_source(
                sources,
                "standalone_shipping_fee",
            )
        else:
            actual_shipping = shipping_fees.get(pid)
            if actual_shipping:
                _apply_field(
                    updates,
                    values,
                    sources,
                    "standalone_shipping_fee",
                    actual_shipping["value"],
                    _source_entry(
                        actual_shipping["basis"],
                        actual_shipping["source"],
                        sample_size=actual_shipping.get("sample_size"),
                    ),
                    current=product.get("standalone_shipping_fee"),
                    force=force,
                )
            else:
                _apply_field(
                    updates,
                    values,
                    sources,
                    "standalone_shipping_fee",
                    DEFAULT_STANDALONE_SHIPPING_FEE,
                    _source_entry("estimated", "default_6_99"),
                    current=product.get("standalone_shipping_fee"),
                    force=force,
                )
                stats["estimated_shipping"] += 1

        dimensions_defaulted = False
        for field, default_value in DEFAULT_PACKAGE_DIMENSIONS.items():
            current_dimension = _decimal_or_none(product.get(field))
            if current_dimension is not None and not force:
                values[field] = current_dimension
                sources[field] = _existing_source(sources, field)
                continue
            _apply_field(
                updates,
                values,
                sources,
                field,
                default_value,
                _source_entry("default", "default_10x5x5"),
                current=product.get(field),
                force=force,
            )
            dimensions_defaulted = True
        if dimensions_defaulted:
            stats["default_dimensions"] += 1

        calc = _roas_completion_calc(values, rmb_per_usd=rate)
        sources["calculation"] = {
            "estimated_roas": calc.get("estimated_roas"),
            "actual_roas": calc.get("actual_roas"),
            "effective_basis": calc.get("effective_basis"),
            "effective_roas": calc.get("effective_roas"),
            "rmb_per_usd": calc.get("rmb_per_usd"),
            "computed_at": (now_func or datetime.now)().isoformat(timespec="seconds"),
        }
        sources["docs_anchor"] = DOCS_ANCHOR

        has_required = all(values.get(field) is not None for field in required_fields)
        if has_required and calc.get("effective_roas") is not None:
            stats["completed"] += 1
        elif has_required:
            stats["non_positive_margin"] += 1

        updates["roas_inputs_source_json"] = json.dumps(
            _jsonable(sources),
            ensure_ascii=False,
            sort_keys=True,
        )
        if updates:
            stats["updated"] += 1
            if not dry_run:
                keys = list(updates)
                set_sql = ", ".join(f"{key}=%s" for key in keys)
                params = tuple(updates[key] for key in keys) + (pid,)
                execute(
                    f"UPDATE media_products SET {set_sql} WHERE id=%s",
                    params,
                )

        missing_fields = [
            field for field in required_fields
            if values.get(field) is None
        ]
        if price_error:
            status = "missing_price"
        elif has_required and calc.get("effective_roas") is not None:
            status = "completed"
        elif has_required:
            status = "non_positive_margin"
        else:
            status = "incomplete"
        product_result = {
            "id": pid,
            "product_code": product.get("product_code") or "",
            "status": status,
            "updated": bool(updates),
            "updated_fields": sorted(key for key in updates if key != "roas_inputs_source_json"),
            "missing_fields": missing_fields,
            "effective_basis": calc.get("effective_basis"),
            "effective_roas": calc.get("effective_roas"),
            "source_basis": {
                field: (sources.get(field) or {}).get("basis")
                for field in required_fields
                if isinstance(sources.get(field), dict)
            },
        }
        if include_products or status != "completed":
            product_results.append(product_result)
        if progress_fn is not None:
            progress_fn(product_result)

    if include_products or product_results:
        stats["products"] = product_results
    return stats


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


def _query_logistic_fees_by_pid(
    pids: set[int],
    start_time: datetime,
    end_time: datetime,
) -> dict[int, list[float]]:
    """从本地 dianxiaomi_order_lines 直接聚合 logistic_fee，不再走 CDP。"""
    if not pids:
        return {}
    placeholders = ",".join(["%s"] * len(pids))
    rows = query(
        f"SELECT product_id, logistic_fee "
        f"FROM dianxiaomi_order_lines "
        f"WHERE product_id IN ({placeholders}) "
        f"  AND logistic_fee IS NOT NULL AND logistic_fee > 0 "
        f"  AND paid_at >= %s AND paid_at <= %s",
        tuple(pids) + (start_time, end_time),
    )
    fees_by_pid: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        fees_by_pid[int(r["product_id"])].append(float(r["logistic_fee"]))
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
    now_func: Callable[[], datetime] | None = None,
    **__kwargs,  # 兼容旧 cdp_url / page_provider 参数（已废弃）
) -> dict[str, Any]:
    pid_to_shop, shop_to_pids = _dianxiaomi_shop_groups(force=force)
    if not pid_to_shop:
        return {"candidates": 0, "shops": 0, "with_fees": 0, "updated": 0}
    now = (now_func or datetime.now)()
    end_time = now - timedelta(days=settlement_delay_days)
    start_time = end_time - timedelta(days=int(days))

    fees_by_pid = _query_logistic_fees_by_pid(set(pid_to_shop.keys()), start_time, end_time)

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
