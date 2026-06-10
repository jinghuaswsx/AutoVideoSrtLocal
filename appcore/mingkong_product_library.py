"""Local Mingkong product library persistence and lookup helpers."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from appcore.db import execute, query, query_one


SYNC_TASK_CODE = "mingkong_product_library_sync"
DEFAULT_DXM02_CDP_URL = "http://127.0.0.1:9223"
MAX_DB_WEIGHT_GRAMS = 99_999_999.99


def normalize_product_code(value: Any) -> str:
    text = str(value or "").strip().strip("/")
    if not text:
        return ""
    text = re.sub(r"https?://[^/]+/products/", "", text).strip("/")
    if text.endswith("-rjc"):
        text = text[:-4]
    return text


def normalize_1688_offer_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"offer/(\d+)", text)
    if match:
        return match.group(1)
    return text if text.isdigit() else ""


def product_code_from_handle(handle: Any) -> str:
    return normalize_product_code(handle)


def parse_dxm_millis(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    seconds = number / 1000 if number > 10_000_000_000 else number
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def normalize_image_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("/productimage/"):
        return f"http://productimage-1251220924.picgz.myqcloud.com{text}"
    return text


def _normalize_public_shopify_price(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            if "." in text:
                return float(text)
            numeric = int(text)
        except ValueError:
            return None
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != int(number):
            return number
        numeric = int(number)
    return round(numeric / 100.0, 2)


def _normalize_public_shopify_weight_grams(variant: dict[str, Any]) -> float | None:
    value = variant.get("grams")
    if value not in (None, ""):
        try:
            grams = round(float(value), 2)
        except (TypeError, ValueError):
            return None
        return grams if 0 <= grams <= MAX_DB_WEIGHT_GRAMS else None
    return None


def _normalize_db_weight_grams(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        grams = round(float(value), 2)
    except (TypeError, ValueError):
        return None
    return grams if 0 <= grams <= MAX_DB_WEIGHT_GRAMS else None


def _variant_title_from_public_variant(variant: dict[str, Any]) -> str:
    title = str(variant.get("title") or "").strip()
    if title and title != "Default Title":
        return title
    parts: list[str] = []
    for key in ("option1", "option2", "option3"):
        text = str(variant.get(key) or "").strip()
        if text and text != "Default Title":
            parts.append(text)
    return " / ".join(parts)


def _public_variant_image_url(variant: dict[str, Any]) -> str:
    for key in ("featured_image", "image"):
        value = variant.get(key)
        if isinstance(value, dict):
            image = normalize_image_url(value.get("src") or value.get("url"))
        else:
            image = normalize_image_url(value)
        if image:
            return image
    return ""


def _public_product_urls_from_link(url: Any) -> list[str]:
    text = str(url or "").strip()
    if not text or "/products/" not in text:
        return []
    parsed = urlsplit(text)
    if not parsed.scheme or not parsed.netloc:
        return []
    path = parsed.path.rstrip("/")
    if path.endswith(".js") or path.endswith(".json"):
        base = path.rsplit(".", 1)[0]
    else:
        base = path
    urls: list[str] = []
    for suffix in (".js", ".json"):
        urls.append(urlunsplit((parsed.scheme, parsed.netloc, f"{base}{suffix}", "", "")))
    return urls


def public_shopify_sku_rows_from_product(
    product: dict[str, Any],
    *,
    fetch_json_fn=None,
    timeout_seconds: int = 10,
) -> list[dict[str, Any]]:
    """Build the full local SKU base from the product's public Shopify JSON."""

    urls = _public_product_urls_from_link(product.get("product_link"))
    if not urls:
        return []
    def default_fetch_json(url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/javascript,*/*",
            },
        )
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - Shopify public product JSON
            return json.loads(response.read().decode("utf-8", errors="replace"))

    fetch_json = fetch_json_fn or default_fetch_json
    for url in urls:
        try:
            payload = fetch_json(url)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        source_product = payload.get("product") if isinstance(payload.get("product"), dict) else payload
        if not isinstance(source_product, dict):
            continue
        shopify_product_id = str(source_product.get("id") or "").strip()
        rows: list[dict[str, Any]] = []
        for variant in source_product.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            variant_id = str(variant.get("id") or variant.get("shopifyVariantId") or "").strip()
            if not variant_id:
                continue
            sku = str(variant.get("sku") or "").strip()
            rows.append({
                "shopify_product_id": shopify_product_id,
                "shopify_variant_id": variant_id,
                "shopify_sku": sku,
                "shopify_price": _normalize_public_shopify_price(variant.get("price")),
                "shopify_compare_at_price": _normalize_public_shopify_price(
                    variant.get("compare_at_price") or variant.get("compareAtPrice")
                ),
                "shopify_currency": None,
                "shopify_inventory_quantity": (
                    variant.get("inventory_quantity")
                    if variant.get("inventory_quantity") is not None
                    else variant.get("inventoryQuantity")
                ),
                "shopify_weight_grams": _normalize_public_shopify_weight_grams(variant),
                "shopify_variant_title": _variant_title_from_public_variant(variant),
                "dianxiaomi_sku": "",
                "dianxiaomi_product_sku": "",
                "dianxiaomi_sku_code": "",
                "dianxiaomi_name": "",
                "source": "shopify_public",
                "image_url": _public_variant_image_url(variant),
                "purchase_1688_url": "",
                "mingkong_procurement": None,
            })
        if rows:
            return rows
    return []


def purchase_url_from_pairing(row: dict[str, Any]) -> str:
    source_url = str(row.get("sourceUrl") or "").strip()
    offer_id = normalize_1688_offer_id(
        row.get("alibabaProductId") or row.get("productIdAlibaba") or source_url
    )
    if offer_id:
        return f"https://detail.1688.com/offer/{offer_id}.html"
    return source_url


def _json_dump(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def start_sync_run(*, window_start: str | None = None, window_end: str | None = None) -> int:
    return int(execute(
        """
        INSERT INTO mingkong_product_library_sync_runs
          (status, started_at, window_start, window_end)
        VALUES ('running', NOW(), %s, %s)
        """,
        (window_start, window_end),
    ))


def finish_sync_run(
    run_id: int,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    summary = summary or {}
    execute(
        """
        UPDATE mingkong_product_library_sync_runs
        SET status=%s,
            finished_at=NOW(),
            products_seen=%s,
            variants_seen=%s,
            erp_skus_seen=%s,
            procurement_links_seen=%s,
            combo_components_seen=%s,
            summary_json=%s,
            error_message=%s
        WHERE id=%s
        """,
        (
            status,
            int(summary.get("products_seen") or 0),
            int(summary.get("variants_seen") or 0),
            int(summary.get("erp_skus_seen") or 0),
            int(summary.get("procurement_links_seen") or 0),
            int(summary.get("combo_components_seen") or 0),
            _json_dump(summary),
            error_message,
            int(run_id),
        ),
    )


def _pick_product_image(row: dict[str, Any]) -> str:
    for key in ("imgUrl", "imageUrl", "mainImage", "mainImg", "productImage"):
        image = normalize_image_url(row.get(key))
        if image:
            return image
    for variant in row.get("variants") or []:
        if isinstance(variant, dict):
            image = normalize_image_url(variant.get("imgUrl") or variant.get("imageUrl"))
            if image:
                return image
    return ""


def product_payload_from_shopify_row(row: dict[str, Any]) -> dict[str, Any]:
    shopify_product_id = str(row.get("shopifyProductId") or "").strip()
    handle = str(row.get("handle") or "").strip()
    return {
        "product_code": product_code_from_handle(handle),
        "mk_shopify_product_id": shopify_product_id,
        "mk_shop_id": str(row.get("shopId") or "").strip(),
        "mk_handle": handle,
        "mk_product_url": f"/products/{handle}" if handle else "",
        "mk_title": str(row.get("title") or "").strip(),
        "mk_title_cn": str(row.get("titleCn") or row.get("nameCn") or "").strip(),
        "mk_main_image_url": _pick_product_image(row),
        "source_url": str(row.get("sourceUrl") or "").strip(),
        "shopify_created_at": parse_dxm_millis(row.get("shopiyfCreateTime")),
        "shopify_updated_at": parse_dxm_millis(row.get("shopiyfUpdateTime")),
        "raw_json": row,
    }


def upsert_product(row: dict[str, Any]) -> int:
    payload = product_payload_from_shopify_row(row)
    if not payload["mk_shopify_product_id"]:
        raise ValueError("missing mk_shopify_product_id")
    execute(
        """
        INSERT INTO mingkong_products
          (product_code, mk_shopify_product_id, mk_shop_id, mk_handle, mk_product_url,
           mk_title, mk_title_cn, mk_main_image_url, source_url, shopify_created_at,
           shopify_updated_at, first_seen_at, last_seen_at, last_synced_at, raw_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW(),NOW(),%s)
        ON DUPLICATE KEY UPDATE
          product_code=VALUES(product_code),
          mk_shop_id=VALUES(mk_shop_id),
          mk_handle=VALUES(mk_handle),
          mk_product_url=VALUES(mk_product_url),
          mk_title=VALUES(mk_title),
          mk_title_cn=VALUES(mk_title_cn),
          mk_main_image_url=VALUES(mk_main_image_url),
          source_url=VALUES(source_url),
          shopify_created_at=VALUES(shopify_created_at),
          shopify_updated_at=VALUES(shopify_updated_at),
          last_seen_at=NOW(),
          last_synced_at=NOW(),
          raw_json=VALUES(raw_json)
        """,
        (
            payload["product_code"],
            payload["mk_shopify_product_id"],
            payload["mk_shop_id"],
            payload["mk_handle"],
            payload["mk_product_url"],
            payload["mk_title"],
            payload["mk_title_cn"],
            payload["mk_main_image_url"],
            payload["source_url"],
            payload["shopify_created_at"],
            payload["shopify_updated_at"],
            _json_dump(payload["raw_json"]),
        ),
    )
    found = query_one(
        "SELECT id FROM mingkong_products WHERE mk_shopify_product_id=%s",
        (payload["mk_shopify_product_id"],),
    )
    if not found:
        raise RuntimeError("failed to reload mingkong product")
    return int(found["id"])


def variant_payloads_from_shopify_row(product_row: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    shopify_product_id = str(product_row.get("shopifyProductId") or "").strip()
    for variant in product_row.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        variant_id = str(
            variant.get("shopifyVariantId")
            or variant.get("shopify_variant_id")
            or variant.get("id")
            or ""
        ).strip()
        if not variant_id:
            continue
        sku = str(variant.get("sku") or variant.get("shopify_sku") or "").strip()
        option_parts: list[str] = []
        for key in ("option1", "option2", "option3"):
            text = str(variant.get(key) or "").strip()
            if text:
                option_parts.append(text)
        variant_title = (
            " / ".join(option_parts)
            if option_parts
            else str(
                variant.get("shopify_variant_title")
                or variant.get("title")
                or ""
            ).strip()
        )
        out.append({
            "mk_shopify_product_id": shopify_product_id,
            "mk_shopify_variant_id": variant_id,
            "variant_title": variant_title,
            "shopify_sku": sku,
            "pair_key": sku or variant_id,
            "shopify_price": (
                variant.get("shopify_price")
                if variant.get("shopify_price") is not None
                else variant.get("price")
            ),
            "shopify_compare_at_price": (
                variant.get("shopify_compare_at_price")
                if variant.get("shopify_compare_at_price") is not None
                else variant.get("compareAtPrice")
            ),
            "shopify_inventory_quantity": (
                variant.get("shopify_inventory_quantity")
                if variant.get("shopify_inventory_quantity") is not None
                else variant.get("inventoryQuantity")
            ),
            "shopify_weight_grams": (
                _normalize_db_weight_grams(variant.get("shopify_weight_grams"))
                if variant.get("shopify_weight_grams") is not None
                else _normalize_db_weight_grams(variant.get("weight"))
            ),
            "raw_json": variant,
        })
    return out


def erp_payload_from_dxm_item(item: dict[str, Any]) -> dict[str, Any]:
    sku = str(item.get("sku") or "").strip()
    return {
        "dxm_product_id": str(item.get("id") or "").strip(),
        "dxm_parent_id": str(item.get("parentId") or "").strip(),
        "dxm_sku": sku,
        "dxm_sku_code": str(item.get("skuCode") or "").strip(),
        "dxm_product_sku": str(item.get("productSku") or item.get("goodsSku") or sku).strip(),
        "dxm_name": str(item.get("name") or "").strip(),
        "dxm_name_en": str(item.get("nameEn") or "").strip(),
        "dxm_img_url": normalize_image_url(item.get("imgUrl")),
        "dxm_source_url": str(item.get("sourceUrl") or "").strip(),
        "relation_flag": 1 if item.get("relationFlag") else 0,
        "group_state": int(item.get("groupState") or 0),
        "is_combo": 1 if int(item.get("groupState") or 0) == 1 else 0,
    }


def upsert_variant(
    *,
    mingkong_product_id: int,
    variant: dict[str, Any],
    erp_index: dict[str, dict[str, Any]],
) -> int:
    pair_key = str(variant.get("pair_key") or "").strip()
    erp = erp_index.get(pair_key) or {}
    payload = {**variant, **erp}
    execute(
        """
        INSERT INTO mingkong_product_variants
          (mingkong_product_id, mk_shopify_product_id, mk_shopify_variant_id,
           variant_title, shopify_sku, pair_key, shopify_price, shopify_compare_at_price,
           shopify_inventory_quantity, shopify_weight_grams, dxm_product_id, dxm_parent_id,
           dxm_sku, dxm_sku_code, dxm_product_sku, dxm_name, dxm_name_en, dxm_img_url,
           dxm_source_url, relation_flag, group_state, is_combo, raw_json, last_synced_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON DUPLICATE KEY UPDATE
          mingkong_product_id=VALUES(mingkong_product_id),
          mk_shopify_product_id=VALUES(mk_shopify_product_id),
          variant_title=VALUES(variant_title),
          shopify_sku=VALUES(shopify_sku),
          pair_key=VALUES(pair_key),
          shopify_price=VALUES(shopify_price),
          shopify_compare_at_price=VALUES(shopify_compare_at_price),
          shopify_inventory_quantity=VALUES(shopify_inventory_quantity),
          shopify_weight_grams=VALUES(shopify_weight_grams),
          dxm_product_id=VALUES(dxm_product_id),
          dxm_parent_id=VALUES(dxm_parent_id),
          dxm_sku=VALUES(dxm_sku),
          dxm_sku_code=VALUES(dxm_sku_code),
          dxm_product_sku=VALUES(dxm_product_sku),
          dxm_name=VALUES(dxm_name),
          dxm_name_en=VALUES(dxm_name_en),
          dxm_img_url=VALUES(dxm_img_url),
          dxm_source_url=VALUES(dxm_source_url),
          relation_flag=VALUES(relation_flag),
          group_state=VALUES(group_state),
          is_combo=VALUES(is_combo),
          raw_json=VALUES(raw_json),
          last_synced_at=NOW()
        """,
        (
            int(mingkong_product_id),
            payload.get("mk_shopify_product_id"),
            payload.get("mk_shopify_variant_id"),
            payload.get("variant_title") or None,
            payload.get("shopify_sku") or None,
            payload.get("pair_key") or None,
            payload.get("shopify_price"),
            payload.get("shopify_compare_at_price"),
            payload.get("shopify_inventory_quantity"),
            payload.get("shopify_weight_grams"),
            payload.get("dxm_product_id") or None,
            payload.get("dxm_parent_id") or None,
            payload.get("dxm_sku") or None,
            payload.get("dxm_sku_code") or None,
            payload.get("dxm_product_sku") or None,
            payload.get("dxm_name") or None,
            payload.get("dxm_name_en") or None,
            payload.get("dxm_img_url") or None,
            payload.get("dxm_source_url") or None,
            int(payload.get("relation_flag") or 0),
            int(payload.get("group_state") or 0),
            int(payload.get("is_combo") or 0),
            _json_dump(payload.get("raw_json")),
        ),
    )
    found = query_one(
        "SELECT id FROM mingkong_product_variants WHERE mk_shopify_variant_id=%s",
        (payload.get("mk_shopify_variant_id"),),
    )
    if not found:
        raise RuntimeError("failed to reload mingkong variant")
    return int(found["id"])


def normalize_pairing_row(row: dict[str, Any]) -> dict[str, Any]:
    alibaba_product_id = str(
        row.get("alibabaProductId") or row.get("productIdAlibaba") or ""
    ).strip() or normalize_1688_offer_id(row.get("sourceUrl"))
    return {
        "pairing_row_id": str(row.get("id") or "").strip(),
        "sku": str(row.get("sku") or "").strip(),
        "sku_code": str(row.get("skuCode") or "").strip(),
        "dxm_product_id": str(row.get("productId") or "").strip(),
        "dxm_name": str(row.get("name") or "").strip(),
        "purchase_1688_url": purchase_url_from_pairing(row),
        "source_url": str(row.get("sourceUrl") or "").strip(),
        "alibaba_product_id": alibaba_product_id,
        "sku_id_alibaba": str(row.get("skuIdAlibaba") or "").strip(),
        "supplier_id": str(row.get("supplierId") or "").strip(),
        "supplier_name": str(row.get("supplierName") or "").strip(),
        "pairing_state": int(row.get("state")) if str(row.get("state") or "").lstrip("-").isdigit() else None,
        "raw_json": row,
    }


def upsert_procurement_link(
    row: dict[str, Any],
    *,
    variant_id_by_sku: dict[str, int] | None = None,
) -> None:
    payload = normalize_pairing_row(row)
    if not payload["pairing_row_id"] or not payload["sku"]:
        return
    variant_id = (variant_id_by_sku or {}).get(payload["sku"])
    execute(
        """
        INSERT INTO mingkong_procurement_links
          (mingkong_variant_id, pairing_row_id, sku, sku_code, dxm_product_id, dxm_name,
           purchase_1688_url, source_url, alibaba_product_id, sku_id_alibaba, supplier_id,
           supplier_name, pairing_state, confidence, raw_json, last_synced_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'exact_sku',%s,NOW())
        ON DUPLICATE KEY UPDATE
          mingkong_variant_id=VALUES(mingkong_variant_id),
          sku=VALUES(sku),
          sku_code=VALUES(sku_code),
          dxm_product_id=VALUES(dxm_product_id),
          dxm_name=VALUES(dxm_name),
          purchase_1688_url=VALUES(purchase_1688_url),
          source_url=VALUES(source_url),
          alibaba_product_id=VALUES(alibaba_product_id),
          sku_id_alibaba=VALUES(sku_id_alibaba),
          supplier_id=VALUES(supplier_id),
          supplier_name=VALUES(supplier_name),
          pairing_state=VALUES(pairing_state),
          raw_json=VALUES(raw_json),
          last_synced_at=NOW()
        """,
        (
            variant_id,
            payload["pairing_row_id"],
            payload["sku"],
            payload["sku_code"] or None,
            payload["dxm_product_id"] or None,
            payload["dxm_name"] or None,
            payload["purchase_1688_url"] or None,
            payload["source_url"] or None,
            payload["alibaba_product_id"] or None,
            payload["sku_id_alibaba"] or None,
            payload["supplier_id"] or None,
            payload["supplier_name"] or None,
            payload["pairing_state"],
            _json_dump(payload["raw_json"]),
        ),
    )


def upsert_combo_component(
    row: dict[str, Any],
    *,
    mingkong_variant_id: int | None,
    combo_dxm_product_id: str,
    combo_dxm_sku: str,
) -> None:
    component_product_id = str(row.get("productId") or row.get("id") or "").strip()
    component_sku = str(row.get("sku") or "").strip()
    if not combo_dxm_product_id or not component_product_id or not component_sku:
        return
    execute(
        """
        INSERT INTO mingkong_combo_components
          (mingkong_variant_id, combo_dxm_product_id, combo_dxm_sku,
           component_dxm_product_id, component_sku, component_name,
           component_img_url, component_quantity, raw_json, last_synced_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON DUPLICATE KEY UPDATE
          mingkong_variant_id=VALUES(mingkong_variant_id),
          combo_dxm_sku=VALUES(combo_dxm_sku),
          component_sku=VALUES(component_sku),
          component_name=VALUES(component_name),
          component_img_url=VALUES(component_img_url),
          component_quantity=VALUES(component_quantity),
          raw_json=VALUES(raw_json),
          last_synced_at=NOW()
        """,
        (
            mingkong_variant_id,
            combo_dxm_product_id,
            combo_dxm_sku,
            component_product_id,
            component_sku,
            str(row.get("name") or "").strip() or None,
            normalize_image_url(row.get("imgUrl")) or None,
            int(row.get("num") or row.get("groupNum") or 0),
            _json_dump(row),
        ),
    )


def _candidate_shopify_ids(product: dict[str, Any]) -> set[str]:
    out = {str(product.get("shopifyid") or "").strip()}
    try:
        media_product_id = int(product.get("id") or 0)
    except (TypeError, ValueError):
        media_product_id = 0
    if media_product_id:
        for row in query(
            """
            SELECT shopify_product_id
            FROM media_product_shopify_ids
            WHERE product_id=%s
            ORDER BY domain ASC
            """,
            (media_product_id,),
        ):
            out.add(str(row.get("shopify_product_id") or "").strip())
    base_code = normalize_product_code(product.get("product_code"))
    link = str(product.get("product_link") or "").strip()
    if base_code:
        for row in query(
            """
            SELECT shopify_product_id
            FROM mingkong_material_products
            WHERE product_code=%s
               OR mk_product_link=%s
            ORDER BY id DESC
            LIMIT 20
            """,
            (base_code, link),
        ):
            out.add(str(row.get("shopify_product_id") or "").strip())
        for row in query(
            """
            SELECT product_id
            FROM dianxiaomi_product_assets
            WHERE product_code=%s OR product_url=%s
            ORDER BY id DESC
            LIMIT 20
            """,
            (base_code, link),
        ):
            out.add(str(row.get("product_id") or "").strip())
    return {value for value in out if value}


def list_library_candidates_for_product(product: dict[str, Any], *, limit: int = 10) -> list[dict[str, Any]]:
    base_code = normalize_product_code(product.get("product_code"))
    shopify_ids = _candidate_shopify_ids(product)
    clauses: list[str] = []
    args: list[Any] = []
    if base_code:
        clauses.append("p.product_code=%s")
        args.append(base_code)
        clauses.append("p.mk_handle=%s")
        args.append(base_code)
    if shopify_ids:
        placeholders = ",".join(["%s"] * len(shopify_ids))
        clauses.append(f"p.mk_shopify_product_id IN ({placeholders})")
        args.extend(sorted(shopify_ids))
    if not clauses:
        return []
    shopify_order_expr = "1"
    order_args: list[Any] = []
    if shopify_ids:
        shopify_order_placeholders = ",".join(["%s"] * len(shopify_ids))
        shopify_order_expr = f"CASE WHEN p.mk_shopify_product_id IN ({shopify_order_placeholders}) THEN 0 ELSE 1 END"
        order_args.extend(sorted(shopify_ids))
    rows = query(
        f"""
        SELECT
          p.*,
          COALESCE(stats.procurement_count, 0) AS procurement_count,
          COALESCE(stats.relation_count, 0) AS relation_count,
          COALESCE(stats.combo_count, 0) AS combo_count
        FROM mingkong_products p
        LEFT JOIN (
          SELECT
            v.mingkong_product_id,
            COUNT(DISTINCT l.id) AS procurement_count,
            SUM(CASE WHEN v.relation_flag = 1 THEN 1 ELSE 0 END) AS relation_count,
            SUM(CASE WHEN v.is_combo = 1 THEN 1 ELSE 0 END) AS combo_count
          FROM mingkong_product_variants v
          LEFT JOIN mingkong_procurement_links l ON l.mingkong_variant_id = v.id
          GROUP BY v.mingkong_product_id
        ) stats ON stats.mingkong_product_id = p.id
        WHERE {" OR ".join(clauses)}
        ORDER BY
          {shopify_order_expr},
          COALESCE(stats.procurement_count, 0) DESC,
          COALESCE(stats.relation_count, 0) DESC,
          COALESCE(stats.combo_count, 0) DESC,
          p.shopify_created_at DESC,
          p.id DESC
        LIMIT %s
        """,
        tuple(args + order_args + [int(limit)]),
    )
    return [dict(row) for row in rows]


def _procurement_for_skus(skus: list[str]) -> dict[str, dict[str, Any]]:
    clean = [sku for sku in {str(s or "").strip() for s in skus} if sku]
    if not clean:
        return {}
    placeholders = ",".join(["%s"] * len(clean))
    rows = query(
        f"""
        SELECT *
        FROM mingkong_procurement_links
        WHERE sku IN ({placeholders})
        ORDER BY pairing_state DESC, id DESC
        """,
        tuple(clean),
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        sku = str(row.get("sku") or "").strip()
        out.setdefault(sku, dict(row))
    return out


def _selected_candidate_product_ids(
    candidates: list[dict[str, Any]],
    shopify_ids: set[str],
) -> list[int]:
    if not candidates:
        return []
    matched_shopify = [
        row for row in candidates
        if str(row.get("mk_shopify_product_id") or "").strip() in shopify_ids
    ]
    if matched_shopify:
        out: list[int] = []
        seen: set[int] = set()
        for row in [*matched_shopify, *candidates]:
            row_id = int(row["id"])
            if row_id in seen:
                continue
            seen.add(row_id)
            out.append(row_id)
        return out
    with_procurement = [
        row for row in candidates
        if int(row.get("procurement_count") or 0) > 0
    ]
    selected = with_procurement or candidates
    return [int(row["id"]) for row in selected]


def _dedupe_variant_rows_by_dxm_sku(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def score(row: dict[str, Any]) -> tuple[int, int, int]:
        return (
            int(row.get("_procurement_count") or 0),
            int(row.get("_component_count") or 0),
            int(row.get("relation_flag") or 0),
        )

    out: list[dict[str, Any]] = []
    best_by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        sku = str(row.get("dxm_sku") or "").strip()
        key = f"sku:{sku}" if sku else f"variant:{row.get('mk_shopify_variant_id') or row.get('id')}"
        existing = best_by_key.get(key)
        if existing is None or score(row) > score(existing):
            best_by_key[key] = row
    seen: set[str] = set()
    for row in rows:
        sku = str(row.get("dxm_sku") or "").strip()
        key = f"sku:{sku}" if sku else f"variant:{row.get('mk_shopify_variant_id') or row.get('id')}"
        if key in seen or best_by_key.get(key) is not row:
            continue
        seen.add(key)
        out.append(row)
    return out


def sku_rows_from_library(product: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = list_library_candidates_for_product(product, limit=10)
    if not candidates:
        return []
    shopify_ids = _candidate_shopify_ids(product)
    product_ids = _selected_candidate_product_ids(candidates, shopify_ids)
    placeholders = ",".join(["%s"] * len(product_ids))
    variants = query(
        f"""
        SELECT
          v.*,
          p.product_code,
          p.mk_title,
          p.mk_main_image_url,
          COALESCE(proc.procurement_count, 0) AS _procurement_count,
          COALESCE(comp.component_count, 0) AS _component_count
        FROM mingkong_product_variants v
        JOIN mingkong_products p ON p.id = v.mingkong_product_id
        LEFT JOIN (
          SELECT sku, COUNT(*) AS procurement_count
          FROM mingkong_procurement_links
          WHERE sku IS NOT NULL AND sku <> ''
          GROUP BY sku
        ) proc ON proc.sku = NULLIF(v.dxm_sku, '')
        LEFT JOIN (
          SELECT mingkong_variant_id, COUNT(*) AS component_count
          FROM mingkong_combo_components
          GROUP BY mingkong_variant_id
        ) comp ON comp.mingkong_variant_id = v.id
        WHERE v.mingkong_product_id IN ({placeholders})
        ORDER BY FIELD(v.mingkong_product_id, {placeholders}), v.id
        """,
        tuple(product_ids + product_ids),
    )
    variants = _dedupe_variant_rows_by_dxm_sku([dict(row) for row in variants])
    skus = [
        str(row.get("dxm_sku") or "").strip()
        for row in variants
        if str(row.get("dxm_sku") or "").strip()
    ]
    procurement = _procurement_for_skus(skus)
    combo_rows = query(
        f"""
        SELECT c.*
        FROM mingkong_combo_components c
        JOIN mingkong_product_variants v ON v.id = c.mingkong_variant_id
        WHERE v.mingkong_product_id IN ({placeholders})
        ORDER BY c.id
        """,
        tuple(product_ids),
    )
    components_by_variant: dict[int, list[dict[str, Any]]] = {}
    component_procurement = _procurement_for_skus([
        str(row.get("component_sku") or "").strip()
        for row in combo_rows
    ])
    for component in combo_rows:
        item = dict(component)
        item["pairing"] = component_procurement.get(str(item.get("component_sku") or "").strip())
        components_by_variant.setdefault(int(item["mingkong_variant_id"]), []).append(item)

    product_id = int(product.get("id") or 0)
    fuzzy_candidates = []
    if product_id:
        from appcore.db import query as db_query
        rows = db_query(
            """
            SELECT id, sku, sku_code, dxm_name, purchase_1688_url, raw_json
            FROM mingkong_procurement_links
            WHERE mingkong_product_id = %s AND confidence = 'keyword_candidate'
            ORDER BY last_synced_at DESC
            """,
            (product_id,),
        )
        for r in rows:
            raw_item = json.loads(r.get("raw_json") or "{}")
            img_url = raw_item.get("imgUrl") or raw_item.get("imageUrl") or ""
            if img_url:
                img_url = normalize_image_url(img_url)
            fuzzy_candidates.append({
                "id": r.get("id"),
                "sku": r.get("sku"),
                "sku_code": r.get("sku_code"),
                "dxm_name": r.get("dxm_name") or raw_item.get("name") or "",
                "image_url": img_url,
                "purchase_1688_url": r.get("purchase_1688_url") or raw_item.get("sourceUrl") or "",
                "alibaba_product_id": r.get("alibaba_product_id") or normalize_1688_offer_id(r.get("purchase_1688_url")),
            })

    out: list[dict[str, Any]] = []
    for row in variants:
        item = dict(row)
        sku = str(item.get("dxm_sku") or "").strip()
        proc = procurement.get(sku)
        out.append({
            "shopify_product_id": item.get("mk_shopify_product_id") or "",
            "shopify_variant_id": item.get("mk_shopify_variant_id") or "",
            "shopify_sku": item.get("shopify_sku") or "",
            "shopify_variant_title": item.get("variant_title") or "",
            "dianxiaomi_sku": sku,
            "dianxiaomi_product_sku": item.get("dxm_product_sku") or "",
            "dianxiaomi_sku_code": item.get("dxm_sku_code") or "",
            "dianxiaomi_name": item.get("dxm_name") or item.get("mk_title") or "",
            "source": "mingkong_library",
            "image_url": item.get("dxm_img_url") or item.get("mk_main_image_url") or "",
            "purchase_1688_url": (proc or {}).get("purchase_1688_url") or "",
            "mingkong_product_id": item.get("mingkong_product_id"),
            "mingkong_variant_id": item.get("id"),
            "mingkong_procurement": proc or None,
            "is_combo": bool(item.get("is_combo")),
            "combo_components": components_by_variant.get(int(item["id"]), []),
            "fuzzy_candidates": fuzzy_candidates,
        })
    return out


def refresh_product_from_dxm02(
    product: dict[str, Any],
    *,
    cdp_url: str = DEFAULT_DXM02_CDP_URL,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Targeted DXM02 refresh used as a workbench fallback.

    This intentionally imports the CLI module lazily so normal page rendering
    does not load Playwright unless the local Mingkong product library misses.
    """
    product_code = normalize_product_code(product.get("product_code") or product.get("product_link"))
    if not product_code:
        return {"products_seen": 0, "variants_seen": 0, "reason": "missing_product_code"}

    from argparse import Namespace
    from tools import mingkong_product_library_sync as runner

    args = Namespace(
        cdp_url=cdp_url,
        days=0,
        product_code=product_code,
        max_pages=5,
        timeout_seconds=timeout_seconds,
        lock_timeout=180,
        include_combo_components=True,
        page_delay_seconds=0.0,
        rest_every_pages=0,
        rest_seconds=0.0,
        sku_delay_seconds=0.0,
        pair_delay_seconds=0.0,
        public_variant_delay_seconds=0.0,
    )
    return runner.run_sync(args)


def extract_keyword_from_name(name: str) -> str:
    if not name:
        return ""
    # Only keep Chinese characters
    chinese_chars = "".join(c for c in name if "\u4e00" <= c <= "\u9fff")
    if not chinese_chars:
        return ""
    # Remove common prefixes/suffixes/adjectives/stop words
    stop_words = ["儿童", "玩具", "新款", "跨境", "抖音", "爆款", "现货", "批发", "3D", "大号", "小号", "小", "个", "套", "只"]
    keyword = chinese_chars
    for word in stop_words:
        keyword = keyword.replace(word, "")
    return keyword or chinese_chars


def save_fuzzy_candidate_commodity(product_id: int, p: dict[str, Any]) -> None:
    sku = str(p.get("sku") or "").strip()
    dxm_product_id = str(p.get("id") or "").strip()
    if not sku or not dxm_product_id:
        return
    
    pairing_row_id = f"fuzzy_{dxm_product_id}"
    dxm_name = str(p.get("name") or "").strip()
    source_url = str(p.get("sourceUrl") or "").strip()
    purchase_1688_url = purchase_url_from_pairing({"sourceUrl": source_url}) or source_url
    
    execute(
        """
        INSERT INTO mingkong_procurement_links
          (mingkong_product_id, mingkong_variant_id, pairing_row_id, sku, sku_code, dxm_product_id, dxm_name,
           purchase_1688_url, source_url, alibaba_product_id, sku_id_alibaba, supplier_id,
           supplier_name, pairing_state, confidence, raw_json, last_synced_at)
        VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, NULL, NULL, -1, 'keyword_candidate', %s, NOW())
        ON DUPLICATE KEY UPDATE
          mingkong_product_id=VALUES(mingkong_product_id),
          sku=VALUES(sku),
          sku_code=VALUES(sku_code),
          dxm_product_id=VALUES(dxm_product_id),
          dxm_name=VALUES(dxm_name),
          purchase_1688_url=VALUES(purchase_1688_url),
          source_url=VALUES(source_url),
          raw_json=VALUES(raw_json),
          confidence='keyword_candidate',
          last_synced_at=NOW()
        """,
        (
            product_id,
            pairing_row_id,
            sku,
            str(p.get("skuCode") or "").strip() or None,
            dxm_product_id,
            dxm_name,
            purchase_1688_url or None,
            source_url or None,
            json.dumps(p, ensure_ascii=False),
        )
    )


def refresh_fuzzy_candidates_from_dxm02(
    product: dict[str, Any],
    keyword: str | None = None,
    *,
    cdp_url: str = DEFAULT_DXM02_CDP_URL,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Search DXM02 ERP commodities using fuzzy keyword matching and store them as candidates."""
    try:
        product_id = int(product.get("id") or 0)
    except (TypeError, ValueError):
        return {"candidates_seen": 0, "reason": "invalid_product_id"}
    
    if not product_id:
        return {"candidates_seen": 0, "reason": "missing_product_id"}
        
    if not keyword:
        # Extract search keyword from Chinese name or handle
        keyword = extract_keyword_from_name(product.get("name") or product.get("shopify_title") or "")
        
    if not keyword:
        return {"candidates_seen": 0, "reason": "empty_keyword"}

    from playwright.sync_api import sync_playwright
    from tools.mingkong_product_library_sync import DXM_PRODUCT_API, build_dxm_payload, _post_form
    from appcore.browser_automation_lock import browser_automation_lock

    candidates_seen = 0
    try:
        with browser_automation_lock(
            task_code="mingkong_product_library_fuzzy_sync",
            timeout_seconds=180,
            command=f"fuzzy_{product_id}_{keyword}",
        ):
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                
                # Fetch fuzzy candidates
                payload = build_dxm_payload(
                    1,
                    searchValue=keyword,
                    searchType=2,          # 2 is commodity name in Chinese
                    productSearchType=0,   # 0 is fuzzy substring matching
                    pageSize=100
                )
                data = _post_form(context, DXM_PRODUCT_API, payload, timeout_ms=int(timeout_seconds * 1000))
                page = data.get("data", {}).get("page", {})
                lst = page.get("list", []) if page else []
                
                for group in lst:
                    if not isinstance(group, dict):
                        continue
                    products = group.get("dxmCommodityProductList") or []
                    for p in products:
                        if not isinstance(p, dict):
                            continue
                        save_fuzzy_candidate_commodity(product_id, p)
                        candidates_seen += 1
                        
        return {"status": "success", "candidates_seen": candidates_seen, "keyword": keyword}
    except Exception as exc:
        return {"status": "failed", "candidates_seen": candidates_seen, "error": str(exc), "keyword": keyword}

