"""店小秘订单导入、产品域作用域、订单解析与分析查询。

由 ``appcore.order_analytics`` package 在 PR 1.2 抽出；函数体逐字符
保留，行为不变。``__init__.py`` 通过显式 re-export 把这里的公开符号
带回 ``appcore.order_analytics`` 命名空间，调用方与子类零改动。
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ._constants import (
    META_ATTRIBUTION_CUTOVER_HOUR_BJ,
    META_ATTRIBUTION_TIMEZONE,
    _DIANXIAOMI_EXCLUDED_DOMAINS,
    _DIANXIAOMI_SITE_DOMAINS,
)
from ._helpers import (
    _canonical_product_handle,
    _combined_link_text,
    _json_dumps_for_db,
    _money,
    _parse_dianxiaomi_ts,
    _parse_iso_date_param,
    _revenue_with_shipping,
    _safe_decimal_float,
    _safe_int,
)


# DB 入口走 module-level wrapper：函数体里写 query(...) 时 Python 走 LOAD_GLOBAL
# 在本 module dict 找到这些 wrapper，wrapper 内部动态从 parent package facade 拿
# 真实 callable。这样 monkeypatch.setattr(oa, "query", fake) 替换的是 oa namespace
# 的 query，wrapper 下次调用就会拿到 fake，行为与单文件时代等价。
# (LOAD_GLOBAL 不走 module-level __getattr__，所以必须真函数定义。)
def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def get_conn(*args, **kwargs):
    return _facade().get_conn(*args, **kwargs)


@dataclass(frozen=True)
class DianxiaomiProductScope:
    by_shopify_id: dict[str, dict[str, Any]]
    by_handle: dict[str, dict[str, Any]]
    excluded_shopify_ids: set[str]
    excluded_handles: set[str]
    requested_site_codes: set[str]


def compute_meta_business_window_bj(day_value: date) -> tuple[datetime, datetime]:
    start = datetime(day_value.year, day_value.month, day_value.day, META_ATTRIBUTION_CUTOVER_HOUR_BJ, 0, 0)
    return start, start + timedelta(days=1)


def compute_order_meta_attribution(
    *values: datetime | None,
) -> dict[str, Any]:
    attribution_time = next((value for value in values if value is not None), None)
    if attribution_time is None:
        return {
            "attribution_time_at": None,
            "attribution_source": None,
            "attribution_timezone": META_ATTRIBUTION_TIMEZONE,
            "meta_business_date": None,
            "meta_window_start_at": None,
            "meta_window_end_at": None,
        }
    business_date = (attribution_time - timedelta(hours=META_ATTRIBUTION_CUTOVER_HOUR_BJ)).date()
    window_start, window_end = compute_meta_business_window_bj(business_date)
    return {
        "attribution_time_at": attribution_time,
        "attribution_source": None,
        "attribution_timezone": META_ATTRIBUTION_TIMEZONE,
        "meta_business_date": business_date,
        "meta_window_start_at": window_start,
        "meta_window_end_at": window_end,
    }


def _infer_dianxiaomi_site_code_from_text(text: str, requested_site_codes: set[str]) -> str | None:
    normalized = (text or "").lower()
    if not normalized:
        return None
    if any(domain in normalized for domain in _DIANXIAOMI_EXCLUDED_DOMAINS):
        return "smartgearx"
    for site_code in sorted(requested_site_codes):
        domains = _DIANXIAOMI_SITE_DOMAINS.get(site_code, (site_code,))
        if any(domain in normalized for domain in domains):
            return site_code
    return None


def extract_dianxiaomi_shopify_product_id(line: dict[str, Any]) -> str | None:
    for key in ("productId", "shopifyProductId", "pid"):
        value = str(line.get(key) or "").strip()
        if value.isdigit():
            return value
    for key in ("productUrl", "sourceUrl"):
        text = str(line.get(key) or "")
        match = re.search(r"/products/(\d+)", text)
        if match:
            return match.group(1)
    return None


def extract_dianxiaomi_product_handle(line: dict[str, Any]) -> str | None:
    for key in ("productUrl", "sourceUrl"):
        handle = _canonical_product_handle(line.get(key))
        if handle:
            return handle
    return None


def build_dianxiaomi_product_scope(site_codes: list[str]) -> DianxiaomiProductScope:
    requested = {str(code).strip().lower() for code in site_codes if str(code).strip()}
    rows = query(
        "SELECT id, product_code, shopifyid, product_link, localized_links_json "
        "FROM media_products "
        "WHERE deleted_at IS NULL AND shopifyid IS NOT NULL AND shopifyid <> ''"
    )
    by_shopify_id: dict[str, dict[str, Any]] = {}
    by_handle: dict[str, dict[str, Any]] = {}
    excluded_shopify_ids: set[str] = set()
    excluded_handles: set[str] = set()
    for row in rows:
        shopifyid = str(row.get("shopifyid") or "").strip()
        product_code = str(row.get("product_code") or "").strip()
        handle = _canonical_product_handle(product_code)
        if not shopifyid and not handle:
            continue
        site_code = _infer_dianxiaomi_site_code_from_text(
            _combined_link_text(
                product_code,
                row.get("product_link"),
                row.get("localized_links_json"),
            ),
            requested,
        )
        if site_code == "smartgearx":
            if shopifyid:
                excluded_shopify_ids.add(shopifyid)
            if handle:
                excluded_handles.add(handle)
            continue
        if site_code in requested or (site_code is None and handle):
            product = {
                "product_id": row.get("id"),
                "product_code": product_code,
                "site_code": site_code,
                "shopifyid": shopifyid,
            }
            if shopifyid:
                by_shopify_id[shopifyid] = product
            if handle:
                by_handle[handle] = product
    return DianxiaomiProductScope(
        by_shopify_id=by_shopify_id,
        by_handle=by_handle,
        excluded_shopify_ids=excluded_shopify_ids,
        excluded_handles=excluded_handles,
        requested_site_codes=requested,
    )


def _dianxiaomi_order_lines(order: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("productList", "cancelProductList"):
        value = order.get(key) or []
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _resolve_dianxiaomi_line_product(
    line: dict[str, Any],
    shopify_product_id: str,
    scope: DianxiaomiProductScope,
) -> dict[str, Any] | None:
    handle = extract_dianxiaomi_product_handle(line)
    if handle in scope.excluded_handles:
        return None
    if shopify_product_id in scope.excluded_shopify_ids:
        return None
    site_code = _infer_dianxiaomi_site_code_from_text(
        _combined_link_text(line.get("productUrl"), line.get("sourceUrl")),
        scope.requested_site_codes,
    )

    def resolve_site(product: dict[str, Any]) -> dict[str, Any] | None:
        if product.get("site_code"):
            return product
        if site_code == "smartgearx" or site_code not in scope.requested_site_codes:
            return None
        resolved = dict(product)
        resolved["site_code"] = site_code
        return resolved

    product = scope.by_shopify_id.get(shopify_product_id)
    if product:
        return resolve_site(product)
    if handle:
        product = scope.by_handle.get(handle)
        if product:
            return resolve_site(product)
    if site_code == "smartgearx" or site_code not in scope.requested_site_codes:
        return None
    return {
        "product_id": None,
        "product_code": None,
        "site_code": site_code,
        "shopifyid": shopify_product_id,
    }


def normalize_dianxiaomi_order(
    order: dict[str, Any],
    scope: DianxiaomiProductScope,
    profits_by_package_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    normalized: list[dict[str, Any]] = []
    skipped = 0
    package_id = str(order.get("id") or order.get("packageId") or "").strip()
    profit = profits_by_package_id.get(package_id) or {}
    for line in _dianxiaomi_order_lines(order):
        shopify_product_id = extract_dianxiaomi_shopify_product_id(line)
        if not shopify_product_id:
            skipped += 1
            continue
        product = _resolve_dianxiaomi_line_product(line, shopify_product_id, scope)
        if not product:
            skipped += 1
            continue
        quantity = _safe_int(str(line.get("quantity") or line.get("productCount") or "1"), 1)
        unit_price = _safe_decimal_float(line.get("price"))
        line_amount = round((unit_price or 0) * quantity, 2) if unit_price is not None else None
        addr = order.get("dxmPackageAddr") if isinstance(order.get("dxmPackageAddr"), dict) else {}
        order_created_at = _parse_dianxiaomi_ts(order.get("orderCreateTime"))
        order_paid_at = _parse_dianxiaomi_ts(order.get("orderPayTime"))
        paid_at = _parse_dianxiaomi_ts(order.get("paidTime"))
        shipped_at = _parse_dianxiaomi_ts(order.get("shippedTime"))
        attribution = compute_order_meta_attribution(order_paid_at, paid_at, order_created_at, shipped_at)
        attribution["attribution_source"] = (
            "order_paid_at" if order_paid_at is not None else
            "paid_at" if paid_at is not None else
            "order_created_at" if order_created_at is not None else
            "shipped_at" if shipped_at is not None else None
        )
        normalized.append({
            "site_code": product["site_code"],
            "product_id": product["product_id"],
            "product_code": product["product_code"],
            "shopify_product_id": shopify_product_id,
            "dxm_shop_id": str(order.get("shopId") or "").strip() or None,
            "dxm_shop_name": str(order.get("shopName") or "").strip() or None,
            "dxm_package_id": package_id,
            "dxm_order_id": str(order.get("orderId") or "").strip() or None,
            "extended_order_id": str(order.get("extendedOrderId") or "").strip() or None,
            "package_number": str(order.get("packageNumber") or "").strip() or None,
            "platform": str(order.get("platform") or order.get("shopPlatform") or "").strip() or None,
            "order_state": str(order.get("state") or "").strip() or None,
            "buyer_name": str(order.get("buyerName") or "").strip() or None,
            "buyer_account": str(order.get("buyerAccount") or "").strip() or None,
            "product_name": str(line.get("productName") or "").strip()[:500] or None,
            "product_sku": str(line.get("productSku") or "").strip()[:128] or None,
            "product_sub_sku": str(line.get("productSubSku") or "").strip()[:128] or None,
            "product_display_sku": str(line.get("productDisplaySku") or line.get("displaySku") or "").strip()[:128] or None,
            "variant_text": str(line.get("attrListStr") or line.get("attrList") or "").strip()[:500] or None,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_amount": line_amount,
            "order_amount": _safe_decimal_float(order.get("orderAmount")),
            "order_currency": str(order.get("orderUnit") or "").strip() or None,
            "ship_amount": _safe_decimal_float(order.get("shipAmount")),
            "amount_with_shipping": _safe_decimal_float(order.get("orderAmount")),
            "amount_cny": _safe_decimal_float(profit.get("amountCNY")),
            "logistic_fee": _safe_decimal_float(profit.get("logisticFee")),
            "profit": _safe_decimal_float(profit.get("profit")),
            "refund_amount_usd": _safe_decimal_float(order.get("refundAmountUsd")),
            "refund_amount": _safe_decimal_float(order.get("refundAmount")),
            "buyer_country": str(order.get("buyerCountry") or addr.get("country") or "").strip() or None,
            "buyer_country_name": str(order.get("countryCN") or "").strip() or None,
            "province": str(addr.get("province") or "").strip() or None,
            "city": str(addr.get("city") or "").strip() or None,
            "order_created_at": order_created_at,
            "order_paid_at": order_paid_at,
            "paid_at": paid_at,
            "shipped_at": shipped_at,
            **attribution,
            "raw_order_json": order,
            "raw_line_json": line,
            "profit_json": profit or None,
        })
    return normalized, skipped


def start_dianxiaomi_order_import_batch(
    date_from: str,
    date_to: str,
    site_codes: list[str],
    included_shopify_ids_count: int,
) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dianxiaomi_order_import_batches "
                "(date_from, date_to, requested_site_codes, included_shopify_ids_count) "
                "VALUES (%s,%s,%s,%s)",
                (date_from, date_to, ",".join(site_codes), included_shopify_ids_count),
            )
            batch_id = int(cur.lastrowid)
        conn.commit()
        return batch_id
    finally:
        conn.close()


def finish_dianxiaomi_order_import_batch(
    batch_id: int,
    status: str,
    summary: dict[str, Any],
    error_message: str | None = None,
) -> None:
    execute(
        "UPDATE dianxiaomi_order_import_batches SET status=%s, finished_at=NOW(), "
        "duration_seconds=TIMESTAMPDIFF(SECOND, started_at, NOW()), "
        "total_pages=%s, fetched_orders=%s, fetched_lines=%s, inserted_lines=%s, "
        "updated_lines=%s, skipped_lines=%s, error_message=%s, summary_json=%s "
        "WHERE id=%s",
        (
            status,
            int(summary.get("total_pages") or 0),
            int(summary.get("fetched_orders") or 0),
            int(summary.get("fetched_lines") or 0),
            int(summary.get("inserted_lines") or 0),
            int(summary.get("updated_lines") or 0),
            int(summary.get("skipped_lines") or 0),
            error_message,
            json.dumps(summary, ensure_ascii=False, default=str),
            batch_id,
        ),
    )


_DIANXIAOMI_ORDER_LINE_COLUMNS = [
    "batch_id",
    "site_code",
    "product_id",
    "product_code",
    "shopify_product_id",
    "dxm_shop_id",
    "dxm_shop_name",
    "dxm_package_id",
    "dxm_order_id",
    "extended_order_id",
    "package_number",
    "platform",
    "order_state",
    "buyer_name",
    "buyer_account",
    "product_name",
    "product_sku",
    "product_sub_sku",
    "product_display_sku",
    "variant_text",
    "quantity",
    "unit_price",
    "line_amount",
    "order_amount",
    "order_currency",
    "ship_amount",
    "amount_with_shipping",
    "amount_cny",
    "logistic_fee",
    "profit",
    "refund_amount_usd",
    "refund_amount",
    "buyer_country",
    "buyer_country_name",
    "province",
    "city",
    "order_created_at",
    "order_paid_at",
    "paid_at",
    "shipped_at",
    "attribution_time_at",
    "attribution_source",
    "attribution_timezone",
    "meta_business_date",
    "meta_window_start_at",
    "meta_window_end_at",
    "raw_order_json",
    "raw_line_json",
    "profit_json",
]


def _dianxiaomi_order_line_values(batch_id: int, row: dict[str, Any]) -> tuple[Any, ...]:
    enriched = dict(row)
    enriched["batch_id"] = batch_id
    enriched["raw_order_json"] = _json_dumps_for_db(row.get("raw_order_json"))
    enriched["raw_line_json"] = _json_dumps_for_db(row.get("raw_line_json"))
    enriched["profit_json"] = _json_dumps_for_db(row.get("profit_json"))
    return tuple(enriched.get(column) for column in _DIANXIAOMI_ORDER_LINE_COLUMNS)


def upsert_dianxiaomi_order_lines(batch_id: int, rows: list[dict[str, Any]]) -> dict[str, int]:
    if not rows:
        return {"affected": 0, "rows": 0}
    columns_sql = ", ".join(_DIANXIAOMI_ORDER_LINE_COLUMNS)
    placeholders = ", ".join(["%s"] * len(_DIANXIAOMI_ORDER_LINE_COLUMNS))
    update_columns = [
        "batch_id",
        "site_code",
        "product_id",
        "product_code",
        "quantity",
        "unit_price",
        "line_amount",
        "order_amount",
        "order_currency",
        "ship_amount",
        "amount_with_shipping",
        "amount_cny",
        "logistic_fee",
        "profit",
        "refund_amount_usd",
        "refund_amount",
        "buyer_country",
        "buyer_country_name",
        "province",
        "city",
        "order_created_at",
        "order_paid_at",
        "paid_at",
        "shipped_at",
        "attribution_time_at",
        "attribution_source",
        "attribution_timezone",
        "meta_business_date",
        "meta_window_start_at",
        "meta_window_end_at",
        "raw_order_json",
        "raw_line_json",
        "profit_json",
    ]
    updates_sql = ", ".join(f"{column}=VALUES({column})" for column in update_columns)
    sql = (
        f"INSERT INTO dianxiaomi_order_lines ({columns_sql}) "
        f"VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates_sql}, imported_at=NOW()"
    )

    affected = 0
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(sql, _dianxiaomi_order_line_values(batch_id, row))
                affected += int(cur.rowcount or 0)
        conn.commit()
    finally:
        conn.close()
    return {"affected": affected, "rows": len(rows)}


def get_dianxiaomi_order_import_batches(limit: int = 20) -> list[dict]:
    limit = max(1, min(int(limit or 20), 100))
    return query(
        "SELECT * FROM dianxiaomi_order_import_batches "
        "ORDER BY started_at DESC LIMIT %s",
        (limit,),
    )


def _dianxiaomi_order_time_expr() -> str:
    return "COALESCE(order_paid_at, paid_at, order_created_at, shipped_at, attribution_time_at)"


def get_dianxiaomi_order_analysis(
    start_date: str,
    end_date: str,
    *,
    page: int = 1,
    page_size: int = 50,
    store: str | None = None,
) -> dict:
    start = _parse_iso_date_param(start_date, "start_date")
    end = _parse_iso_date_param(end_date, "end_date")
    if end < start:
        raise ValueError("end_date must be >= start_date")

    page = max(1, int(page or 1))
    page_size = max(10, min(int(page_size or 50), 200))
    offset = (page - 1) * page_size

    store_code = str(store or "").strip().lower()
    if store_code == "all":
        store_code = ""

    where_clauses = [
        "meta_business_date >= %s",
        "meta_business_date <= %s",
    ]
    where_args: list[Any] = [start, end]
    if store_code:
        where_clauses.append("site_code = %s")
        where_args.append(store_code)
    where_sql = "FROM dianxiaomi_order_lines WHERE " + " AND ".join(where_clauses)
    where_args_tuple = tuple(where_args)
    summary_row = query_one(
        "SELECT COUNT(DISTINCT dxm_package_id) AS order_count, "
        "SUM(COALESCE(quantity, 0)) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS product_net_sales, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping "
        + where_sql,
        where_args_tuple,
    ) or {}
    total_row = query_one(
        "SELECT COUNT(*) AS total " + where_sql,
        where_args_tuple,
    ) or {}

    order_time_expr = _dianxiaomi_order_time_expr()
    rows = query(
        "SELECT id, site_code, dxm_shop_name, dxm_package_id, dxm_order_id, extended_order_id, "
        "package_number, order_state, buyer_country, buyer_country_name, "
        + order_time_expr + " AS order_time, meta_business_date, product_name, product_sku, "
        "product_sub_sku, product_display_sku, variant_text, quantity, unit_price, line_amount, "
        "ship_amount, order_currency "
        + where_sql + " "
        "ORDER BY order_time DESC, dxm_package_id DESC, id DESC LIMIT %s OFFSET %s",
        where_args_tuple + (page_size, offset),
    )

    total = int(total_row.get("total") or 0)
    product_net_sales = _money(summary_row.get("product_net_sales"))
    shipping = _money(summary_row.get("shipping"))
    return {
        "period": {
            "start_date": start,
            "end_date": end,
            "date_field": "meta_business_date",
            "timezone": META_ATTRIBUTION_TIMEZONE,
        },
        "filters": {
            "store": store_code,
        },
        "summary": {
            "total_sales": _revenue_with_shipping(product_net_sales, shipping),
            "order_count": int(summary_row.get("order_count") or 0),
            "units": int(summary_row.get("units") or 0),
            "shipping": shipping,
            "product_net_sales": product_net_sales,
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        },
        "rows": [
            {
                **row,
                "quantity": int(row.get("quantity") or 0),
                "unit_price": _money(row.get("unit_price")),
                "line_amount": _money(row.get("line_amount")),
                "ship_amount": _money(row.get("ship_amount")),
                "total_sales": _revenue_with_shipping(
                    _money(row.get("line_amount")),
                    _money(row.get("ship_amount")),
                ),
            }
            for row in rows
        ],
    }
