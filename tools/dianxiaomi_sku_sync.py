"""店小秘 SKU 配对同步：

每天把店小秘 Shopify 在线商品库的 variants 和"商品管理"里的 ERP 商品做配对，
回填 media_products.shopify_title 和 media_product_skus（每个 variant ↔ ERP SKU
一行）。

- 入口与 tools/shopifyid_dianxiaomi_sync.py 完全独立（不同 task_code、不同 timer）。
- 共用其浏览器/SSH/MySQL 等基础设施函数（直接 import，不修改原文件）。
- pure 采集与配对函数同时供 web 路由的"单产品手动刷新"复用。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from tools.shopifyid_dianxiaomi_sync import (
    BROWSER_MODES,
    CDP_URL,
    DB_MODES,
    REMOTE_ENVS,
    REPO_ROOT,
    SERVER_BROWSER_CDP_URL,
    _open_browser_context,
    _run_mysql,
    _sql_quote,
    build_scheduled_task_runs_table_sql,
    ensure_dianxiaomi_success,
    resolve_browser_mode,
    resolve_db_mode,
)


SHOPIFY_ONLINE_URL = "https://www.dianxiaomi.com/web/shopifyProduct/online"
SHOPIFY_API_URL = "https://www.dianxiaomi.com/api/shopifyProduct/pageList.json"
DXM_PRODUCT_PAGE_URL = "https://www.dianxiaomi.com/web/dxmCommodityProduct/index"
DXM_PRODUCT_API_URL = "https://www.dianxiaomi.com/api/dxmCommodityProduct/pageList.json"

OUTPUT_DIR = REPO_ROOT / "output" / "dianxiaomi_sku_sync"
TASK_CODE = "dianxiaomi_sku"
TASK_NAME = "店小秘 SKU 配对同步"

REMOTE_MEDIA_PRODUCTS_TABLE = "media_products"
REMOTE_SKU_PAIR_TABLE = "media_product_skus"


# Shopify 在线商品库默认查询参数，与浏览器 UI 默认一致。
SHOPIFY_DEFAULT_PAYLOAD = {
    "sortName": 2,
    "pageSize": 100,
    "total": 0,
    "sortValue": 0,
    "searchType": 1,
    "searchValue": "",
    "productSearchType": 0,
    "sellType": 0,
    "listingStatus": "Active",
    "shopId": "-1",
    "dxmState": "online",
    "dxmOfflineState": "",
    "fullCid": "",
}

# 店小秘"商品管理"页面默认参数（来自 DevTools 抓包，对应"全部商品"过滤态）。
DXM_DEFAULT_PAYLOAD = {
    "pageNo": 1,
    "pageSize": 100,
    "searchType": 1,
    "searchValue": "",
    "saleMode": -1,
    "productMode": -1,
    "productPxId": 1,
    "productPxSxId": 0,
    "fullCid": "",
    "productSearchType": 1,
    "productGroupLxId": 1,
}


# ----------------------------------------------------------------------
# 纯函数（pure）：构造请求、解析响应、配对
# ----------------------------------------------------------------------


def _normalize_decimal(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_weight_grams(weight: Any, unit: Any) -> float | None:
    """Shopify variant.weight 默认单位由 weighUnit 决定（kg/g/oz/lb）。统一归一到 g。"""
    w = _normalize_decimal(weight)
    if w is None:
        return None
    code = str(unit or "").strip().lower()
    multipliers = {"kg": 1000.0, "g": 1.0, "oz": 28.3495, "lb": 453.592}
    factor = multipliers.get(code, 1000.0)  # 没有单位就当 kg（与店小秘默认一致）
    return round(w * factor, 2)


def build_shopify_payload(page_no: int, **overrides: Any) -> dict[str, Any]:
    payload = dict(SHOPIFY_DEFAULT_PAYLOAD)
    payload["pageNo"] = page_no
    payload.update(overrides)
    return payload


def build_dxm_payload(page_no: int, **overrides: Any) -> dict[str, Any]:
    payload = dict(DXM_DEFAULT_PAYLOAD)
    payload["pageNo"] = page_no
    payload.update(overrides)
    return payload


def extract_shopify_products(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从 shopifyProduct/pageList.json 响应里抽 product + variants。"""
    page = ((payload.get("data") or {}).get("page") or {})
    products: list[dict[str, Any]] = []
    for item in page.get("list") or []:
        if not isinstance(item, dict):
            continue
        shopify_product_id = str(item.get("shopifyProductId") or "").strip()
        handle = str(item.get("handle") or "").strip()
        title = str(item.get("title") or "").strip()
        shop_id = str(item.get("shopId") or "").strip()
        variants_raw = item.get("variants") or []
        variants: list[dict[str, Any]] = []
        for v in variants_raw:
            if not isinstance(v, dict):
                continue
            variant_id = str(v.get("shopifyVariantId") or "").strip()
            if not variant_id:
                continue
            sku_raw = v.get("sku")
            sku = str(sku_raw or "").strip()
            option_parts: list[str] = []
            for key in ("option1", "option2", "option3"):
                value = v.get(key)
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    option_parts.append(text)
            variants.append({
                "shopify_variant_id": variant_id,
                "shopify_sku": sku or None,
                "shopify_price": _normalize_decimal(v.get("price")),
                "shopify_compare_at_price": _normalize_decimal(v.get("compareAtPrice")),
                "shopify_inventory_quantity": _normalize_int(v.get("inventoryQuantity")),
                "shopify_weight_grams": _normalize_weight_grams(v.get("weight"), v.get("weighUnit")),
                "shopify_variant_title": " / ".join(option_parts) if option_parts else None,
                # pair_key = sku 优先，否则用 variant_id（店小秘 ERP 的 sku 字段就是这么生成的）
                "pair_key": sku or variant_id,
            })
        products.append({
            "shopify_product_id": shopify_product_id,
            "shopify_handle": handle,
            "shopify_title": title,
            "shop_id": shop_id,
            "variants": variants,
        })
    return products


