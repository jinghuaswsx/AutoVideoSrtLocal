from __future__ import annotations

import json
import logging
import re
import time
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from appcore.browser_automation_lock import browser_automation_lock
from appcore.db import execute, get_conn, query, query_one


log = logging.getLogger(__name__)

ORDER_PAGE_URL = "https://www.xmyc.com/storage/index.htm?indexType=1"
PAGE_LIST_URL = "https://www.xmyc.com/storage/pageList.htm"
DEFAULT_CDP_URL = "http://127.0.0.1:9224"
DEFAULT_PAGE_SIZE = 200
MAX_PAGES = 50
XMYC_BASE_URL = "https://www.xmyc.com/"


class XmycStorageError(RuntimeError):
    pass


def _norm(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _to_int(text: str | None) -> int | None:
    if text is None:
        return None
    s = _norm(text)
    if not s or s in ("--", "-"):
        return None
    try:
        return int(s.replace(",", ""))
    except ValueError:
        return None


def _to_decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    s = _norm(text)
    if not s or s in ("--", "-"):
        return None
    try:
        return Decimal(s.replace(",", ""))
    except (ValueError, ArithmeticError):
        return None


def _image_url_from_img(img) -> str:
    if img is None:
        return ""
    for attr in ("data-original", "data-src", "data-lazy-src", "src"):
        raw = _norm(img.get(attr))
        if raw and raw not in ("#", "javascript:void(0);") and not raw.startswith("data:"):
            return urljoin(XMYC_BASE_URL, raw)
    return ""


def parse_page_list_html(html: str) -> tuple[int | None, list[dict[str, Any]]]:
    soup = BeautifulSoup(html or "", "html.parser")
    total_input = soup.select_one("#totalSize")
    total_size = _to_int(total_input.get("value")) if total_input else None
    rows: list[dict[str, Any]] = []
    for tr in soup.select("table.commodityList tbody tr"):
        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue
        checkbox = tr.select_one('input[type="checkbox"][value]')
        sku_code_el = tr.select_one(".skuCode")
        sku_el = tr.select_one(".sku")
        goods_name_el = tr.select_one(".goodsName")
        image_el = tr.select_one(".imgOut img, .img_out img, img")
        sku = _norm(sku_el.get_text() if sku_el else "")
        sku_code = _norm(sku_code_el.get_text() if sku_code_el else "")
        if not sku or not sku_code:
            continue
        # td indexes: 0=checkbox 1=info 2=warehouse 3=safety 4=in_transit 5=outbound
        # 6=available 7=stock_in 8=frozen 9=unit_price 10=total_price
        # 11=stagnation 12=shelf 13=time
        def td_text(idx: int) -> str:
            return _norm(tds[idx].get_text()) if idx < len(tds) else ""
        rows.append({
            "xmyc_id": (checkbox.get("value") or "").strip() if checkbox else "",
            "sku_code": sku_code,
            "sku": sku,
            "goods_name": _norm(goods_name_el.get_text() if goods_name_el else ""),
            "image_url": _image_url_from_img(image_el),
            "warehouse": td_text(2),
            "stock_available": _to_int(td_text(7)),
            "unit_price": _to_decimal(td_text(9)),
            "shelf_code": td_text(12),
        })
    return total_size, rows


@contextmanager
def open_xmyc_page(cdp_url: str = DEFAULT_CDP_URL) -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = next((x for x in ctx.pages if "xmyc.com" in (x.url or "")), None)
        owns = False
        if page is None:
            page = ctx.new_page()
            page.goto(ORDER_PAGE_URL, wait_until="domcontentloaded", timeout=30000)
            owns = True
        try:
            yield page
        finally:
            if owns:
                try:
                    page.close()
                except Exception:
                    pass


def _post_page_list(page, *, page_no: int, page_size: int) -> str:
    last_error: Exception | None = None
    for attempt in range(1, 5):
        try:
            result = page.evaluate(
                """
                async ({ url, payload }) => {
                  const body = new URLSearchParams();
                  for (const [k, v] of Object.entries(payload)) body.append(k, String(v ?? ""));
                  const r = await fetch(url, {
                    method: "POST",
                    headers: {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest"},
                    credentials: "include",
                    body: body.toString(),
                  });
                  const text = await r.text();
                  return { ok: r.ok, status: r.status, text };
                }
                """,
                {"url": PAGE_LIST_URL, "payload": {
                    "zoneType": "", "searchMode": "1", "orderBy": "0",
                    "page": str(page_no), "pageSize": str(page_size),
                }},
            )
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * attempt)
            continue
        if result.get("ok"):
            return result.get("text") or ""
        last_error = RuntimeError(f"HTTP {result.get('status')}")
        time.sleep(1.5 * attempt)
    raise last_error or XmycStorageError("xmyc page_list request failed")


