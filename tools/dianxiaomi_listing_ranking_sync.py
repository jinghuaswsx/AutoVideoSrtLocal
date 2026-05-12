"""Collect Dianxiaomi Listing sales Top1000 ranking for Mingkong selection.

Spec: docs/superpowers/specs/2026-05-12-dianxiaomi-listing-ranking-sync.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import scheduled_tasks
from appcore.db import get_conn, query
from tools.shopifyid_dianxiaomi_sync import _connect_existing_browser_context


TASK_CODE = "dianxiaomi_listing_ranking_sync"
TASK_NAME = "店小秘 Listing 销量 Top1000"

DXM02_BROWSER_CDP_URL = "http://127.0.0.1:9223"
DXM02_BROWSER_SERVICE_NAME = "autovideosrt-dxm02-mk-vnc.service"
LISTING_PAGE_URL = "https://www.dianxiaomi.com/web/stat/salesStatistics"
LISTING_API_URL = "https://www.dianxiaomi.com/api/stat/product/statSalesPageListNew.json"

DEFAULT_START_DATE = date(2026, 4, 23)
DEFAULT_TARGET_ROWS = 1000
DEFAULT_PAGE_SIZE = 100
DEFAULT_DAILY_OFFSET_DAYS = 1
OUTPUT_DIR = REPO_ROOT / "output" / "dianxiaomi_listing_ranking_sync"


ListingFetchPage = Callable[[date, int, int], dict[str, Any]]


def parse_yyyy_mm_dd(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def _date_text(value: str | date | datetime) -> str:
    return parse_yyyy_mm_dd(value).isoformat()


def _iter_dates(start_date: date, end_date: date) -> list[date]:
    if end_date < start_date:
        return []
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def resolve_daily_target_date(*, today: date | None = None, offset_days: int = DEFAULT_DAILY_OFFSET_DAYS) -> date:
    base = today or date.today()
    return base - timedelta(days=max(0, int(offset_days)))


def resolve_rolling_dates(
    *,
    today: date | None = None,
    rolling_days: int = 7,
    offset_days: int = 0,
) -> list[date]:
    end_date = resolve_daily_target_date(today=today, offset_days=offset_days)
    safe_days = max(1, int(rolling_days))
    start_date = end_date - timedelta(days=safe_days - 1)
    return _iter_dates(start_date, end_date)


def build_listing_payload(
    snapshot_date: str | date | datetime,
    *,
    page_no: int,
    page_size: int = DEFAULT_PAGE_SIZE,
    sort_type: str = "paidProductCount",
    is_desc: str = "1",
) -> dict[str, Any]:
    day = _date_text(snapshot_date)
    return {
        "shopIds": "all",
        "shopGroupId": "",
        "sortType": sort_type,
        "isDesc": is_desc,
        "pageNo": int(page_no),
        "pageSize": int(page_size),
        "beginDate": day,
        "endDate": day,
        "searchType": "productId",
        "searchValue": "",
        "searchCondition": "2",
    }


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        text = text.replace("CNY", "").replace("USD", "").replace("$", "").replace("¥", "").strip()
    else:
        text = value
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _format_cny(value: Any) -> str:
    amount = _as_float(value)
    if amount is None:
        return str(value or "").strip()
    return f"CNY {amount:.2f}"


def _format_percent(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value).strip()
    if text.endswith("%"):
        return text
    amount = _as_float(text)
    if amount is None:
        return text
    if amount == int(amount):
        return f"{int(amount)}%"
    return f"{amount:g}%"


def normalize_listing_row(
    row: Mapping[str, Any],
    *,
    snapshot_date: date,
    rank_position: int,
) -> dict[str, Any]:
    product_id = str(row.get("productId") or row.get("shopifyProductId") or "").strip()
    product_name = str(row.get("productName") or row.get("title") or "").strip()
    product_url = str(row.get("sourceUrl") or row.get("productUrl") or row.get("onlineUrl") or "").strip()
    store = str(row.get("shopName") or row.get("store") or row.get("shopId") or "").strip()
    platform = str(row.get("platform") or row.get("platformName") or "").strip()

    return {
        "product_id": product_id,
        "product_name": product_name,
        "product_url": product_url,
        "store": store,
        "platform": platform,
        "parent_sku": str(row.get("parentSku") or row.get("parent_sku") or "").strip(),
        "order_count": _as_int(row.get("paidOrderCount", row.get("orderCount"))),
        "sales_count": _as_int(row.get("paidProductCount", row.get("salesCount"))),
        "revenue_main": _format_cny(row.get("paidAmountCny", row.get("revenue"))),
        "revenue_split": _format_cny(row.get("averagePaidAmountCny", row.get("revenueSplit"))),
        "refund_orders": _as_int(row.get("refundOrderCount", row.get("refundOrders"))),
        "refund_qty": _as_int(row.get("refundProductCount", row.get("refundQty"))),
        "refund_amt": _format_cny(row.get("refundAmountCny", row.get("refundAmt"))),
        "refund_rate": _format_percent(row.get("refundRate")),
        "media_product_id": None,
        "snapshot_date": snapshot_date,
        "rank_position": int(rank_position),
    }


def ensure_dianxiaomi_success(payload: Mapping[str, Any]) -> None:
    try:
        code = int(payload.get("code", 0))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Dianxiaomi response has invalid code: {payload!r}") from exc
    if code != 0:
        raise RuntimeError(f"Dianxiaomi API failed: code={payload.get('code')} msg={payload.get('msg')}")


def extract_listing_page(payload: Mapping[str, Any]) -> dict[str, Any]:
    ensure_dianxiaomi_success(payload)
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    page = data.get("page") if isinstance(data.get("page"), Mapping) else {}
    items = page.get("list") or []
    return {
        "items": [item for item in items if isinstance(item, Mapping)],
        "page_no": _as_int(page.get("pageNo"), 1),
        "page_size": _as_int(page.get("pageSize"), DEFAULT_PAGE_SIZE),
        "total_size": _as_int(page.get("totalSize")),
        "total_page": _as_int(page.get("totalPage")),
    }


def collect_top_rankings_for_date(
    snapshot_date: date,
    *,
    fetch_page: ListingFetchPage,
    target_rows: int = DEFAULT_TARGET_ROWS,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pages_fetched = 0
    api_total_size = 0
    api_total_page = 0
    max_pages = max(1, (int(target_rows) + int(page_size) - 1) // int(page_size))

    for page_no in range(1, max_pages + 1):
        payload = fetch_page(snapshot_date, page_no, page_size)
        page = extract_listing_page(payload)
        pages_fetched += 1
        if page_no == 1:
            api_total_size = int(page["total_size"])
            api_total_page = int(page["total_page"])

        for index, raw in enumerate(page["items"]):
            rank_position = (page_no - 1) * page_size + index + 1
            normalized = normalize_listing_row(
                raw,
                snapshot_date=snapshot_date,
                rank_position=rank_position,
            )
            if normalized["product_id"]:
                rows.append(normalized)
            if len(rows) >= target_rows:
                break

        if len(rows) >= target_rows:
            break
        if not page["items"]:
            break
        if api_total_page and page_no >= api_total_page:
            break

    return rows[:target_rows], {
        "pages_fetched": pages_fetched,
        "api_total_size": api_total_size,
        "api_total_page": api_total_page,
        "rows_fetched": len(rows[:target_rows]),
    }


def select_missing_dates(
    *,
    start_date: date,
    end_date: date,
    existing_counts: Mapping[str | date | datetime, int],
    target_rows: int = DEFAULT_TARGET_ROWS,
) -> list[date]:
    normalized_counts = {
        parse_yyyy_mm_dd(key): int(value or 0)
        for key, value in existing_counts.items()
    }
    return [
        day
        for day in _iter_dates(start_date, end_date)
        if normalized_counts.get(day, 0) < target_rows
    ]


def load_existing_counts(start_date: date, end_date: date) -> dict[date, int]:
    rows = query(
        """
        SELECT snapshot_date, COUNT(*) AS cnt
        FROM dianxiaomi_rankings
        WHERE snapshot_date BETWEEN %s AND %s
        GROUP BY snapshot_date
        """,
        (start_date, end_date),
    )
    return {
        parse_yyyy_mm_dd(row["snapshot_date"]): int(row.get("cnt") or 0)
        for row in rows
    }


def _clean_name(name: str) -> str:
    text = re.sub(r"[^\w\s-]", "", name)
    return re.sub(r"\s+", " ", text).strip().lower()


def _fetch_one(cursor, sql: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
    cursor.execute(sql, args)
    return cursor.fetchone()


def _match_media_product_id(cursor, row: Mapping[str, Any]) -> int | None:
    product_url = str(row.get("product_url") or "")
    match = re.search(r"/products/([^/?#]+)", product_url)
    if match:
        media = _fetch_one(
            cursor,
            "SELECT id FROM media_products WHERE product_code = %s AND deleted_at IS NULL LIMIT 1",
            (match.group(1),),
        )
        if media:
            return int(media["id"])

    product_name = str(row.get("product_name") or "").strip()
    if product_name:
        media = _fetch_one(
            cursor,
            "SELECT id FROM media_products WHERE name = %s AND deleted_at IS NULL LIMIT 1",
            (product_name,),
        )
        if media:
            return int(media["id"])

        keyword = _clean_name(product_name)[:40]
        if keyword:
            media = _fetch_one(
                cursor,
                "SELECT id FROM media_products WHERE LOWER(name) LIKE %s AND deleted_at IS NULL LIMIT 1",
                (f"%{keyword}%",),
            )
            if media:
                return int(media["id"])
    return None


def persist_rankings(snapshot_date: date, rows: list[dict[str, Any]]) -> dict[str, int]:
    conn = get_conn()
    matched = 0
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM dianxiaomi_rankings WHERE snapshot_date = %s",
                (snapshot_date,),
            )
            for row in rows:
                media_product_id = _match_media_product_id(cursor, row)
                if media_product_id:
                    matched += 1
                cursor.execute(
                    """
                    INSERT INTO dianxiaomi_rankings
                        (product_id, product_name, product_url, store, platform, parent_sku,
                         order_count, sales_count, revenue_main, revenue_split,
                         refund_orders, refund_qty, refund_amt, refund_rate,
                         media_product_id, snapshot_date, rank_position)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        product_name=VALUES(product_name),
                        product_url=VALUES(product_url),
                        store=VALUES(store),
                        platform=VALUES(platform),
                        parent_sku=VALUES(parent_sku),
                        order_count=VALUES(order_count),
                        sales_count=VALUES(sales_count),
                        revenue_main=VALUES(revenue_main),
                        revenue_split=VALUES(revenue_split),
                        refund_orders=VALUES(refund_orders),
                        refund_qty=VALUES(refund_qty),
                        refund_amt=VALUES(refund_amt),
                        refund_rate=VALUES(refund_rate),
                        media_product_id=VALUES(media_product_id),
                        rank_position=VALUES(rank_position)
                    """,
                    (
                        row["product_id"],
                        row["product_name"],
                        row["product_url"],
                        row["store"],
                        row["platform"],
                        row["parent_sku"],
                        row["order_count"],
                        row["sales_count"],
                        row["revenue_main"],
                        row["revenue_split"],
                        row["refund_orders"],
                        row["refund_qty"],
                        row["refund_amt"],
                        row["refund_rate"],
                        media_product_id,
                        snapshot_date,
                        row["rank_position"],
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"stored_rows": len(rows), "matched_media_products": matched}


def guard_against_windows_local_mysql() -> None:
    if os.name != "nt":
        return
    from config import DB_HOST, DB_PORT

    host = str(DB_HOST or "").strip().lower()
    if host in {"127.0.0.1", "localhost", "::1"} and int(DB_PORT) == 3306:
        raise RuntimeError(
            "项目规则禁止在 Windows 本机连接 127.0.0.1:3306 MySQL；"
            "请在服务器环境运行店小秘 Listing 排名采集。"
        )


def _stringify_form_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    return {str(key): "" if value is None else str(value) for key, value in payload.items()}


def _parse_response_text(*, ok: bool, status: int | None, text: str) -> dict[str, Any]:
    if not ok:
        raise RuntimeError(f"Dianxiaomi request failed: HTTP {status}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Dianxiaomi returned non-JSON content: {text[:200]}") from exc
    ensure_dianxiaomi_success(payload)
    return payload


def _build_context_fetcher(context, *, timeout_ms: int) -> ListingFetchPage:
    def _fetch(snapshot_date: date, page_no: int, page_size: int) -> dict[str, Any]:
        payload = build_listing_payload(
            snapshot_date,
            page_no=page_no,
            page_size=page_size,
        )
        response = context.request.post(
            LISTING_API_URL,
            form=_stringify_form_payload(payload),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://www.dianxiaomi.com",
                "Referer": LISTING_PAGE_URL,
            },
            timeout=timeout_ms,
        )
        response_ok = getattr(response, "ok", False)
        if callable(response_ok):
            response_ok = response_ok()
        return _parse_response_text(
            ok=bool(response_ok),
            status=getattr(response, "status", None),
            text=response.text(),
        )

    return _fetch


def _select_dates(args: argparse.Namespace) -> tuple[list[date], dict[str, Any]]:
    if args.mode == "rolling":
        if args.target_date:
            target_date = parse_yyyy_mm_dd(args.target_date)
            selected = resolve_rolling_dates(
                today=target_date,
                rolling_days=args.rolling_days,
                offset_days=0,
            )
        else:
            selected = resolve_rolling_dates(
                rolling_days=args.rolling_days,
                offset_days=args.daily_offset_days,
            )
        return selected, {
            "mode": "rolling",
            "date_from": selected[0].isoformat(),
            "date_to": selected[-1].isoformat(),
            "rolling_days": int(args.rolling_days),
            "skipped_complete_dates": 0,
            "refresh_existing": True,
        }
    if args.mode == "daily":
        target_date = parse_yyyy_mm_dd(args.target_date) if args.target_date else resolve_daily_target_date(
            offset_days=args.daily_offset_days
        )
        return [target_date], {
            "mode": "daily",
            "date_from": target_date.isoformat(),
            "date_to": target_date.isoformat(),
            "skipped_complete_dates": 0,
        }
    if args.mode == "date":
        target_date = parse_yyyy_mm_dd(args.target_date)
        return [target_date], {
            "mode": "date",
            "date_from": target_date.isoformat(),
            "date_to": target_date.isoformat(),
            "skipped_complete_dates": 0,
        }

    start_date = parse_yyyy_mm_dd(args.start_date)
    end_date = parse_yyyy_mm_dd(args.end_date) if args.end_date else date.today()
    existing_counts = load_existing_counts(start_date, end_date)
    missing = select_missing_dates(
        start_date=start_date,
        end_date=end_date,
        existing_counts=existing_counts,
        target_rows=args.target_rows,
    )
    selected = missing[: max(1, int(args.max_days_per_run))]
    return selected, {
        "mode": "backfill",
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "missing_dates": [item.isoformat() for item in missing],
        "skipped_complete_dates": max(0, len(_iter_dates(start_date, end_date)) - len(missing)),
    }


def _write_report(summary: dict[str, Any]) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUTPUT_DIR / f"dianxiaomi-listing-ranking-sync-{stamp}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path)


def run_collection(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    guard_against_windows_local_mysql()
    selected_dates, base_summary = _select_dates(args)
    summary: dict[str, Any] = {
        **base_summary,
        "target_rows": int(args.target_rows),
        "page_size": int(args.page_size),
        "selected_dates": [item.isoformat() for item in selected_dates],
        "fetched_days": 0,
        "pages_fetched": 0,
        "rows_fetched": 0,
        "rows_stored": 0,
        "matched_media_products": 0,
        "incomplete_dates": [],
        "daily_offset_days": int(args.daily_offset_days),
        "rolling_days": int(getattr(args, "rolling_days", 0) or 0),
    }
    if not selected_dates:
        output_file = _write_report(summary)
        return summary, output_file

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser, context = _connect_existing_browser_context(
            playwright,
            args.browser_cdp_url,
            browser_service_name=DXM02_BROWSER_SERVICE_NAME,
        )
        del browser
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LISTING_PAGE_URL, wait_until="domcontentloaded", timeout=args.timeout_seconds * 1000)
        page.wait_for_timeout(1000)
        fetch_page = _build_context_fetcher(context, timeout_ms=args.timeout_seconds * 1000)

        for snapshot_date in selected_dates:
            rows, fetch_stats = collect_top_rankings_for_date(
                snapshot_date,
                fetch_page=fetch_page,
                target_rows=args.target_rows,
                page_size=args.page_size,
            )
            if not rows and args.mode == "rolling":
                summary["incomplete_dates"].append({
                    "date": snapshot_date.isoformat(),
                    "rows": 0,
                    "api_total_size": fetch_stats["api_total_size"],
                    "skipped_persist": True,
                })
                continue
            persist_stats = persist_rankings(snapshot_date, rows)
            summary["fetched_days"] += 1
            summary["pages_fetched"] += int(fetch_stats["pages_fetched"])
            summary["rows_fetched"] += int(fetch_stats["rows_fetched"])
            summary["rows_stored"] += int(persist_stats["stored_rows"])
            summary["matched_media_products"] += int(persist_stats["matched_media_products"])
            if len(rows) < args.target_rows:
                summary["incomplete_dates"].append({
                    "date": snapshot_date.isoformat(),
                    "rows": len(rows),
                    "api_total_size": fetch_stats["api_total_size"],
                })

    output_file = _write_report(summary)
    return summary, output_file


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Dianxiaomi Listing sales ranking into dianxiaomi_rankings.")
    parser.add_argument("--mode", choices=("backfill", "daily", "date", "rolling"), default="backfill")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE.isoformat())
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--target-date", default="")
    parser.add_argument("--daily-offset-days", type=int, default=DEFAULT_DAILY_OFFSET_DAYS)
    parser.add_argument("--rolling-days", type=int, default=7)
    parser.add_argument("--max-days-per-run", type=int, default=1)
    parser.add_argument("--target-rows", type=int, default=DEFAULT_TARGET_ROWS)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument(
        "--browser-cdp-url",
        default=os.environ.get("DXM_LISTING_BROWSER_CDP_URL", DXM02_BROWSER_CDP_URL),
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    guard_against_windows_local_mysql()
    run_id = scheduled_tasks.start_run(TASK_CODE)
    try:
        summary, output_file = run_collection(args)
    except Exception as exc:
        scheduled_tasks.finish_run(
            run_id,
            status="failed",
            summary={"error": str(exc), "mode": args.mode},
            error_message=str(exc),
        )
        raise
    scheduled_tasks.finish_run(
        run_id,
        status="success",
        summary=summary,
        output_file=output_file,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
