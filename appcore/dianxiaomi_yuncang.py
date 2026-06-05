from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from bs4 import BeautifulSoup

from appcore.db import execute, get_conn, query, query_one


log = logging.getLogger(__name__)

DEFAULT_CDP_URL = "http://127.0.0.1:9225"
YUNCANG_PAGE_URL = "https://www.dianxiaomi.com/yuncangWarehouseSku/index.htm"
YUNCANG_TABLE = "dianxiaomi_yuncang_skus"


class DianxiaomiYuncangError(RuntimeError):
    pass


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