def fetch_all_skus(page) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for page_no in range(1, MAX_PAGES + 1):
        html = _post_page_list(page, page_no=page_no, page_size=DEFAULT_PAGE_SIZE)
        total_size, rows = parse_page_list_html(html)
        if not rows:
            break
        for row in rows:
            seen[row["sku"]] = row
        if len(rows) < DEFAULT_PAGE_SIZE:
            break
        if total_size is not None and len(seen) >= total_size:
            break
    return list(seen.values())


def upsert_skus(rows: list[dict[str, Any]]) -> dict[str, int]:
    if not rows:
        return {"inserted": 0, "updated": 0, "rows": 0}
    now = datetime.now()
    sql = (
        "INSERT INTO xmyc_storage_skus "
        "(xmyc_id, sku_code, sku, goods_name, image_url, unit_price, stock_available, warehouse, shelf_code, raw_json, synced_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  xmyc_id=VALUES(xmyc_id), sku_code=VALUES(sku_code), goods_name=VALUES(goods_name), "
        "  image_url=VALUES(image_url), "
        "  unit_price=VALUES(unit_price), stock_available=VALUES(stock_available), "
        "  warehouse=VALUES(warehouse), shelf_code=VALUES(shelf_code), "
        "  raw_json=VALUES(raw_json), synced_at=VALUES(synced_at)"
    )
    affected_total = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for row in rows:
                params = (
                    row.get("xmyc_id") or None,
                    row["sku_code"],
                    row["sku"],
                    row.get("goods_name") or None,
                    row.get("image_url") or None,
                    row.get("unit_price"),
                    row.get("stock_available"),
                    row.get("warehouse") or None,
                    row.get("shelf_code") or None,
                    json.dumps(row, default=str, ensure_ascii=False),
                    now,
                )
                cur.execute(sql, params)
                affected_total += cur.rowcount
        conn.commit()
    return {"rows": len(rows), "affected": affected_total}


