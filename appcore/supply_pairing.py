"""Search dianxiaomi 1688 supply pairing records via DXM CDP browser.

Exposes an API to look up 1688 purchase links (sourceUrl) by SKU or Chinese
keyword from the dianxiaomi /web/supply/pairing page.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from appcore.browser_automation_lock import browser_automation_lock

log = logging.getLogger(__name__)

SUPPLY_PAIRING_API = (
    "https://www.dianxiaomi.com/api/dxmAlibabaProductPair/"
    "alibabaProductPairPageList.json"
)
SUPPLY_PAIRING_PAGE = "https://www.dianxiaomi.com/web/supply/pairing"
DEFAULT_DXM_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_PAGE_SIZE = 20
MAX_PAGES = 50


def _post_form_via_page(page, url: str, payload: dict[str, str]) -> dict[str, Any]:
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
                {"url": url, "payload": payload},
            )
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * attempt)
            continue
        if result.get("ok"):
            try:
                return json.loads(result.get("text") or "")
            except json.JSONDecodeError:
                last_error = RuntimeError(f"non-json response: {(result.get('text') or '')[:200]}")
        else:
            last_error = RuntimeError(f"HTTP {result.get('status')}")
        time.sleep(1.5 * attempt)
    raise last_error or RuntimeError("dianxiaomi supply pairing request failed")


def _search_once(
    page,
    query: str,
    *,
    search_type: str,
    status: str = "2",
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = MAX_PAGES,
) -> list[dict[str, Any]]:
    """Call the supply pairing API and return all items across pages."""
    out: list[dict[str, Any]] = []
    for page_no in range(1, max_pages + 1):
        payload = {
            "pageNo": str(page_no),
            "pageSize": str(page_size),
            "status": status,
            "searchType": search_type,
            "searchValue": query,
            "searchMode": "1",
        }
        body = _post_form_via_page(page, SUPPLY_PAIRING_API, payload)
        code = body.get("code")
        if code != 0:
            raise RuntimeError(f"dxm supply pairing error: {body.get('msg')} (code={code})")
        page_data = ((body.get("data") or {}).get("page") or {})
        items = page_data.get("list") or []
        out.extend(items)
        if len(items) < page_size:
            break
    return out


def _open_supply_page(cdp_url: str = DEFAULT_DXM_CDP_URL):
    """Context manager that yields a Playwright page on dianxiaomi.com."""
    from contextlib import contextmanager
    from playwright.sync_api import sync_playwright

    @contextmanager
    def _cm():
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            existing = next((x for x in ctx.pages if "dianxiaomi.com" in (x.url or "")), None)
            owns = False
            if existing is None:
                page = ctx.new_page()
                page.goto(SUPPLY_PAIRING_PAGE, wait_until="domcontentloaded", timeout=30000)
                owns = True
            else:
                page = existing
            try:
                yield page
            finally:
                if owns:
                    try:
                        page.close()
                    except Exception:
                        pass

    return _cm()


def search_supply_pairing(
    query: str,
    *,
    status: str = "0",
    page_size: int = DEFAULT_PAGE_SIZE,
    cdp_url: str = DEFAULT_DXM_CDP_URL,
) -> dict[str, Any]:
    """Search 1688 supply pairing records by SKU or Chinese keyword.

    Tries SKU search (searchType=1) first; if no results, falls back to
    keyword search (searchType=2).

    Returns:
        {"items": [...], "query": str, "search_type_used": "1"|"2", "total": int}
        Each item contains: id, sku, skuCode, name, sourceUrl (1688 link),
        alibabaProductId, imgUrl, state, etc.
    """
    query = str(query).strip()
    # Empty query = fetch all without keyword filter (used by backfill scripts)

    with browser_automation_lock(
        task_code="supply_pairing_search",
        timeout_seconds=120,
        command=f"query={query} status={status}",
    ):
        with _open_supply_page(cdp_url) as page:
            # Try SKU search first
            try:
                items = _search_once(page, query, search_type="1", status=status,
                                     page_size=page_size)
                if items:
                    return {
                        "items": items,
                        "query": query,
                        "search_type_used": "1",
                        "total": len(items),
                    }
            except Exception:
                log.warning("SKU search failed for %r, trying keyword", query, exc_info=True)

            # Fallback to keyword search
            try:
                items = _search_once(page, query, search_type="2", status=status,
                                     page_size=page_size)
                return {
                    "items": items,
                    "query": query,
                    "search_type_used": "2",
                    "total": len(items),
                }
            except Exception:
                log.exception("Keyword search failed for %r", query)
                raise


def extract_1688_url(item: dict[str, Any]) -> str | None:
    """Extract the 1688 purchase URL from a supply pairing item."""
    url = item.get("sourceUrl")
    if url:
        return str(url)
    # Some items may have URL inside alibabaProductList
    alibaba_list = item.get("alibabaProductList") or []
    for prod in alibaba_list:
        if isinstance(prod, dict):
            source = prod.get("sourceUrl")
            if source:
                return str(source)
    return None
