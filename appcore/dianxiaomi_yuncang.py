from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from typing import Any

from bs4 import BeautifulSoup

from appcore.browser_automation_lock import browser_automation_lock
from appcore.db import execute, get_conn, query, query_one


log = logging.getLogger(__name__)

DEFAULT_CDP_URL = "http://127.0.0.1:9225"
DXM_BASE_URL = "https://www.dianxiaomi.com"
YUNCANG_PAGE_URL = "https://www.dianxiaomi.com/yuncangWarehouseSku/index.htm"
YUNCANG_PAGE_LIST_PATH = "/yuncangWarehouseSku/pageList.htm"
YUNCANG_CHOOSE_GOODS_PATH = "/dxmCommodityProduct/getPageForYuncang.htm"
YUNCANG_ADD_SKU_PATH = "/yuncangWarehouseSku/addSku.json"
YUNCANG_TABLE = "dianxiaomi_yuncang_skus"


class DianxiaomiYuncangError(RuntimeError):
    pass


def dxm03_cdp_url() -> str:
    return (
        os.getenv("DXM03_DIANXIAOMI_CDP_URL")
        or os.getenv("DIANXIAOMI_DXM03_CDP_URL")
        or DEFAULT_CDP_URL
    )


def _run_playwright_operation(
    label: str,
    operation,
    *,
    force_isolated_thread: bool | None = None,
) -> Any:
    use_isolated_thread = True if force_isolated_thread is None else bool(force_isolated_thread)
    if not use_isolated_thread:
        return operation()
    log.info("%s: running Playwright sync operation on a worker thread", label)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="dxm03-yuncang") as executor:
        return executor.submit(operation).result()


