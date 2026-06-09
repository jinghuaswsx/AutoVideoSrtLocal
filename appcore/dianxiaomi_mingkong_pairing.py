"""DXM03 procurement pairing helpers for the Mingkong SKU workbench."""

from __future__ import annotations

import os
import re
import time
from typing import Any

from appcore.browser_automation_lock import browser_automation_lock
from appcore import mingkong_product_library


DXM_BASE_URL = "https://www.dianxiaomi.com"
DEFAULT_DXM03_CDP_URL = "http://127.0.0.1:9225"

DXM_PRODUCT_API = "/api/dxmCommodityProduct/pageList.json"
DXM_UPDATE_SOURCE_URL_API = "/api/dxmCommodityProduct/updateUrl.json"
DXM_CHILD_SKU_INFO_API = "/api/dxmCommodityProduct/getChildSkuInfo.json"
PAIR_LIST_API = "/api/dxmAlibabaProductPair/alibabaProductPairPageList.json"
PAIR_SOURCE_SYNC_API = "/api/dxmAlibabaProductPair/asnycAlibabaByDxmProSourceUrlOpt.json"
PAIR_CHECK_API = "/api/dxmAlibabaProductPair/getCheckPairOpt.json"
PAIR_CONFIRM_API = "/api/dxmAlibabaProductPair/confirmPairOpt.json"


class DianxiaomiPairingError(RuntimeError):
    """Raised when DXM03 cannot be reached or returns an unexpected response."""


def dxm03_cdp_url() -> str:
    return (
        os.getenv("DXM03_DIANXIAOMI_CDP_URL")
        or os.getenv("DIANXIAOMI_DXM03_CDP_URL")
        or DEFAULT_DXM03_CDP_URL
    )


def normalize_1688_offer_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"offer/(\d+)", text)
    if match:
        return match.group(1)
    return text if text.isdigit() else ""


def _purchase_url_for_offer(offer_id: str, fallback: str = "") -> str:
    offer_id = normalize_1688_offer_id(offer_id)
    if offer_id:
        return f"https://detail.1688.com/offer/{offer_id}.html"
    return str(fallback or "").strip()


def _normalize_image_url(value: Any) -> str:
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


def _stringify_form(payload: dict[str, Any]) -> dict[str, str]:
    return {key: "" if value is None else str(value) for key, value in payload.items()}


def _ensure_success(payload: dict[str, Any], action: str) -> None:
    code = payload.get("code")
    if code not in (0, "0", None):
        raise DianxiaomiPairingError(
            f"{action} failed: {payload.get('msg') or payload.get('message') or code}"
        )


