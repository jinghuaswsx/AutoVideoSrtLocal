from __future__ import annotations

import json
import logging
import statistics
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable, Iterator

from appcore.browser_automation_lock import browser_automation_lock
from appcore.db import query


log = logging.getLogger(__name__)

ORDER_LIST_URL = "https://www.dianxiaomi.com/api/package/list.json"
ORDER_PAGE_URL = "https://www.dianxiaomi.com/web/order/all"
DEFAULT_DXM_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_LOOKBACK_DAYS = 30
# Orders younger than this are excluded — logistic fee is not finalized within
# the first ~2 days after fulfillment, per the user's domain knowledge.
SETTLEMENT_DELAY_DAYS = 2
DEFAULT_PAGE_SIZE = 200
MAX_PAGES = 60


class ParcelCostSuggestError(RuntimeError):
    pass


def pick_primary_sku_and_shop(product_id: int) -> tuple[str, str]:
    rows = query(
        """
        SELECT product_sku, dxm_shop_id, COUNT(*) AS cnt
        FROM dianxiaomi_order_lines
        WHERE product_id = %s
          AND product_sku IS NOT NULL
          AND dxm_shop_id IS NOT NULL
        GROUP BY product_sku, dxm_shop_id
        ORDER BY cnt DESC
        LIMIT 1
        """,
        (int(product_id),),
    )
    if not rows:
        raise ParcelCostSuggestError("no_orders")
    row = rows[0]
    return str(row["product_sku"]), str(row["dxm_shop_id"])


def build_order_payload(
    *,
    page_no: int,
    page_size: int,
    shop_id: str,
    start_time: datetime,
    end_time: datetime,
) -> dict[str, str]:
    return {
        "pageNo": str(page_no),
        "pageSize": str(page_size),
        "shopId": str(shop_id),
        "state": "",
        "platform": "",
        "isSearch": "1",
        "searchType": "orderId",
        "searchValue": "",
        "authId": "-1",
        "startTime": start_time.strftime("%Y-%m-%d 00:00:00"),
        "endTime": end_time.strftime("%Y-%m-%d 23:59:59"),
        "country": "",
        "orderField": "order_pay_time",
        "isVoided": "-1",
        "isRemoved": "-1",
        "ruleId": "-1",
        "sysRule": "",
        "applyType": "",
        "applyStatus": "",
        "printJh": "-1",
        "printMd": "-1",
        "commitPlatform": "",
        "productStatus": "",
        "jhComment": "-1",
        "storageId": "0",
        "isOversea": "-1",
        "isFree": "-1",
        "isBatch": "-1",
        "history": "",
        "custom": "-1",
        "timeOut": "0",
        "refundStatus": "0",
        "forbiddenStatus": "-1",
        "forbiddenReason": "0",
        "behindTrack": "-1",
        "orderId": "",
        "axios_cancelToken": "true",
    }


def post_form_via_page(page, url: str, payload: dict[str, str]) -> dict[str, Any]:
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
    raise last_error or RuntimeError("dianxiaomi request failed")


def fetch_orders_in_window(
    page,
    *,
    shop_id: str,
    start_time: datetime,
    end_time: datetime,
    max_pages: int = MAX_PAGES,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page_no in range(1, max_pages + 1):
        payload = build_order_payload(
            page_no=page_no,
            page_size=page_size,
            shop_id=shop_id,
            start_time=start_time,
            end_time=end_time,
        )
        body = post_form_via_page(page, ORDER_LIST_URL, payload)
        try:
            code = int(body.get("code"))
        except (TypeError, ValueError):
            raise ParcelCostSuggestError(f"dxm_unexpected_response:{body!r}")
        if code != 0:
            raise ParcelCostSuggestError(f"dxm_error:{body.get('msg')}")
        items = (((body.get("data") or {}).get("page") or {}).get("list") or [])
        out.extend(items)
        if len(items) < page_size:
            break
    return out


def filter_logistic_fees(orders: list[dict[str, Any]], target_sku: str) -> list[float]:
    fees: list[float] = []
    target_sku = str(target_sku)
    for order in orders:
        product_lines = order.get("productList") or []
        if not isinstance(product_lines, list):
            continue
        matched = False
        for line in product_lines:
            if not isinstance(line, dict):
                continue
            if any(line.get(k) == target_sku for k in ("displaySku", "productSku", "sku")):
                matched = True
                break
        if not matched:
            continue
        raw = order.get("logisticFee")
        if raw is None or raw == "" or raw == "--" or raw == "-":
            continue
        try:
            fees.append(float(raw))
        except (TypeError, ValueError):
            continue
    return fees


def compute_suggestion(fees: list[float]) -> dict[str, Any]:
    if not fees:
        return {"sample_size": 0, "median": None, "mean": None, "min": None, "max": None}
    fees_sorted = sorted(fees)
    return {
        "sample_size": len(fees),
        "median": round(statistics.median(fees_sorted), 2),
        "mean": round(sum(fees) / len(fees), 2),
        "min": round(fees_sorted[0], 2),
        "max": round(fees_sorted[-1], 2),
    }


@contextmanager
def open_dxm_page(cdp_url: str = DEFAULT_DXM_CDP_URL) -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        existing = next((x for x in ctx.pages if "dianxiaomi.com" in (x.url or "")), None)
        owns = False
        if existing is None:
            page = ctx.new_page()
            page.goto(ORDER_PAGE_URL, wait_until="domcontentloaded", timeout=30000)
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


def suggest_parcel_cost(
    product_id: int,
    *,
    days: int = DEFAULT_LOOKBACK_DAYS,
    cdp_url: str = DEFAULT_DXM_CDP_URL,
    now_func: Callable[[], datetime] | None = None,
    page_provider: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    sku, shop_id = pick_primary_sku_and_shop(int(product_id))
    now = (now_func or datetime.now)()
    end_time = now - timedelta(days=SETTLEMENT_DELAY_DAYS)
    start_time = end_time - timedelta(days=int(days))

    page_cm = page_provider() if page_provider else open_dxm_page(cdp_url)
    with browser_automation_lock(
        task_code="parcel_cost_suggest",
        timeout_seconds=300,
        command=f"product_id={product_id} sku={sku} shop={shop_id}",
    ):
        with page_cm as page:
            orders = fetch_orders_in_window(
                page,
                shop_id=shop_id,
                start_time=start_time,
                end_time=end_time,
            )
    fees = filter_logistic_fees(orders, sku)
    suggestion = compute_suggestion(fees)
    return {
        "product_id": int(product_id),
        "sku": sku,
        "dxm_shop_id": shop_id,
        "lookback_days": int(days),
        "settlement_delay_days": SETTLEMENT_DELAY_DAYS,
        "window_start": start_time.strftime("%Y-%m-%d"),
        "window_end": end_time.strftime("%Y-%m-%d"),
        "orders_pulled": len(orders),
        **suggestion,
    }