def auto_match_products() -> dict[str, int]:
    sql = (
        "UPDATE xmyc_storage_skus s "
        "JOIN ("
        "  SELECT product_display_sku, product_id, COUNT(*) AS cnt "
        "  FROM dianxiaomi_order_lines "
        "  WHERE product_display_sku IS NOT NULL AND product_id IS NOT NULL "
        "  GROUP BY product_display_sku, product_id "
        ") d ON d.product_display_sku = s.sku "
        "SET s.product_id = d.product_id, s.match_type = 'auto', s.matched_at = NOW() "
        "WHERE s.match_type IS NULL OR s.match_type = 'auto'"
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            affected = cur.rowcount
        conn.commit()
    return {"auto_matched": int(affected or 0)}


def sync_from_xmyc(cdp_url: str = DEFAULT_CDP_URL) -> dict[str, Any]:
    # The shared shopify-style automation lock is taken by the systemd unit
    # via deploy/server_browser/with_browser_lock.sh, so we don't re-acquire
    # it here — that nested flock would deadlock against the parent's lock
    # on the same file path.
    with open_xmyc_page(cdp_url) as page:
        rows = fetch_all_skus(page)
    upsert_summary = upsert_skus(rows)
    auto_summary = auto_match_products()
    refresh_summary = refresh_purchase_prices_for_matched()
    return {
        "fetched": len(rows),
        "upsert": upsert_summary,
        "auto_match": auto_summary,
        "refresh_prices": refresh_summary,
    }


def list_skus(*, keyword: str | None = None, matched_filter: str = "all",
              product_id: int | None = None, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if keyword:
        like = f"%{keyword.strip()}%"
        where.append("(s.sku LIKE %s OR s.sku_code LIKE %s OR s.goods_name LIKE %s)")
        params.extend([like, like, like])
    if matched_filter == "matched":
        where.append("s.product_id IS NOT NULL")
    elif matched_filter == "unmatched":
        where.append("s.product_id IS NULL")
    if product_id is not None:
        where.append("s.product_id = %s")
        params.append(int(product_id))
    sql = (
        "SELECT s.id, s.xmyc_id, s.sku_code, s.sku, s.goods_name, s.image_url, s.unit_price, s.stock_available, "
        "       s.warehouse, s.shelf_code, s.product_id, s.match_type, s.matched_at, "
        "       s.standalone_price_sku, s.standalone_shipping_fee_sku, s.packet_cost_actual_sku, "
        "       s.sku_orders_count, "
        "       p.name AS product_name, p.product_code AS product_code "
        "FROM xmyc_storage_skus s "
        "LEFT JOIN media_products p ON p.id = s.product_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY (s.product_id IS NOT NULL), s.sku "
        "LIMIT %s OFFSET %s"
    )
    params.extend([int(limit), int(offset)])
    return query(sql, tuple(params))


def get_skus_for_product(product_id: int) -> list[dict[str, Any]]:
    return list_skus(product_id=int(product_id), limit=200)


def set_product_skus(product_id: int, skus: list[str], *, matched_by: int | None = None) -> dict[str, Any]:
    skus = [s.strip() for s in (skus or []) if s and s.strip()]
    pid = int(product_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE xmyc_storage_skus SET product_id = NULL, match_type = NULL, matched_by = NULL, matched_at = NULL "
                "WHERE product_id = %s AND match_type IN ('auto','manual')",
                (pid,),
            )
            cleared = cur.rowcount
            attached = 0
            if skus:
                placeholders = ",".join(["%s"] * len(skus))
                cur.execute(
                    f"UPDATE xmyc_storage_skus SET product_id = %s, match_type = 'manual', "
                    f"  matched_by = %s, matched_at = NOW() "
                    f"WHERE sku IN ({placeholders})",
                    [pid, matched_by, *skus],
                )
                attached = cur.rowcount
        conn.commit()
    new_price = _refresh_product_purchase_price(pid)
    return {
        "product_id": pid,
        "cleared": int(cleared or 0),
        "attached": int(attached or 0),
        "purchase_price": float(new_price) if new_price is not None else None,
    }


def _refresh_product_purchase_price(product_id: int) -> Decimal | None:
    pid = int(product_id)
    
    # 1. Fetch SKUs that are associated with the product in xmyc_storage_skus
    skus = set()
    try:
        rows = query("SELECT sku FROM xmyc_storage_skus WHERE product_id = %s", (pid,))
        for r in rows:
            if r.get("sku"):
                skus.add(r["sku"])
    except Exception as exc:
        log.warning("Failed to query xmyc_storage_skus for SKUs: %s", exc)

    # 2. Also fetch SKUs that are associated with the product in dianxiaomi_order_lines
    try:
        rows = query(
            "SELECT DISTINCT product_display_sku FROM dianxiaomi_order_lines "
            "WHERE product_id = %s AND product_display_sku IS NOT NULL AND product_display_sku != ''",
            (pid,)
        )
        for r in rows:
            skus.add(r["product_display_sku"])
    except Exception as exc:
        log.warning("Failed to query dianxiaomi_order_lines for SKUs: %s", exc)

    if not skus:
        execute(
            "UPDATE media_products SET purchase_price = NULL WHERE id = %s",
            (pid,),
        )
        return None

    # 3. Lookup SKU prices in dianxiaomi_yuncang_skus first (filtering for price > 0)
    sku_to_price = {}
    placeholders = ",".join(["%s"] * len(skus))
    try:
        dxm_prices = query(
            f"SELECT sku, unit_price FROM dianxiaomi_yuncang_skus "
            f"WHERE sku IN ({placeholders}) AND unit_price IS NOT NULL AND unit_price > 0",
            tuple(skus)
        )
        for r in dxm_prices:
            sku_to_price[r["sku"]] = r["unit_price"]
    except Exception as exc:
        log.warning("Failed to query dianxiaomi_yuncang_skus: %s", exc)

    # 4. Fallback to xmyc_storage_skus price > 0 if not found in dianxiaomi_yuncang_skus
    try:
        xmyc_prices = query(
            f"SELECT sku, unit_price FROM xmyc_storage_skus "
            f"WHERE sku IN ({placeholders}) AND unit_price IS NOT NULL AND unit_price > 0",
            tuple(skus)
        )
        for r in xmyc_prices:
            sku = r["sku"]
            if sku not in sku_to_price:
                sku_to_price[sku] = r["unit_price"]
    except Exception as exc:
        log.warning("Failed to query xmyc_storage_skus: %s", exc)

    if not sku_to_price:
        execute("UPDATE media_products SET purchase_price = NULL WHERE id = %s", (pid,))
        return None

    # 5. Resolve primary price based on order line counts
    counts = query(
        "SELECT product_display_sku AS sku, COUNT(*) AS cnt FROM dianxiaomi_order_lines "
        "WHERE product_id = %s AND product_display_sku IS NOT NULL "
        "GROUP BY product_display_sku ORDER BY cnt DESC",
        (pid,),
    )
    primary_price: Decimal | None = None
    for row in counts:
        sku = row["sku"]
        if sku in sku_to_price:
            primary_price = sku_to_price[sku]
            break
    if primary_price is None and sku_to_price:
        primary_price = sorted(sku_to_price.values())[len(sku_to_price) // 2]

    if primary_price is None or primary_price <= 0:
        execute("UPDATE media_products SET purchase_price = NULL WHERE id = %s", (pid,))
        return None

    execute(
        "UPDATE media_products SET purchase_price = %s WHERE id = %s",
        (primary_price, pid),
    )
    return primary_price


def sync_dianxiaomi_yuncang_skus(cdp_url: str = "http://127.0.0.1:9225") -> dict[str, Any]:
    # 1. Initialize table
    execute("""
    CREATE TABLE IF NOT EXISTS dianxiaomi_yuncang_skus (
      sku VARCHAR(128) NOT NULL PRIMARY KEY,
      sku_code VARCHAR(128) DEFAULT NULL,
      goods_name VARCHAR(500) DEFAULT NULL,
      stock_available INT DEFAULT 0,
      unit_price DECIMAL(10,2) DEFAULT NULL,
      raw_json JSON DEFAULT NULL,
      synced_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)
    
    # 2. Scrape from Dianxiaomi
    all_items = []
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            page.goto("https://www.dianxiaomi.com/yuncangWarehouseSku/index.htm", timeout=30000)
            page.wait_for_load_state("networkidle")
            time.sleep(3)
            
            # Click 300 page size to speed up scraping
            try:
                page.select_option('select[name="pageselct"]', '300')
                page.wait_for_load_state("networkidle")
                time.sleep(4)
            except Exception:
                pass
                
            while True:
                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                
                table = None
                for t in soup.find_all("table"):
                    headers = [th.get_text().strip() for th in t.find_all("th")]
                    if "商品信息" in headers:
                        table = t
                        break
                
                if not table:
                    break
                    
                rows = table.find_all("tr", class_="content")
                if not rows:
                    break
                    
                for r in rows:
                    tds = r.find_all("td", recursive=False)
                    if len(tds) < 10:
                        continue
                    
                    info_td = tds[1]
                    copy_divs = info_td.select(".copyDataContentText")
                    
                    title = ""
                    sku_code = ""
                    if len(copy_divs) >= 2:
                        title = _norm(copy_divs[0].get("data-content", ""))
                        sku_code = _norm(copy_divs[1].get("data-content", ""))
                    elif len(copy_divs) == 1:
                        title = _norm(copy_divs[0].get("data-content", ""))
                    
                    sku = ""
                    platform_sku_el = info_td.select_one(".limingcentUrlpic span")
                    if platform_sku_el:
                        sku = _norm(platform_sku_el.get_text())
                    
                    if not sku:
                        continue
                        
                    available_stock = _to_int(tds[5].get_text()) or 0
                    unit_price = _to_decimal(tds[8].get_text())
                    
                    all_items.append({
                        "sku": sku,
                        "sku_code": sku_code,
                        "goods_name": title,
                        "stock_available": available_stock,
                        "unit_price": unit_price
                    })
                
                # Check next page button inside #upPage specifically to avoid strict mode violations
                next_page_btn = page.locator('#upPage a[title="下一页"]')
                if not next_page_btn.count():
                    break
                    
                parent_li = page.locator('#upPage a[title="下一页"]').locator('xpath=..')
                li_class = parent_li.get_attribute("class") or ""
                if "disabled" in li_class:
                    break
                    
                next_page_btn.click()
                time.sleep(3)
                page.wait_for_load_state("networkidle")
                
        finally:
            try:
                page.close()
            except Exception:
                pass
                
    # 3. Clear and save to DB
    inserted = 0
    if all_items:
        execute("TRUNCATE TABLE dianxiaomi_yuncang_skus")
        sql = """
        INSERT INTO dianxiaomi_yuncang_skus (sku, sku_code, goods_name, stock_available, unit_price, raw_json)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          sku_code = VALUES(sku_code),
          goods_name = VALUES(goods_name),
          stock_available = VALUES(stock_available),
          unit_price = VALUES(unit_price),
          raw_json = VALUES(raw_json)
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                for item in all_items:
                    cur.execute(sql, (
                        item["sku"],
                        item["sku_code"] or None,
                        item["goods_name"] or None,
                        item["stock_available"],
                        item["unit_price"],
                        json.dumps(item, ensure_ascii=False, default=str)
                    ))
                    inserted += 1
            conn.commit()

    # 4. Refresh matched prices
    refresh_summary = refresh_purchase_prices_for_matched()
    
    return {
        "fetched": len(all_items),
        "inserted": inserted,
        "refresh_prices": refresh_summary
    }


_SKU_EDITABLE_FIELDS = frozenset({
    "standalone_price_sku",
    "standalone_shipping_fee_sku",
    "packet_cost_actual_sku",
})


def update_sku(sku_id: int, fields: dict[str, Any]) -> dict[str, Any]:
    """Update editable aggregate fields on a single xmyc_storage_skus row.

    Returns the updated row dict (without roas enrichment).
    """
    sid = int(sku_id)
    updates: dict[str, Any] = {}
    for key in _SKU_EDITABLE_FIELDS:
        if key not in fields:
            continue
        val = fields[key]
        if val is None or (isinstance(val, str) and val.strip() == ""):
            updates[key] = None
            continue
        try:
            updates[key] = Decimal(str(val))
        except (ValueError, ArithmeticError):
            raise ValueError(f"invalid decimal for {key}: {val!r}")
    if not updates:
        raise ValueError("no editable fields provided")
    set_clauses = [f"{col} = %s" for col in updates]
    params = list(updates.values()) + [sid]
    execute(f"UPDATE xmyc_storage_skus SET {', '.join(set_clauses)} WHERE id = %s", tuple(params))
    row = query_one("SELECT * FROM xmyc_storage_skus WHERE id = %s", (sid,))
    if row is None:
        raise LookupError(f"sku id {sid} not found")
    return row


def refresh_purchase_prices_for_matched() -> dict[str, int]:
    rows = query(
        "SELECT DISTINCT product_id FROM xmyc_storage_skus WHERE product_id IS NOT NULL"
    )
    refreshed = 0
    for r in rows:
        try:
            _refresh_product_purchase_price(r["product_id"])
            refreshed += 1
        except Exception:
            log.warning("refresh purchase_price failed for product_id=%s", r.get("product_id"), exc_info=True)
    return {"refreshed": refreshed}