def extract_dxm_index(payload: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    """从 dxmCommodityProduct/pageList.json 响应里抽 ERP 商品索引：sku -> {sku_code, name, name_en}。"""
    page = ((payload.get("data") or {}).get("page") or {})
    index: dict[str, dict[str, str | None]] = {}
    for group in page.get("list") or []:
        if not isinstance(group, dict):
            continue
        for prod in group.get("dxmCommodityProductList") or []:
            if not isinstance(prod, dict):
                continue
            dxm_sku = str(prod.get("sku") or "").strip()
            if not dxm_sku:
                continue
            sku_code = str(prod.get("skuCode") or "").strip() or None
            name_cn = str(prod.get("name") or "").strip() or None
            name_en = str(prod.get("nameEn") or "").strip() or None
            relation_flag = bool(prod.get("relationFlag"))
            existing = index.get(dxm_sku)
            if existing and not relation_flag:
                # 已经收过一条带 relationFlag=True 的；保留更可信的那条
                continue
            index[dxm_sku] = {
                "dianxiaomi_sku": dxm_sku,
                "dianxiaomi_sku_code": sku_code,
                "dianxiaomi_name": name_cn or name_en,
                "relation_flag": relation_flag,
            }
    return index


def build_pair_rows(
    shopify_products: list[dict[str, Any]],
    dxm_index: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """对每个 Shopify product，把它的 variants 与 dxm_index 合并成可入库的 pair 行。

    返回 {shopify_product_id: [{shopify_product_id, shopify_variant_id, shopify_sku,
        shopify_variant_title, dianxiaomi_sku, dianxiaomi_sku_code, dianxiaomi_name}]}.
    没有 dxm 配对的 variant 也会保留，dianxiaomi_* 字段为空。
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for product in shopify_products:
        spid = product.get("shopify_product_id") or ""
        if not spid:
            continue
        rows: list[dict[str, Any]] = []
        for variant in product.get("variants") or []:
            pair_key = variant.get("pair_key") or ""
            dxm_match = dxm_index.get(pair_key) if pair_key else None
            rows.append({
                "shopify_product_id": spid,
                "shopify_variant_id": variant.get("shopify_variant_id"),
                "shopify_sku": variant.get("shopify_sku"),
                "shopify_price": variant.get("shopify_price"),
                "shopify_compare_at_price": variant.get("shopify_compare_at_price"),
                "shopify_inventory_quantity": variant.get("shopify_inventory_quantity"),
                "shopify_weight_grams": variant.get("shopify_weight_grams"),
                "shopify_variant_title": variant.get("shopify_variant_title"),
                "dianxiaomi_sku": (dxm_match or {}).get("dianxiaomi_sku") or pair_key,
                "dianxiaomi_sku_code": (dxm_match or {}).get("dianxiaomi_sku_code"),
                "dianxiaomi_name": (dxm_match or {}).get("dianxiaomi_name"),
            })
        grouped[spid] = rows
    return grouped


def fetch_all_pages(
    fetch_page: Callable[[int], dict[str, Any]],
    *,
    extract_total_page: Callable[[dict[str, Any]], int],
    extract_items: Callable[[dict[str, Any]], list[Any]],
) -> tuple[dict[str, Any], list[Any]]:
    """通用分页拉取：按 page_no 逐页 fetch_page，直到 total_page 全拉完。"""
    first = fetch_page(1)
    ensure_dianxiaomi_success(first)
    total_page = extract_total_page(first)
    items: list[Any] = list(extract_items(first))
    for page_no in range(2, total_page + 1):
        payload = fetch_page(page_no)
        ensure_dianxiaomi_success(payload)
        items.extend(extract_items(payload))
    return first, items


def _shopify_total_page(payload: dict[str, Any]) -> int:
    return int(((payload.get("data") or {}).get("page") or {}).get("totalPage") or 0)


def _dxm_total_page(payload: dict[str, Any]) -> int:
    return int(((payload.get("data") or {}).get("page") or {}).get("totalPage") or 0)


def _shopify_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return ((payload.get("data") or {}).get("page") or {}).get("list") or []


def _dxm_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return ((payload.get("data") or {}).get("page") or {}).get("list") or []


# ----------------------------------------------------------------------
# 远端 MySQL：建表 + 读写 media_products / media_product_skus
# ----------------------------------------------------------------------


def build_remote_ensure_table_sql() -> str:
    return (
        "CREATE TABLE IF NOT EXISTS media_product_skus ("
        "id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        "product_id BIGINT UNSIGNED NOT NULL,"
        "shopify_product_id VARCHAR(32) NULL,"
        "shopify_variant_id VARCHAR(32) NULL,"
        "shopify_sku VARCHAR(128) NULL,"
        "shopify_price DECIMAL(12,2) NULL,"
        "shopify_compare_at_price DECIMAL(12,2) NULL,"
        "shopify_currency VARCHAR(8) NULL,"
        "shopify_inventory_quantity INT NULL,"
        "shopify_weight_grams DECIMAL(10,2) NULL,"
        "shopify_variant_title VARCHAR(512) NULL,"
        "dianxiaomi_sku VARCHAR(128) NULL,"
        "dianxiaomi_sku_code VARCHAR(64) NULL,"
        "dianxiaomi_name VARCHAR(512) NULL,"
        "source VARCHAR(32) NULL,"
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "UNIQUE KEY uk_media_product_skus_pid_variant (product_id, shopify_variant_id),"
        "KEY idx_media_product_skus_product (product_id),"
        "KEY idx_media_product_skus_dxm_sku (dianxiaomi_sku),"
        "KEY idx_media_product_skus_dxm_code (dianxiaomi_sku_code),"
        "KEY idx_media_product_skus_shopify_sku (shopify_sku)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"
    )


def build_remote_add_shopify_title_sql() -> str:
    return (
        "SET @ddl := IF("
        "EXISTS(SELECT 1 FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME='media_products' "
        "AND COLUMN_NAME='shopify_title'),"
        "'SELECT 1',"
        "'ALTER TABLE media_products ADD COLUMN shopify_title VARCHAR(512) NULL AFTER shopifyid');"
        "PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;\n"
    )


def build_remote_select_products_sql() -> str:
    return (
        "SELECT id, IFNULL(shopifyid, '') AS shopifyid, IFNULL(shopify_title, '') AS shopify_title "
        "FROM media_products "
        "WHERE deleted_at IS NULL AND shopifyid IS NOT NULL AND shopifyid <> '' "
        "ORDER BY id ASC;\n"
    )


def parse_remote_products_tsv(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        if len(parts) < 3:
            raise ValueError(f"远端 media_products TSV 行格式不正确：{raw_line!r}")
        product_id_text, shopifyid, shopify_title = parts[:3]
        rows.append({
            "id": int(product_id_text.strip()),
            "shopifyid": shopifyid.strip() or "",
            "shopify_title": shopify_title.strip() or "",
        })
    return rows


def build_remote_apply_sql(
    *,
    title_updates: list[tuple[int, str | None]],
    sku_replacements: list[tuple[int, list[dict[str, Any]]]],
    source: str = "auto",
) -> str:
    """对每个产品先 DELETE 旧 SKU 配对再 INSERT 新行；同时刷新 shopify_title。

    所有语句包在一个事务里，便于 mysql 子进程整批跑。
    """
    lines: list[str] = ["START TRANSACTION;"]
    for product_id, title in title_updates:
        lines.append(
            f"UPDATE media_products SET shopify_title={_sql_quote(title)} "
            f"WHERE id={int(product_id)} AND deleted_at IS NULL;"
        )
    for product_id, pairs in sku_replacements:
        lines.append(
            f"DELETE FROM media_product_skus WHERE product_id={int(product_id)};"
        )
        for pair in pairs:
            def num(value):
                return "NULL" if value is None else str(value)
            lines.append(
                "INSERT INTO media_product_skus "
                "(product_id, shopify_product_id, shopify_variant_id, shopify_sku, "
                " shopify_price, shopify_compare_at_price, shopify_inventory_quantity, "
                " shopify_weight_grams, shopify_variant_title, dianxiaomi_sku, "
                " dianxiaomi_sku_code, dianxiaomi_name, source) VALUES ("
                f"{int(product_id)}, {_sql_quote(pair.get('shopify_product_id'))}, "
                f"{_sql_quote(pair.get('shopify_variant_id'))}, "
                f"{_sql_quote(pair.get('shopify_sku'))}, "
                f"{num(pair.get('shopify_price'))}, "
                f"{num(pair.get('shopify_compare_at_price'))}, "
                f"{num(pair.get('shopify_inventory_quantity'))}, "
                f"{num(pair.get('shopify_weight_grams'))}, "
                f"{_sql_quote(pair.get('shopify_variant_title'))}, "
                f"{_sql_quote(pair.get('dianxiaomi_sku'))}, "
                f"{_sql_quote(pair.get('dianxiaomi_sku_code'))}, "
                f"{_sql_quote(pair.get('dianxiaomi_name'))}, "
                f"{_sql_quote(source)});"
            )
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# 综合：构造同步计划 & 执行
# ----------------------------------------------------------------------


def plan_sync(
    *,
    shopify_products: list[dict[str, Any]],
    dxm_index: dict[str, dict[str, Any]],
    local_products: list[dict[str, Any]],
) -> dict[str, Any]:
    pair_index = build_pair_rows(shopify_products, dxm_index)
    title_index = {
        str(p.get("shopify_product_id") or ""): p.get("shopify_title") or ""
        for p in shopify_products
        if p.get("shopify_product_id")
    }

    title_updates: list[tuple[int, str | None]] = []
    sku_replacements: list[tuple[int, list[dict[str, Any]]]] = []
    matched_products: list[dict[str, Any]] = []
    unmatched_local: list[dict[str, Any]] = []

    for row in local_products:
        spid = str(row.get("shopifyid") or "").strip()
        if not spid:
            continue
        pairs = pair_index.get(spid)
        if pairs is None:
            unmatched_local.append({
                "id": row.get("id"),
                "shopifyid": spid,
                "status": "missing_in_shopify_pagelist",
            })
            continue
        new_title = title_index.get(spid) or None
        existing_title = (row.get("shopify_title") or "").strip()
        if (new_title or "") != existing_title:
            title_updates.append((int(row["id"]), new_title))
        sku_replacements.append((int(row["id"]), pairs))
        matched_products.append({
            "id": int(row["id"]),
            "shopifyid": spid,
            "variants": len(pairs),
            "matched_dxm": sum(1 for p in pairs if p.get("dianxiaomi_sku_code")),
        })

    matched_pair_total = sum(len(pairs) for _, pairs in sku_replacements)
    matched_with_dxm = sum(
        1 for _, pairs in sku_replacements for p in pairs if p.get("dianxiaomi_sku_code")
    )

    return {
        "title_updates": title_updates,
        "sku_replacements": sku_replacements,
        "matched_products": matched_products,
        "unmatched_local": unmatched_local,
        "summary": {
            "shopify_products_fetched": len(shopify_products),
            "dxm_skus_fetched": len(dxm_index),
            "local_products_with_shopifyid": len(local_products),
            "matched_local_products": len(matched_products),
            "unmatched_local_products": len(unmatched_local),
            "title_updates": len(title_updates),
            "matched_variant_pairs": matched_pair_total,
            "matched_variant_pairs_with_dxm": matched_with_dxm,
        },
    }


def run_sync(
    *,
    fetch_shopify_page: Callable[[int], dict[str, Any]],
    fetch_dxm_page: Callable[[int], dict[str, Any]],
    fetch_local_products: Callable[[], list[dict[str, Any]]],
    apply_changes: Callable[[dict[str, Any]], None],
    output_dir: Path,
    now_text: str | None = None,
) -> dict[str, Any]:
    _, shopify_items = fetch_all_pages(
        fetch_shopify_page,
        extract_total_page=_shopify_total_page,
        extract_items=_shopify_items,
    )
    shopify_products: list[dict[str, Any]] = []
    for raw in shopify_items:
        shopify_products.extend(extract_shopify_products({"data": {"page": {"list": [raw]}}}))

    _, dxm_items = fetch_all_pages(
        fetch_dxm_page,
        extract_total_page=_dxm_total_page,
        extract_items=_dxm_items,
    )
    dxm_index: dict[str, dict[str, Any]] = {}
    for raw_group in dxm_items:
        merged = extract_dxm_index({"data": {"page": {"list": [raw_group]}}})
        # Merge: prefer relationFlag=True; else first seen
        for sku, info in merged.items():
            existing = dxm_index.get(sku)
            if existing and existing.get("relation_flag") and not info.get("relation_flag"):
                continue
            dxm_index[sku] = info

    local_products = fetch_local_products()
    plan = plan_sync(
        shopify_products=shopify_products,
        dxm_index=dxm_index,
        local_products=local_products,
    )
    if plan["title_updates"] or plan["sku_replacements"]:
        apply_changes(plan)

    report = {
        "summary": plan["summary"],
        "matched_products": plan["matched_products"],
        "unmatched_local": plan["unmatched_local"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_text or _now_text()
    output_file = output_dir / f"dianxiaomi-sku-sync-{stamp}.json"
    output_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_file"] = str(output_file)
    return report


# ----------------------------------------------------------------------
# 浏览器层（CDP）
# ----------------------------------------------------------------------


def _fetch_via_browser(page, api_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = page.evaluate(
        """
        async ({ apiUrl, payload }) => {
          const body = new URLSearchParams();
          for (const [key, value] of Object.entries(payload)) {
            body.append(key, String(value ?? ""));
          }
          const response = await fetch(apiUrl, {
            method: "POST",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
              "X-Requested-With": "XMLHttpRequest",
            },
            credentials: "include",
            body: body.toString(),
          });
          const text = await response.text();
          return { ok: response.ok, status: response.status, text };
        }
        """,
        {"apiUrl": api_url, "payload": payload},
    )
    if not result.get("ok"):
        raise RuntimeError(f"店小秘接口请求失败：HTTP {result.get('status')}")
    text = str(result.get("text") or "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"店小秘接口返回了非 JSON 内容：{text[:200]}") from exc


# ----------------------------------------------------------------------
# Web 入口：复用现有 CDP 浏览器拉一次全量数据（不关闭浏览器）
# ----------------------------------------------------------------------


def fetch_shopify_and_dxm_via_cdp(
    cdp_url: str = SERVER_BROWSER_CDP_URL,
    *,
    timeout_seconds: int = 60,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """连接到已经登录店小秘的常驻 CDP 浏览器，拉一次全量 shopify + dxm 数据。

    供 web 路由（"刷新 SKU/英文名"按钮）使用。函数**不会**关闭浏览器进程，
    避免破坏 shopifyid_dianxiaomi_sync.py 同享的登录态。

    分页拉取的耗时由 timeout_seconds 控制（playwright 的 evaluate 会在浏览器
    端拉接口）。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        try:
            deadline = time.time() + 5
            while time.time() < deadline and not browser.contexts:
                time.sleep(0.2)
            if not browser.contexts:
                raise RuntimeError("connected to CDP browser but no context is available")
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(timeout_seconds * 1000)

            page.goto(SHOPIFY_ONLINE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(600)

            def _shopify_fetch(page_no: int) -> dict[str, Any]:
                return _fetch_via_browser(page, SHOPIFY_API_URL, build_shopify_payload(page_no))

            _, shopify_items = fetch_all_pages(
                _shopify_fetch,
                extract_total_page=_shopify_total_page,
                extract_items=_shopify_items,
            )
            shopify_products: list[dict[str, Any]] = []
            for raw in shopify_items:
                shopify_products.extend(extract_shopify_products({"data": {"page": {"list": [raw]}}}))

            page.goto(DXM_PRODUCT_PAGE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(600)

            def _dxm_fetch(page_no: int) -> dict[str, Any]:
                return _fetch_via_browser(page, DXM_PRODUCT_API_URL, build_dxm_payload(page_no))

            _, dxm_items = fetch_all_pages(
                _dxm_fetch,
                extract_total_page=_dxm_total_page,
                extract_items=_dxm_items,
            )
            dxm_index: dict[str, dict[str, Any]] = {}
            for raw_group in dxm_items:
                merged = extract_dxm_index({"data": {"page": {"list": [raw_group]}}})
                for sku, info in merged.items():
                    existing = dxm_index.get(sku)
                    if existing and existing.get("relation_flag") and not info.get("relation_flag"):
                        continue
                    dxm_index[sku] = info
        finally:
            # 不要 browser.close()：常驻 chrome 的登录态需要保留给定时任务复用。
            # disconnect 通过 sync_playwright() 的 __exit__ 自动完成。
            pass

    return shopify_products, dxm_index


# ----------------------------------------------------------------------
# 任务运行登记
# ----------------------------------------------------------------------


def _now_text() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _ensure_scheduled_runs_table(db_name: str, *, db_mode: str) -> None:
    _run_mysql(build_scheduled_task_runs_table_sql(), db_name, db_mode=db_mode)


def _start_run(db_name: str, *, db_mode: str) -> int:
    _ensure_scheduled_runs_table(db_name, db_mode=db_mode)
    sql = (
        "INSERT INTO scheduled_task_runs (task_code, task_name, status, started_at) "
        f"VALUES ({_sql_quote(TASK_CODE)}, {_sql_quote(TASK_NAME)}, 'running', NOW());\n"
        "SELECT LAST_INSERT_ID();\n"
    )
    output = _run_mysql(sql, db_name, db_mode=db_mode).strip()
    for line in reversed(output.splitlines()):
        text = line.strip()
        if text.isdigit():
            return int(text)
    raise RuntimeError(f"无法读取定时任务运行记录 ID：{output!r}")


def _finish_run(
    db_name: str,
    run_id: int,
    *,
    db_mode: str,
    status: str,
    summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    output_file: str | None = None,
) -> None:
    summary_sql = (
        _sql_quote(json.dumps(summary, ensure_ascii=False)) if summary is not None else "NULL"
    )
    sql = (
        "UPDATE scheduled_task_runs SET "
        f"status={_sql_quote(status)}, finished_at=NOW(), "
        "duration_seconds=TIMESTAMPDIFF(SECOND, started_at, NOW()), "
        f"summary_json={summary_sql}, "
        f"error_message={_sql_quote(error_message)}, "
        f"output_file={_sql_quote(output_file)} "
        f"WHERE id={int(run_id)};\n"
    )
    _run_mysql(sql, db_name, db_mode=db_mode)


# ----------------------------------------------------------------------
# CLI 主流程
# ----------------------------------------------------------------------


def _print_report(report: dict[str, Any], *, remote_label: str, db_name: str) -> None:
    s = report.get("summary") or {}
    print("店小秘 SKU 配对同步完成：")
    print(f"  目标库: {remote_label} / {db_name}")
    print(f"  Shopify 在线商品: {s.get('shopify_products_fetched')}")
    print(f"  店小秘 ERP SKU: {s.get('dxm_skus_fetched')}")
    print(f"  本地待同步 product (有 shopifyid): {s.get('local_products_with_shopifyid')}")
    print(f"  命中本地: {s.get('matched_local_products')} （未匹配 {s.get('unmatched_local_products')}）")
    print(f"  写入 variant 配对: {s.get('matched_variant_pairs')} （含 ERP 编码: {s.get('matched_variant_pairs_with_dxm')}）")
    print(f"  英文名更新: {s.get('title_updates')}")
    print(f"  日志: {report.get('output_file')}")


def _run_main_impl(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="店小秘 SKU 配对同步：回填 media_products.shopify_title 与 media_product_skus")
    parser.add_argument("--env", choices=sorted(REMOTE_ENVS.keys()), default="prod")
    parser.add_argument("--skip-login-prompt", action="store_true")
    parser.add_argument("--browser-mode", choices=BROWSER_MODES, default=os.environ.get("DXM_SKU_BROWSER_MODE", "auto"))
    parser.add_argument("--browser-cdp-url", default=os.environ.get("DXM_SKU_BROWSER_CDP_URL", SERVER_BROWSER_CDP_URL))
    parser.add_argument("--db-mode", choices=DB_MODES, default=os.environ.get("DXM_SKU_DB_MODE", "auto"))
    args = parser.parse_args(argv)
    db_name = str(REMOTE_ENVS[args.env]["db_name"])
    remote_label = str(REMOTE_ENVS[args.env]["label"])
    db_mode = resolve_db_mode(args.db_mode)

    # 确保远端表/字段已就绪（线上是先跑 SQL 迁移，但这里幂等检查一遍兜底）。
    _run_mysql(build_remote_add_shopify_title_sql(), db_name, db_mode=db_mode)
    _run_mysql(build_remote_ensure_table_sql(), db_name, db_mode=db_mode)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser_process, browser, context, _ = _open_browser_context(
            playwright,
            browser_mode=args.browser_mode,
            cdp_url=args.browser_cdp_url,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(SHOPIFY_ONLINE_URL, wait_until="domcontentloaded")
            print(f"已打开店小秘页面：{SHOPIFY_ONLINE_URL}")
            if not args.skip_login_prompt:
                input("如果还没登录，请先登录店小秘；登录完成后按回车继续...")
                page.goto(SHOPIFY_ONLINE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)

            def fetch_shopify_page(page_no: int) -> dict[str, Any]:
                return _fetch_via_browser(page, SHOPIFY_API_URL, build_shopify_payload(page_no))

            # 切到商品管理页（不强制，只是更直观）
            page.goto(DXM_PRODUCT_PAGE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)

            def fetch_dxm_page(page_no: int) -> dict[str, Any]:
                return _fetch_via_browser(page, DXM_PRODUCT_API_URL, build_dxm_payload(page_no))

            def fetch_local_products() -> list[dict[str, Any]]:
                output = _run_mysql(build_remote_select_products_sql(), db_name, db_mode=db_mode)
                return parse_remote_products_tsv(output)

            def apply_changes(plan: dict[str, Any]) -> None:
                sql = build_remote_apply_sql(
                    title_updates=plan["title_updates"],
                    sku_replacements=plan["sku_replacements"],
                )
                if sql.strip().endswith("COMMIT;"):
                    _run_mysql(sql, db_name, db_mode=db_mode)

            report = run_sync(
                fetch_shopify_page=fetch_shopify_page,
                fetch_dxm_page=fetch_dxm_page,
                fetch_local_products=fetch_local_products,
                apply_changes=apply_changes,
                output_dir=OUTPUT_DIR,
            )
        finally:
            browser.close()
            if browser_process is not None and browser_process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(browser_process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                )

    _print_report(report, remote_label=remote_label, db_name=db_name)
    return 0, report, db_name, db_mode


def _parse_task_record_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env", choices=sorted(REMOTE_ENVS.keys()), default="prod")
    parser.add_argument("--db-mode", choices=DB_MODES, default=os.environ.get("DXM_SKU_DB_MODE", "auto"))
    args, _ = parser.parse_known_args(argv)
    return args


def main(argv: list[str] | None = None) -> int:
    record_args = _parse_task_record_args(argv)
    db_name = str(REMOTE_ENVS[record_args.env]["db_name"])
    db_mode = resolve_db_mode(record_args.db_mode)
    run_id = _start_run(db_name, db_mode=db_mode)
    try:
        exit_code, report, _db_name, final_db_mode = _run_main_impl(argv)
    except Exception as exc:
        _finish_run(db_name, run_id, db_mode=db_mode, status="failed", error_message=str(exc))
        raise
    _finish_run(
        db_name,
        run_id,
        db_mode=final_db_mode,
        status="success",
        summary=report.get("summary"),
        output_file=str(report.get("output_file") or ""),
    )
    return exit_code


if __name__ == "__main__":  # pragma: no cover - manual execution entrypoint
    raise SystemExit(main())