def _post_form(ctx, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = ctx.request.post(
        f"{DXM_BASE_URL}{path}",
        form=_stringify_form(payload),
        timeout=30000,
    )
    text = response.text()
    if response.status >= 400:
        raise DianxiaomiPairingError(f"DXM03 HTTP {response.status}: {text[:200]}")
    try:
        data = response.json()
    except Exception as exc:
        raise DianxiaomiPairingError(f"DXM03 returned non-JSON: {text[:200]}") from exc
    if not isinstance(data, dict):
        raise DianxiaomiPairingError("DXM03 returned invalid JSON payload")
    return data


def _dxm_product_payload(sku: str) -> dict[str, Any]:
    return {
        "pageNo": 1,
        "pageSize": 100,
        "searchType": 1,
        "searchValue": sku,
        "saleMode": -1,
        "productMode": -1,
        "productPxId": 1,
        "productPxSxId": 0,
        "fullCid": "",
        "productSearchType": 1,
        "productGroupLxId": 1,
    }


def _pair_list_payload(sku: str) -> dict[str, Any]:
    return {
        "pageNo": 1,
        "pageSize": 20,
        "status": "",
        "searchType": 1,
        "searchValue": sku,
        "searchMode": 1,
    }


def _search_commodity(ctx, sku: str) -> dict[str, Any] | None:
    payload = _post_form(ctx, DXM_PRODUCT_API, _dxm_product_payload(sku))
    _ensure_success(payload, "search commodity")
    page = ((payload.get("data") or {}).get("page") or {})
    for group in page.get("list") or []:
        for item in group.get("dxmCommodityProductList") or []:
            if str(item.get("sku") or "").strip() == sku:
                return {
                    "id": str(item.get("id") or "").strip(),
                    "parent_id": str(item.get("parentId") or "").strip(),
                    "sku": sku,
                    "sku_code": str(item.get("skuCode") or "").strip(),
                    "name": str(item.get("name") or "").strip(),
                    "name_en": str(item.get("nameEn") or "").strip(),
                    "image_url": _normalize_image_url(item.get("imgUrl")),
                    "source_url": str(item.get("sourceUrl") or "").strip(),
                    "relation_flag": bool(item.get("relationFlag")),
                    "group_state": int(item.get("groupState") or 0),
                    "is_combo": int(item.get("groupState") or 0) == 1,
                }
    return None


def _search_child_sku_info(ctx, product_id: str) -> list[dict[str, Any]]:
    if not str(product_id or "").strip():
        return []
    payload = _post_form(ctx, DXM_CHILD_SKU_INFO_API, {"id": product_id})
    _ensure_success(payload, "search child sku info")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if data.get("code") not in (0, "0", None):
        raise DianxiaomiPairingError(data.get("msg") or "search child sku info failed")
    rows = data.get("data") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku") or "").strip()
        if not sku:
            continue
        out.append({
            "product_id": str(row.get("productId") or "").strip(),
            "sku": sku,
            "name": str(row.get("name") or "").strip(),
            "quantity": int(row.get("num") or 0),
            "image_url": _normalize_image_url(row.get("imgUrl")),
        })
    return out


def _candidate_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("skuName", "name", "skuValue", "attributes", "attributeName", "specName", "title"):
        value = item.get(key)
        if value:
            parts.append(str(value))
    for key in ("skuValueList", "attributeList", "attr", "skuAttributes"):
        value = item.get(key)
        if isinstance(value, list):
            parts.extend(str(v) for v in value if v)
    return " ".join(parts).strip()


def _extract_candidate_skus(row: dict[str, Any]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_candidate(product_id: Any, sku_id: Any, title: Any) -> None:
        product_text = str(product_id or "").strip()
        sku_text = str(sku_id or "").strip()
        if not product_text or not sku_text:
            return
        key = (product_text, sku_text)
        if key in seen:
            return
        seen.add(key)
        candidates.append({
            "product_id_alibaba": product_text,
            "sku_id_alibaba": sku_text,
            "title": str(title or "").strip(),
        })

    selected_product_id = row.get("alibabaProductId") or row.get("productIdAlibaba")
    selected_sku_id = row.get("skuIdAlibaba")
    if selected_product_id and selected_sku_id:
        add_candidate(
            selected_product_id,
            selected_sku_id,
            row.get("skuNameAlibaba") or row.get("alibabaSkuName") or row.get("skuName"),
        )

    for product in row.get("alibabaProductList") or []:
        product_id = (
            product.get("alibabaProductId")
            or product.get("productIdAlibaba")
            or product.get("id")
            or product.get("offerId")
        )
        product_title = product.get("title") or product.get("name")
        sku_lists = []
        for key in (
            "alibabaProductSkuList",
            "skuList",
            "skuInfos",
            "alibabaSkuList",
            "productSkuList",
        ):
            value = product.get(key)
            if isinstance(value, list):
                sku_lists.extend(value)
        for sku_item in sku_lists:
            add_candidate(
                product_id,
                sku_item.get("skuIdAlibaba") or sku_item.get("skuId") or sku_item.get("id"),
                _candidate_text(sku_item) or product_title,
            )
    return candidates


def _normalize_pair_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    state_raw = row.get("state")
    try:
        state = int(state_raw)
    except (TypeError, ValueError):
        state = None
    alibaba_product_id = (
        str(row.get("alibabaProductId") or row.get("productIdAlibaba") or "").strip()
        or normalize_1688_offer_id(row.get("sourceUrl"))
    )
    return {
        "pair_row_id": str(row.get("id") or "").strip(),
        "product_id": str(row.get("productId") or "").strip(),
        "sku": str(row.get("sku") or "").strip(),
        "sku_code": str(row.get("skuCode") or "").strip(),
        "name": str(row.get("name") or "").strip(),
        "source_url": str(row.get("sourceUrl") or "").strip(),
        "state": state,
        "is_paired": state == 1,
        "alibaba_product_id": alibaba_product_id,
        "sku_id_alibaba": str(row.get("skuIdAlibaba") or "").strip(),
        "supplier_name": str(row.get("supplierName") or "").strip(),
        "alibaba_title": str(row.get("alibabaProductTitle") or row.get("titleAlibaba") or "").strip(),
        "candidates": _extract_candidate_skus(row),
    }


def _search_pair(ctx, sku: str) -> dict[str, Any] | None:
    payload = _post_form(ctx, PAIR_LIST_API, _pair_list_payload(sku))
    _ensure_success(payload, "search pairing")
    page = ((payload.get("data") or {}).get("page") or {})
    for row in page.get("list") or []:
        if str(row.get("sku") or "").strip() == sku:
            return _normalize_pair_row(row)
    return None


def _update_source_url(ctx, commodity_id: str, purchase_url: str) -> dict[str, Any]:
    payload = _post_form(
        ctx,
        DXM_UPDATE_SOURCE_URL_API,
        {"id": commodity_id, "sourceUrl": purchase_url},
    )
    _ensure_success(payload, "update source url")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    data_code = data.get("code")
    if data_code not in (0, "0", None):
        raise DianxiaomiPairingError(data.get("msg") or "update source url failed")
    return payload


def _trigger_source_sync(ctx, purchase_url: str) -> dict[str, Any]:
    payload = _post_form(
        ctx,
        PAIR_SOURCE_SYNC_API,
        {"urls": purchase_url, "nums": 1},
    )
    _ensure_success(payload, "sync 1688 source url")
    return payload


def _check_pair(ctx, product_id_alibaba: str, sku_id_alibaba: str) -> dict[str, Any]:
    payload = _post_form(
        ctx,
        PAIR_CHECK_API,
        {
            "checkType": 0,
            "productIdAlibaba": product_id_alibaba,
            "skuIdAlibaba": sku_id_alibaba,
        },
    )
    _ensure_success(payload, "check pairing")
    return payload


def _confirm_pair(
    ctx,
    *,
    pair_row_id: str,
    product_id_alibaba: str,
    sku_id_alibaba: str,
) -> dict[str, Any]:
    payload = _post_form(
        ctx,
        PAIR_CONFIRM_API,
        {
            "pairProductId": pair_row_id,
            "productIdAlibaba": product_id_alibaba,
            "skuIdAlibaba": sku_id_alibaba,
        },
    )
    _ensure_success(payload, "confirm pairing")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    data_code = data.get("code")
    if data_code not in (1, "1", 0, "0", None):
        raise DianxiaomiPairingError(data.get("msg") or "confirm pairing failed")
    return payload


def _open_dxm03_context(cdp_url: str):
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        return playwright, browser, ctx
    except Exception:
        playwright.stop()
        raise


def _close_dxm03_context(playwright, browser) -> None:
    # Do not call browser.close() for the shared DXM03 Chrome; stopping
    # Playwright disconnects this client and leaves the logged-in profile alive.
    _ = browser
    try:
        playwright.stop()
    except Exception:
        pass


def fetch_dxm03_pairing_snapshot(
    skus: list[str],
    *,
    cdp_url: str | None = None,
) -> dict[str, dict[str, Any]]:
    clean_skus = [str(sku or "").strip() for sku in skus if str(sku or "").strip()]
    if not clean_skus:
        return {}
    url = cdp_url or dxm03_cdp_url()
    with browser_automation_lock(
        task_code="dxm03_mingkong_pairing_snapshot",
        timeout_seconds=120,
        command=",".join(clean_skus),
    ):
        playwright, browser, ctx = _open_dxm03_context(url)
        try:
            out: dict[str, dict[str, Any]] = {}
            for sku in clean_skus:
                commodity = _search_commodity(ctx, sku)
                components: list[dict[str, Any]] = []
                if commodity and commodity.get("is_combo"):
                    components = _search_child_sku_info(ctx, commodity.get("id") or "")
                    for component in components:
                        component["pairing"] = _search_pair(ctx, component["sku"])
                out[sku] = {
                    "commodity": commodity,
                    "pairing": _search_pair(ctx, sku),
                    "combo_components": components,
                }
            return out
        finally:
            _close_dxm03_context(playwright, browser)


def _selection_map(selections: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in selections or []:
        if not isinstance(item, dict):
            continue
        for key in ("dianxiaomi_sku", "shopify_variant_id"):
            value = str(item.get(key) or "").strip()
            if value:
                out[value] = item
    return out


def _local_sku_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "shopify_product_id": row.get("shopify_product_id") or "",
        "shopify_variant_id": row.get("shopify_variant_id") or "",
        "shopify_sku": row.get("shopify_sku") or "",
        "variant_title": row.get("shopify_variant_title") or row.get("variant_title") or "",
        "dianxiaomi_sku": row.get("dianxiaomi_sku") or "",
        "dianxiaomi_product_sku": row.get("dianxiaomi_product_sku") or "",
        "dianxiaomi_sku_code": row.get("dianxiaomi_sku_code") or "",
        "dianxiaomi_name": row.get("dianxiaomi_name") or "",
        "source": row.get("source") or "",
        "image_url": row.get("image_url") or "",
        "purchase_1688_url": row.get("purchase_1688_url") or "",
        "mingkong_product_id": row.get("mingkong_product_id"),
        "mingkong_variant_id": row.get("mingkong_variant_id"),
        "mingkong_procurement": row.get("mingkong_procurement"),
        "is_combo": bool(row.get("is_combo")),
        "combo_components": row.get("combo_components") or [],
    }


def load_mingkong_library_sku_rows(product: dict[str, Any]) -> dict[str, Any]:
    library_rows = mingkong_product_library.sku_rows_from_library(product)
    realtime_refresh_summary: dict[str, Any] | None = None
    if not library_rows:
        realtime_refresh_summary = mingkong_product_library.refresh_product_from_dxm02(product)
        library_rows = mingkong_product_library.sku_rows_from_library(product)
    rows = [_local_sku_payload(row) for row in library_rows]
    return {
        "rows": rows,
        "realtime_refresh": realtime_refresh_summary,
    }


def build_mingkong_library_sku_import_payload(product: dict[str, Any]) -> dict[str, Any]:
    loaded = load_mingkong_library_sku_rows(product)
    pairs: list[dict[str, Any]] = []
    for row in loaded["rows"]:
        variant_id = str(row.get("shopify_variant_id") or "").strip()
        dianxiaomi_sku = str(row.get("dianxiaomi_sku") or "").strip()
        if not variant_id:
            continue
        pairs.append({
            "shopify_product_id": row.get("shopify_product_id") or product.get("shopifyid") or "",
            "shopify_variant_id": variant_id,
            "shopify_sku": row.get("shopify_sku") or "",
            "shopify_currency": None,
            "shopify_variant_title": row.get("variant_title") or "",
            "dianxiaomi_sku": dianxiaomi_sku or None,
            "dianxiaomi_product_sku": row.get("dianxiaomi_product_sku") or None,
            "dianxiaomi_sku_code": row.get("dianxiaomi_sku_code") or None,
            "dianxiaomi_name": row.get("dianxiaomi_name") or None,
        })
    return {
        "ok": bool(pairs),
        "pairs": pairs,
        "items": loaded["rows"],
        "realtime_refresh": loaded["realtime_refresh"],
        "message": "" if pairs else "明空产品库未找到可同步的 SKU 行",
    }


def _mingkong_reference_indexes(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_variant: dict[str, dict[str, Any]] = {}
    by_sku: dict[str, dict[str, Any]] = {}
    for item in rows:
        row = _local_sku_payload(item)
        variant_id = str(row.get("shopify_variant_id") or "").strip()
        sku = str(row.get("dianxiaomi_sku") or "").strip()
        if variant_id:
            by_variant.setdefault(variant_id, row)
        if sku:
            by_sku.setdefault(sku, row)
    return by_variant, by_sku


def _mingkong_reference_payload(row: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, Any]:
    source = str(fallback.get("source") or "").strip()
    if row is None and source.startswith("mingkong"):
        row = fallback
    if not row:
        return {}
    proc = row.get("mingkong_procurement") or {}
    purchase_url = str(row.get("purchase_1688_url") or proc.get("purchase_1688_url") or "").strip()
    return {
        "shopify_product_id": row.get("shopify_product_id") or "",
        "shopify_variant_id": row.get("shopify_variant_id") or "",
        "variant_title": row.get("variant_title") or "",
        "sku": row.get("dianxiaomi_sku") or "",
        "product_sku": row.get("dianxiaomi_product_sku") or "",
        "sku_code": row.get("dianxiaomi_sku_code") or "",
        "name": row.get("dianxiaomi_name") or "",
        "supplier_name": proc.get("supplier_name") or "",
        "purchase_1688_url": purchase_url,
        "alibaba_product_id": proc.get("alibaba_product_id") or normalize_1688_offer_id(purchase_url),
        "sku_id_alibaba": proc.get("sku_id_alibaba") or "",
        "pairing_state": proc.get("pairing_state"),
        "is_combo": bool(row.get("is_combo")),
        "combo_components": row.get("combo_components") or [],
        "source": row.get("source") or source,
    }


def build_workbench_payload(
    product: dict[str, Any],
    sku_rows: list[dict[str, Any]],
    *,
    include_live: bool = True,
    include_mingkong_reference: bool = False,
    fetch_live_fn=fetch_dxm03_pairing_snapshot,
) -> dict[str, Any]:
    purchase_url = str(product.get("purchase_1688_url") or "").strip()
    source_rows = list(sku_rows or [])
    library_rows: list[dict[str, Any]] = []
    realtime_refresh_error = ""
    mingkong_reference_error = ""
    realtime_refresh_summary: dict[str, Any] | None = None
    if not source_rows:
        try:
            loaded_library = load_mingkong_library_sku_rows(product)
            library_rows = loaded_library["rows"]
            realtime_refresh_summary = loaded_library["realtime_refresh"]
            source_rows = library_rows
        except Exception as exc:
            realtime_refresh_error = str(exc)
            library_rows = []
    elif include_mingkong_reference:
        try:
            library_rows = [
                _local_sku_payload(row)
                for row in mingkong_product_library.sku_rows_from_library(product)
            ]
        except Exception as exc:
            mingkong_reference_error = str(exc)
            library_rows = []
    local_rows = [_local_sku_payload(row) for row in source_rows]
    mingkong_by_variant, mingkong_by_sku = _mingkong_reference_indexes(library_rows)
    sku_values = [row["dianxiaomi_sku"] for row in local_rows if row.get("dianxiaomi_sku")]
    live_error = ""
    snapshot: dict[str, dict[str, Any]] = {}
    if include_live and sku_values:
        try:
            snapshot = fetch_live_fn(sku_values)
        except Exception as exc:
            live_error = str(exc)
    items: list[dict[str, Any]] = []
    ready_count = 0
    paired_count = 0
    for row in local_rows:
        sku = row.get("dianxiaomi_sku") or ""
        live = snapshot.get(sku) or {}
        commodity = live.get("commodity")
        pairing = live.get("pairing")
        components = live.get("combo_components") or []
        mingkong_reference = (
            mingkong_by_variant.get(str(row.get("shopify_variant_id") or "").strip())
            or mingkong_by_sku.get(sku)
        )
        row_purchase_url = str(row.get("purchase_1688_url") or purchase_url).strip()
        row_alibaba_product_id = normalize_1688_offer_id(row_purchase_url)
        status = "missing_local_sku"
        if sku and commodity and commodity.get("is_combo") and components:
            all_components_paired = all(
                (component.get("pairing") or {}).get("is_paired")
                for component in components
            )
            if all_components_paired:
                status = "combo_components_paired"
                paired_count += 1
            else:
                status = "combo_components_incomplete"
        elif sku and commodity and pairing and pairing.get("is_paired"):
            status = "paired"
            paired_count += 1
        elif sku and commodity and pairing:
            status = "ready_to_confirm"
            ready_count += 1
        elif sku and commodity:
            status = "missing_pair_row"
        elif sku:
            status = "missing_dxm03_commodity"
        items.append({
            **row,
            "purchase_1688_url": row_purchase_url,
            "alibaba_product_id": row_alibaba_product_id,
            "image_url": row.get("image_url") or (commodity or {}).get("image_url") or "",
            "is_combo": bool((commodity or {}).get("is_combo") or row.get("is_combo")),
            "combo_components": components,
            "mingkong_procurement": row.get("mingkong_procurement"),
            "mingkong": _mingkong_reference_payload(mingkong_reference, row),
            "dxm03": {
                "commodity": commodity,
                "pairing": pairing,
            },
            "status": status,
        })
    return {
        "product": {
            "id": product.get("id"),
            "product_code": product.get("product_code") or "",
            "name": product.get("name") or "",
            "shopifyid": product.get("shopifyid") or "",
            "shopify_title": product.get("shopify_title") or "",
            "product_link": product.get("product_link") or "",
            "purchase_1688_url": purchase_url,
            "alibaba_product_id": normalize_1688_offer_id(purchase_url),
        },
        "items": items,
        "summary": {
            "sku_count": len(items),
            "ready_count": ready_count,
            "paired_count": paired_count,
            "missing_count": len(items) - ready_count - paired_count,
            "has_purchase_url": bool(purchase_url),
            "live_error": live_error or realtime_refresh_error,
            "mingkong_reference_error": mingkong_reference_error,
            "source": "media_product_skus" if sku_rows else ("mingkong_library" if library_rows else "empty"),
            "realtime_refresh": realtime_refresh_summary,
        },
    }


def confirm_dxm03_pairing(
    product: dict[str, Any],
    sku_rows: list[dict[str, Any]],
    *,
    selections: list[dict[str, Any]] | None = None,
    cdp_url: str | None = None,
) -> dict[str, Any]:
    purchase_url = str(product.get("purchase_1688_url") or "").strip()
    product_id_alibaba = normalize_1688_offer_id(purchase_url)
    source_rows = list(sku_rows or [])
    if not source_rows:
        source_rows = load_mingkong_library_sku_rows(product)["rows"]
    local_rows = [_local_sku_payload(row) for row in source_rows]
    rows_with_sku = [row for row in local_rows if row.get("dianxiaomi_sku")]
    if not rows_with_sku:
        return {
            "ok": False,
            "error": "missing_sku_rows",
            "message": "产品缺少可写入 DXM03 的 SKU 配对行",
            "items": [],
        }
    selection_by_key = _selection_map(selections)
    url = cdp_url or dxm03_cdp_url()
    results: list[dict[str, Any]] = []
    with browser_automation_lock(
        task_code="dxm03_mingkong_pairing_confirm",
        timeout_seconds=180,
        command=str(product.get("product_code") or product.get("id") or ""),
    ):
        playwright, browser, ctx = _open_dxm03_context(url)
        try:
            for row in rows_with_sku:
                sku = row["dianxiaomi_sku"]
                selection = (
                    selection_by_key.get(sku)
                    or selection_by_key.get(str(row.get("shopify_variant_id") or ""))
                    or {}
                )
                selected_product_id = (
                    normalize_1688_offer_id(selection.get("product_id_alibaba"))
                    or normalize_1688_offer_id(selection.get("purchase_1688_url"))
                    or normalize_1688_offer_id(row.get("purchase_1688_url"))
                    or product_id_alibaba
                )
                selected_purchase_url = _purchase_url_for_offer(
                    selected_product_id,
                    selection.get("purchase_1688_url") or row.get("purchase_1688_url") or purchase_url,
                )
                selected_sku_id = str(selection.get("sku_id_alibaba") or "").strip()
                item_result = {
                    **row,
                    "purchase_1688_url": selected_purchase_url,
                    "alibaba_product_id": selected_product_id,
                    "sku_id_alibaba": selected_sku_id,
                    "status": "pending",
                }
                try:
                    commodity = _search_commodity(ctx, sku)
                    item_result["commodity"] = commodity
                    if not commodity:
                        item_result.update({
                            "status": "blocked",
                            "error": "missing_dxm03_commodity",
                            "message": "DXM03 商品管理找不到该 SKU",
                        })
                        results.append(item_result)
                        continue
                    if commodity.get("is_combo"):
                        components = _search_child_sku_info(ctx, commodity.get("id") or "")
                        for component in components:
                            component["pairing"] = _search_pair(ctx, component["sku"])
                        item_result["combo_components"] = components
                        if components and all(
                            (component.get("pairing") or {}).get("is_paired")
                            for component in components
                        ):
                            item_result.update({
                                "status": "already_paired_combo_components",
                                "message": "组合 SKU 的组件采购配对已完整",
                            })
                        else:
                            item_result.update({
                                "status": "blocked",
                                "error": "combo_components_incomplete",
                                "message": "组合 SKU 需要先补齐组件 SKU 与组件采购配对",
                            })
                        results.append(item_result)
                        continue
                    if not selected_product_id:
                        item_result.update({
                            "status": "blocked",
                            "error": "missing_purchase_url",
                            "message": "产品缺少可识别的 1688 采购链接",
                        })
                        results.append(item_result)
                        continue
                    if (
                        selected_purchase_url
                        and commodity.get("id")
                        and commodity.get("source_url") != selected_purchase_url
                    ):
                        _update_source_url(ctx, commodity["id"], selected_purchase_url)
                        commodity = _search_commodity(ctx, sku) or commodity
                        item_result["commodity"] = commodity
                    pair = _search_pair(ctx, sku)
                    if not pair:
                        _trigger_source_sync(ctx, selected_purchase_url)
                        for _ in range(5):
                            time.sleep(1)
                            pair = _search_pair(ctx, sku)
                            if pair:
                                break
                    item_result["pairing_before"] = pair
                    if not pair or not pair.get("pair_row_id"):
                        item_result.update({
                            "status": "blocked",
                            "error": "missing_pair_row",
                            "message": "DXM03 1688 商品配对列表未生成待配对行",
                        })
                        results.append(item_result)
                        continue
                    existing_sku_id = str(pair.get("sku_id_alibaba") or "").strip()
                    if pair.get("is_paired") and (
                        not selected_sku_id or selected_sku_id == existing_sku_id
                    ):
                        item_result.update({
                            "status": "already_paired",
                            "sku_id_alibaba": existing_sku_id,
                            "pairing_after": pair,
                        })
                        results.append(item_result)
                        continue
                    if not selected_sku_id:
                        item_result.update({
                            "status": "blocked",
                            "error": "missing_sku_id_alibaba",
                            "message": "缺少人工确认的 1688 SKU ID",
                        })
                        results.append(item_result)
                        continue
                    _check_pair(ctx, selected_product_id, selected_sku_id)
                    _confirm_pair(
                        ctx,
                        pair_row_id=pair["pair_row_id"],
                        product_id_alibaba=selected_product_id,
                        sku_id_alibaba=selected_sku_id,
                    )
                    item_result["pairing_after"] = _search_pair(ctx, sku)
                    item_result["status"] = "confirmed"
                    results.append(item_result)
                except Exception as exc:
                    item_result.update({
                        "status": "error",
                        "error": "dxm03_write_failed",
                        "message": str(exc),
                    })
                    results.append(item_result)
        finally:
            _close_dxm03_context(playwright, browser)
    ok = bool(results) and all(
        item.get("status") in {
            "already_paired",
            "already_paired_combo_components",
            "confirmed",
        }
        for item in results
    )
    return {
        "ok": ok,
        "product_id": product.get("id"),
        "product_code": product.get("product_code") or "",
        "items": results,
    }