def _norm(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _to_int(text: str | None) -> int | None:
    if text is None:
        return None
    value = _norm(text)
    if not value or value in ("--", "-"):
        return None
    try:
        return int(value.replace(",", ""))
    except ValueError:
        return None


def _to_decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    value = _norm(text)
    if not value or value in ("--", "-"):
        return None
    try:
        return Decimal(value.replace(",", ""))
    except (ValueError, ArithmeticError):
        return None


def _normalize_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("/"):
        return f"{DXM_BASE_URL}{text}"
    return text


def _is_placeholder_image(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or "static/img/kong.png" in text


def _response_json(response, action: str) -> dict[str, Any]:
    text = response.text()
    if response.status >= 400:
        raise DianxiaomiYuncangError(f"{action} HTTP {response.status}: {text[:200]}")
    try:
        data = response.json()
    except Exception as exc:
        raise DianxiaomiYuncangError(f"{action} returned non-JSON: {text[:200]}") from exc
    if not isinstance(data, dict):
        raise DianxiaomiYuncangError(f"{action} returned invalid JSON payload")
    return data


def _ensure_success(data: dict[str, Any], action: str) -> None:
    code = data.get("code")
    if code not in (0, "0", None):
        raise DianxiaomiYuncangError(
            f"{action} failed: {data.get('msg') or data.get('message') or code}"
        )


def _column_exists(column: str) -> bool:
    row = query_one(
        "SELECT 1 AS ok FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (YUNCANG_TABLE, column),
    )
    return bool(row)


def _ensure_column(column: str, ddl: str) -> None:
    if not _column_exists(column):
        execute(f"ALTER TABLE {YUNCANG_TABLE} ADD COLUMN {ddl}")


def ensure_table() -> None:
    execute(
        f"""
        CREATE TABLE IF NOT EXISTS {YUNCANG_TABLE} (
          sku VARCHAR(128) NOT NULL PRIMARY KEY,
          sku_code VARCHAR(128) DEFAULT NULL,
          goods_name VARCHAR(500) DEFAULT NULL,
          stock_available INT DEFAULT 0,
          unit_price DECIMAL(10,2) DEFAULT NULL,
          raw_json JSON DEFAULT NULL,
          synced_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )
    _ensure_column("standalone_price_sku", "standalone_price_sku DECIMAL(10,2) NULL AFTER unit_price")
    _ensure_column(
        "standalone_shipping_fee_sku",
        "standalone_shipping_fee_sku DECIMAL(10,2) NULL AFTER standalone_price_sku",
    )
    _ensure_column(
        "packet_cost_actual_sku",
        "packet_cost_actual_sku DECIMAL(12,2) NULL AFTER standalone_shipping_fee_sku",
    )
    _ensure_column("sku_orders_count", "sku_orders_count INT NULL AFTER packet_cost_actual_sku")


def parse_yuncang_page_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    table = None
    for candidate in soup.find_all("table"):
        headers = [_norm(th.get_text()) for th in candidate.find_all("th")]
        if "商品信息" in headers:
            table = candidate
            break
    if table is None:
        return []

    items: list[dict[str, Any]] = []
    for row in table.find_all("tr", class_="content"):
        tds = row.find_all("td", recursive=False)
        if len(tds) < 9:
            continue

        info_td = tds[1]
        copy_divs = info_td.select(".copyDataContentText")
        goods_name = ""
        sku_code = ""
        if len(copy_divs) >= 2:
            goods_name = _norm(copy_divs[0].get("data-content") or copy_divs[0].get_text())
            sku_code = _norm(copy_divs[1].get("data-content") or copy_divs[1].get_text())
        elif len(copy_divs) == 1:
            goods_name = _norm(copy_divs[0].get("data-content") or copy_divs[0].get_text())

        sku = ""
        platform_sku_el = info_td.select_one(".limingcentUrlpic span")
        if platform_sku_el:
            sku = _norm(platform_sku_el.get_text())
        if not sku:
            continue

        items.append(
            {
                "sku": sku,
                "sku_code": sku_code,
                "goods_name": goods_name,
                "stock_available": _to_int(tds[5].get_text()) or 0,
                "unit_price": _to_decimal(tds[8].get_text()),
            }
        )
    return items


def parse_yuncang_choose_goods_html(html: str) -> list[dict[str, Any]]:
    """Parse the DXM03 yuncang add-product chooser fragment."""

    soup = BeautifulSoup(html or "", "html.parser")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in soup.select("a.chooseGoodsBtn"):
        goods_id = _norm(
            link.get("data-goodsid")
            or link.get("data-goodsId")
            or link.get("data-goods-id")
        )
        if not goods_id or goods_id in seen:
            continue
        seen.add(goods_id)
        sku_el = soup.find(id=f"hiddenSkuz_{goods_id}")
        name_el = soup.find(id=f"hiddenNamez_{goods_id}")
        image_el = soup.find(id=f"hiddenMainImagez_{goods_id}")
        sku = _norm(sku_el.get("value") if sku_el else "")
        if not sku:
            continue
        row = link.find_parent("tr")
        row_text = _norm(row.get_text(" ")) if row else ""
        sku_code_match = re.search(r"\[(\d+)\]", row_text)
        image_url = _norm(image_el.get("value") if image_el else "")
        items.append(
            {
                "goods_id": goods_id,
                "sku": sku,
                "sku_code": sku_code_match.group(1) if sku_code_match else "",
                "goods_name": _norm(name_el.get("value") if name_el else ""),
                "image_url": _normalize_url(image_url),
                "has_image": not _is_placeholder_image(image_url),
                "raw_text": row_text,
            }
        )
    return items


def _fetch_all_skus(cdp_url: str) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    all_items: list[dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            page.goto(YUNCANG_PAGE_URL, timeout=30000)
            page.wait_for_load_state("networkidle")
            time.sleep(3)

            try:
                page.select_option('select[name="pageselct"]', "300")
                page.wait_for_load_state("networkidle")
                time.sleep(4)
            except Exception:
                pass

            seen: set[str] = set()
            while True:
                for item in parse_yuncang_page_html(page.content()):
                    sku = str(item.get("sku") or "").strip()
                    if not sku or sku in seen:
                        continue
                    seen.add(sku)
                    all_items.append(item)

                next_page_btn = page.locator('#upPage a[title="下一页"]')
                if not next_page_btn.count():
                    break
                parent_li = next_page_btn.locator("xpath=..")
                if "disabled" in (parent_li.get_attribute("class") or ""):
                    break
                next_page_btn.click()
                time.sleep(3)
                page.wait_for_load_state("networkidle")
        finally:
            try:
                page.close()
            except Exception:
                pass
    return all_items


def upsert_skus(items: list[dict[str, Any]]) -> dict[str, int]:
    if not items:
        return {"rows": 0, "affected": 0}
    ensure_table()
    now = datetime.now()
    sql = f"""
        INSERT INTO {YUNCANG_TABLE}
          (sku, sku_code, goods_name, stock_available, unit_price, raw_json, synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          sku_code = VALUES(sku_code),
          goods_name = VALUES(goods_name),
          stock_available = VALUES(stock_available),
          unit_price = VALUES(unit_price),
          raw_json = VALUES(raw_json),
          synced_at = VALUES(synced_at)
    """
    affected = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for item in items:
                cur.execute(
                    sql,
                    (
                        item["sku"],
                        item.get("sku_code") or None,
                        item.get("goods_name") or None,
                        item.get("stock_available") or 0,
                        item.get("unit_price"),
                        json.dumps(item, ensure_ascii=False, default=str),
                        now,
                    ),
                )
                affected += cur.rowcount
        conn.commit()
    return {"rows": len(items), "affected": int(affected or 0)}


def list_unit_prices(skus: list[str]) -> dict[str, dict]:
    cleaned = sorted({str(value).strip() for value in (skus or []) if str(value).strip()})
    if not cleaned:
        return {}
    placeholders = ",".join(["%s"] * len(cleaned))
    rows = query(
        "SELECT sku, sku_code, goods_name, unit_price, stock_available "
        f"FROM {YUNCANG_TABLE} WHERE sku IN ({placeholders})",
        tuple(cleaned),
    )
    out: dict[str, dict] = {}
    for row in rows or []:
        sku = str(row.get("sku") or "").strip()
        if not sku:
            continue
        out[sku] = {
            "sku": sku,
            "sku_code": row.get("sku_code") or "",
            "goods_name": row.get("goods_name") or "",
            "unit_price": row.get("unit_price"),
            "stock_available": row.get("stock_available"),
        }
    return out


def _product_skus(product_id: int) -> set[str]:
    skus: set[str] = set()
    rows = query(
        "SELECT dianxiaomi_sku AS sku FROM media_product_skus "
        "WHERE product_id = %s AND dianxiaomi_sku IS NOT NULL AND dianxiaomi_sku <> ''",
        (int(product_id),),
    )
    for row in rows or []:
        sku = str(row.get("sku") or "").strip()
        if sku:
            skus.add(sku)

    rows = query(
        "SELECT DISTINCT product_display_sku AS sku FROM dianxiaomi_order_lines "
        "WHERE product_id = %s AND product_display_sku IS NOT NULL AND product_display_sku <> ''",
        (int(product_id),),
    )
    for row in rows or []:
        sku = str(row.get("sku") or "").strip()
        if sku:
            skus.add(sku)
    return skus


def _refresh_product_purchase_price(product_id: int) -> Decimal | None:
    pid = int(product_id)
    skus = _product_skus(pid)
    if not skus:
        execute("UPDATE media_products SET purchase_price = NULL WHERE id = %s", (pid,))
        return None

    placeholders = ",".join(["%s"] * len(skus))
    rows = query(
        f"SELECT sku, unit_price FROM {YUNCANG_TABLE} "
        f"WHERE sku IN ({placeholders}) AND unit_price IS NOT NULL AND unit_price > 0",
        tuple(skus),
    )
    sku_to_price = {
        str(row["sku"]): row.get("unit_price")
        for row in rows or []
        if row.get("sku") and row.get("unit_price") is not None
    }
    if not sku_to_price:
        execute("UPDATE media_products SET purchase_price = NULL WHERE id = %s", (pid,))
        return None

    counts = query(
        "SELECT product_display_sku AS sku, COUNT(*) AS cnt FROM dianxiaomi_order_lines "
        "WHERE product_id = %s AND product_display_sku IS NOT NULL "
        "GROUP BY product_display_sku ORDER BY cnt DESC",
        (pid,),
    )
    primary_price: Decimal | None = None
    for row in counts or []:
        sku = str(row.get("sku") or "").strip()
        if sku in sku_to_price:
            primary_price = sku_to_price[sku]
            break
    if primary_price is None:
        primary_price = sorted(sku_to_price.values())[len(sku_to_price) // 2]

    if primary_price is None or primary_price <= 0:
        execute("UPDATE media_products SET purchase_price = NULL WHERE id = %s", (pid,))
        return None

    execute("UPDATE media_products SET purchase_price = %s WHERE id = %s", (primary_price, pid))
    return primary_price


def refresh_purchase_prices_for_matched() -> dict[str, int]:
    rows = query(
        f"""
        SELECT DISTINCT mps.product_id
        FROM media_product_skus mps
        JOIN {YUNCANG_TABLE} y ON y.sku = mps.dianxiaomi_sku
        WHERE mps.product_id IS NOT NULL
          AND mps.dianxiaomi_sku IS NOT NULL
          AND mps.dianxiaomi_sku <> ''
        UNION
        SELECT DISTINCT d.product_id
        FROM dianxiaomi_order_lines d
        JOIN {YUNCANG_TABLE} y ON y.sku = d.product_display_sku
        WHERE d.product_id IS NOT NULL
          AND d.product_display_sku IS NOT NULL
          AND d.product_display_sku <> ''
        """
    )
    refreshed = 0
    for row in rows or []:
        product_id = row.get("product_id")
        if product_id is None:
            continue
        try:
            _refresh_product_purchase_price(int(product_id))
            refreshed += 1
        except Exception:
            log.warning("refresh purchase_price failed for product_id=%s", product_id, exc_info=True)
    return {"refreshed": refreshed}


def build_yuncang_add_targets(
    sku_rows: list[dict[str, Any]],
    *,
    pairing_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return base SKU targets that should exist in DXM03 yuncang.

    Single commodities are added directly. Combo commodities are represented by
    their component SKUs because yuncang purchasing/inventory should operate on
    base SKUs instead of the outer combo SKU.
    """

    source_rows = pairing_items if pairing_items is not None else sku_rows
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_target(raw_sku: Any, *, parent_sku: str = "", row: dict[str, Any] | None = None, component: dict[str, Any] | None = None) -> None:
        sku = _norm(raw_sku)
        if not sku or sku in seen:
            return
        seen.add(sku)
        row = row or {}
        component = component or {}
        targets.append(
            {
                "sku": sku,
                "parent_sku": parent_sku,
                "variant_title": row.get("variant_title") or row.get("shopify_variant_title") or "",
                "goods_name": (
                    component.get("name")
                    or component.get("component_name")
                    or row.get("dianxiaomi_name")
                    or row.get("name")
                    or ""
                ),
                "image_url": (
                    component.get("image_url")
                    or component.get("component_img_url")
                    or row.get("image_url")
                    or ""
                ),
                "source": "combo_component" if parent_sku else "single",
            }
        )

    for row in source_rows or []:
        if not isinstance(row, dict):
            continue
        sku = _norm(row.get("dianxiaomi_sku") or row.get("sku"))
        commodity = row.get("commodity") if isinstance(row.get("commodity"), dict) else {}
        dxm03 = row.get("dxm03") if isinstance(row.get("dxm03"), dict) else {}
        dxm03_commodity = dxm03.get("commodity") if isinstance(dxm03.get("commodity"), dict) else {}
        components = row.get("combo_components") if isinstance(row.get("combo_components"), list) else []
        is_combo = bool(
            row.get("is_combo")
            or commodity.get("is_combo")
            or dxm03_commodity.get("is_combo")
            or components
        )
        if is_combo:
            for component in components:
                if not isinstance(component, dict):
                    continue
                add_target(
                    component.get("sku") or component.get("component_sku"),
                    parent_sku=sku,
                    row=row,
                    component=component,
                )
            continue
        add_target(sku, row=row)
    return targets


def _fetch_live_yuncang_sku(ctx, sku: str) -> dict[str, Any] | None:
    response = ctx.request.post(
        f"{DXM_BASE_URL}{YUNCANG_PAGE_LIST_PATH}",
        form={
            "pageNo": "1",
            "pageSize": "50",
            "searchType": "0",
            "searchValue": sku,
            "searchMode": "1",
            "warehouseId": "",
            "salesDayType": "5",
            "salesDayMin": "",
            "salesDayMax": "",
            "createTimeStart": "",
            "createTimeEnd": "",
        },
        timeout=30000,
    )
    if response.status >= 400:
        raise DianxiaomiYuncangError(
            f"query yuncang sku HTTP {response.status}: {response.text()[:200]}"
        )
    for item in parse_yuncang_page_html(response.text()):
        if _norm(item.get("sku")) == sku:
            return item
    return None


def _fetch_yuncang_add_candidates(ctx, sku: str) -> list[dict[str, Any]]:
    response = ctx.request.get(
        f"{DXM_BASE_URL}{YUNCANG_CHOOSE_GOODS_PATH}",
        params={
            "pageNo": "1",
            "pageSize": "50",
            "chooseGoodsId": "",
            "searchType": "0",
            "searchValue": sku,
            "searchMode": "1",
            "agentName": "",
        },
        timeout=30000,
    )
    if response.status >= 400:
        raise DianxiaomiYuncangError(
            f"query yuncang add candidates HTTP {response.status}: {response.text()[:200]}"
        )
    return [
        item for item in parse_yuncang_choose_goods_html(response.text())
        if _norm(item.get("sku")) == sku
    ]


def _add_yuncang_goods_ids(ctx, goods_ids: list[str]) -> dict[str, Any]:
    response = ctx.request.post(
        f"{DXM_BASE_URL}{YUNCANG_ADD_SKU_PATH}",
        form={"ids": ",".join(goods_ids)},
        timeout=30000,
    )
    payload = _response_json(response, "add yuncang sku")
    _ensure_success(payload, "add yuncang sku")
    return payload


def _yuncang_operation_logs(stage: str, items: list[dict[str, Any]]) -> list[dict[str, str]]:
    logs: list[dict[str, str]] = [{"level": "info", "message": f"{stage}：开始"}]
    status_level = {
        "added": "ok",
        "already_exists": "ok",
        "blocked": "warn",
        "error": "error",
        "pending_add": "info",
    }
    for item in items or []:
        status = str(item.get("status") or "")
        label = item.get("variant_title") or item.get("sku") or "SKU"
        message = item.get("message") or status
        logs.append({
            "level": status_level.get(status, "info"),
            "message": f"{label}：{message}",
        })
    return logs


def add_product_skus_to_yuncang(
    product: dict[str, Any],
    sku_rows: list[dict[str, Any]],
    *,
    pairing_items: list[dict[str, Any]] | None = None,
    cdp_url: str | None = None,
    refresh_local: bool = True,
    skip_no_image: bool = True,
    force_isolated_thread: bool | None = None,
) -> dict[str, Any]:
    return _run_playwright_operation(
        "dxm03_yuncang_add_skus",
        lambda: _add_product_skus_to_yuncang_impl(
            product,
            sku_rows,
            pairing_items=pairing_items,
            cdp_url=cdp_url,
            refresh_local=refresh_local,
            skip_no_image=skip_no_image,
        ),
        force_isolated_thread=force_isolated_thread,
    )


def _add_product_skus_to_yuncang_impl(
    product: dict[str, Any],
    sku_rows: list[dict[str, Any]],
    *,
    pairing_items: list[dict[str, Any]] | None = None,
    cdp_url: str | None = None,
    refresh_local: bool = True,
    skip_no_image: bool = True,
) -> dict[str, Any]:
    targets = build_yuncang_add_targets(sku_rows, pairing_items=pairing_items)
    if not targets:
        message = "没有可添加到 DXM03 小秘云仓的基础 SKU"
        return {
            "ok": False,
            "error": "missing_yuncang_targets",
            "message": message,
            "logs": [{"level": "error", "message": message}],
            "items": [],
            "summary": {
                "target_count": 0,
                "added_count": 0,
                "existing_count": 0,
                "blocked_count": 0,
                "error_count": 0,
            },
        }

    from playwright.sync_api import sync_playwright

    url = cdp_url or dxm03_cdp_url()
    results: list[dict[str, Any]] = []
    live_items_by_sku: dict[str, dict[str, Any]] = {}
    with browser_automation_lock(
        task_code="dxm03_yuncang_add_skus",
        timeout_seconds=180,
        command=str(product.get("product_code") or product.get("id") or ""),
    ):
        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.connect_over_cdp(url)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            pending: list[dict[str, Any]] = []
            for target in targets:
                sku = str(target.get("sku") or "").strip()
                item_result = {**target, "status": "pending"}
                try:
                    existing = _fetch_live_yuncang_sku(ctx, sku)
                    if existing:
                        item_result.update({
                            "status": "already_exists",
                            "message": "DXM03 小秘云仓已存在该基础 SKU",
                            "yuncang": existing,
                        })
                        live_items_by_sku[sku] = existing
                        results.append(item_result)
                        continue

                    candidates = _fetch_yuncang_add_candidates(ctx, sku)
                    item_result["candidates"] = candidates
                    if not candidates:
                        item_result.update({
                            "status": "blocked",
                            "error": "missing_yuncang_candidate",
                            "message": "DXM03 商品选择弹窗找不到可添加的基础 SKU",
                        })
                        results.append(item_result)
                        continue

                    candidate = candidates[0]
                    if skip_no_image and not candidate.get("has_image"):
                        item_result.update({
                            "status": "blocked",
                            "error": "missing_sku_image",
                            "message": "DXM03 添加商品弹窗提示该 SKU 缺少图片，未自动添加",
                            "candidate": candidate,
                        })
                        results.append(item_result)
                        continue

                    item_result.update({
                        "status": "pending_add",
                        "message": "待添加到 DXM03 小秘云仓",
                        "candidate": candidate,
                        "goods_id": candidate.get("goods_id") or "",
                    })
                    pending.append(item_result)
                    results.append(item_result)
                except Exception as exc:  # noqa: BLE001 - return per-SKU business result
                    item_result.update({
                        "status": "error",
                        "error": "yuncang_prepare_failed",
                        "message": str(exc),
                    })
                    results.append(item_result)

            goods_ids = [
                str(item.get("goods_id") or "").strip()
                for item in pending
                if str(item.get("goods_id") or "").strip()
            ]
            if goods_ids:
                try:
                    add_payload = _add_yuncang_goods_ids(ctx, goods_ids)
                    time.sleep(1)
                    for item in pending:
                        sku = str(item.get("sku") or "").strip()
                        live = _fetch_live_yuncang_sku(ctx, sku)
                        if live:
                            item.update({
                                "status": "added",
                                "message": "已添加到 DXM03 小秘云仓",
                                "yuncang": live,
                                "add_payload": add_payload,
                            })
                            live_items_by_sku[sku] = live
                        else:
                            item.update({
                                "status": "error",
                                "error": "yuncang_add_not_visible",
                                "message": "DXM03 添加接口返回成功，但云仓列表暂未回查到该 SKU",
                                "add_payload": add_payload,
                            })
                except Exception as exc:  # noqa: BLE001 - mark pending rows as failed
                    for item in pending:
                        if item.get("status") == "pending_add":
                            item.update({
                                "status": "error",
                                "error": "yuncang_add_failed",
                                "message": str(exc),
                            })
        finally:
            try:
                playwright.stop()
            except Exception:
                pass

    local_refresh = {"upsert": {"rows": 0, "affected": 0}, "purchase_price": None}
    if refresh_local and live_items_by_sku:
        upsert_summary = upsert_skus(list(live_items_by_sku.values()))
        purchase_price = None
        if product.get("id") is not None:
            try:
                purchase_price = _refresh_product_purchase_price(int(product["id"]))
            except Exception:
                log.warning(
                    "refresh purchase price after yuncang add failed product_id=%s",
                    product.get("id"),
                    exc_info=True,
                )
        local_refresh = {"upsert": upsert_summary, "purchase_price": purchase_price}

    success_statuses = {"added", "already_exists"}
    ok = bool(results) and all(item.get("status") in success_statuses for item in results)
    summary = {
        "target_count": len(targets),
        "added_count": sum(1 for item in results if item.get("status") == "added"),
        "existing_count": sum(1 for item in results if item.get("status") == "already_exists"),
        "blocked_count": sum(1 for item in results if item.get("status") == "blocked"),
        "error_count": sum(1 for item in results if item.get("status") == "error"),
        "local_refresh": local_refresh,
    }
    message = (
        "DXM03 小秘云仓添加商品完成："
        f"新增 {summary['added_count']}，"
        f"已存在 {summary['existing_count']}，"
        f"阻断 {summary['blocked_count']}，"
        f"失败 {summary['error_count']}"
    )
    return {
        "ok": ok,
        "product_id": product.get("id"),
        "product_code": product.get("product_code") or "",
        "message": message,
        "logs": _yuncang_operation_logs("添加基础 SKU 到 DXM03 小秘云仓", results),
        "items": results,
        "summary": summary,
    }


def sync_skus(cdp_url: str = DEFAULT_CDP_URL) -> dict[str, Any]:
    ensure_table()
    items = _fetch_all_skus(cdp_url)
    upsert_summary = upsert_skus(items)
    refresh_summary = refresh_purchase_prices_for_matched()
    return {
        "fetched": len(items),
        "inserted": upsert_summary.get("affected", 0),
        "upsert": upsert_summary,
        "refresh_prices": refresh_summary,
    }
