import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import order_analytics as oa

ORDER_URL = "https://www.dianxiaomi.com/api/package/list.json"
PROFIT_URL = "https://www.dianxiaomi.com/api/orderProfit/getOrderProfit.json"
ORDER_PAGE_URL = "https://www.dianxiaomi.com/web/order/paid"
SERVER_BROWSER_CDP_URL = "http://127.0.0.1:9223"
DEFAULT_STATES = ["paid", "approved", "processed", "allocated", "shipped"]
BROWSER_MODES = ("auto", "server-cdp")
DXM_ENVIRONMENTS = {
    "DXM-01": {
        "label": "Shopify ID 同步店小秘账号",
        "cdp_url": "http://127.0.0.1:9222",
        "novnc_url": "http://127.0.0.1:6080/vnc.html",
        "profile": "/data/autovideosrt/browser/profiles/shared",
    },
    "DXM-02": {
        "label": "明空选品店小秘账号",
        "cdp_url": "http://127.0.0.1:9223",
        "novnc_url": "http://127.0.0.1:6081/vnc.html",
        "profile": "/data/autovideosrt/browser/profiles/mk-selection",
    },
}


@dataclass(frozen=True)
class OrderPage:
    total_page: int
    page_no: int
    orders: list[dict[str, Any]]


def iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def build_order_payload(day: date, page_no: int, state: str) -> dict[str, Any]:
    return {
        "pageNo": page_no,
        "pageSize": 100,
        "shopId": "-1",
        "state": state,
        "platform": "",
        "isSearch": 0,
        "searchType": "orderId",
        "authId": "-1",
        "startTime": f"{day:%Y-%m-%d} 00:00:00",
        "endTime": f"{day:%Y-%m-%d} 23:59:59",
        "country": "",
        "orderField": "order_pay_time",
        "isVoided": 0,
        "isRemoved": 0,
        "ruleId": "-1",
        "sysRule": "",
        "applyType": "",
        "applyStatus": "",
        "printJh": "-1",
        "printMd": "-1",
        "commitPlatform": "",
        "productStatus": "",
        "jhComment": "-1",
        "storageId": 0,
        "isOversea": "-1",
        "isFree": 0,
        "isBatch": 0,
        "history": "",
        "custom": "-1",
        "timeOut": 0,
        "refundStatus": 0,
        "buyerAccount": "",
        "forbiddenStatus": "-1",
        "forbiddenReason": 0,
        "behindTrack": "-1",
        "orderId": "",
        "axios_cancelToken": "true",
    }


def ensure_dianxiaomi_success(payload: dict[str, Any]) -> None:
    try:
        code = int(payload.get("code"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"店小秘接口返回异常：缺少可识别的 code 字段，payload={payload!r}") from exc
    if code != 0:
        raise RuntimeError(f"店小秘接口返回异常：code={payload.get('code')} msg={payload.get('msg')}")


def extract_order_page(payload: dict[str, Any]) -> OrderPage:
    ensure_dianxiaomi_success(payload)
    page = ((payload.get("data") or {}).get("page") or {})
    return OrderPage(
        total_page=int(page.get("totalPage") or 0),
        page_no=int(page.get("pageNo") or 0),
        orders=[item for item in (page.get("list") or []) if isinstance(item, dict)],
    )


def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def _normalize_csv_list(text: str) -> list[str]:
    return [item.strip().lower() for item in (text or "").split(",") if item.strip()]


def _summary_template() -> dict[str, int]:
    return {
        "total_pages": 0,
        "fetched_orders": 0,
        "fetched_lines": 0,
        "inserted_lines": 0,
        "updated_lines": 0,
        "skipped_lines": 0,
    }


def _coerce_order_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        try:
            return datetime.fromtimestamp(timestamp).replace(microsecond=0)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return _coerce_order_datetime(int(text))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19] if fmt.endswith("%S") else text[:16], fmt)
        except ValueError:
            continue
    return None


def _order_reference_date(order: dict[str, Any], state: str) -> date | None:
    keys = (
        ("shippedTime", "shippedTimeStr", "commitPlatformTime", "commitPlatformTimeStr")
        if state == "shipped"
        else ("orderPayTime", "paidTime", "orderPayTimeStr", "paidTimeStr", "orderCreateTime")
    )
    for key in keys:
        parsed = _coerce_order_datetime(order.get(key))
        if parsed:
            return parsed.date()
    return None


def _order_in_date_range(order: dict[str, Any], state: str, start_date: date, end_date: date) -> bool:
    ref_date = _order_reference_date(order, state)
    return ref_date is not None and start_date <= ref_date <= end_date


def _normalize_page_orders(
    *,
    orders: list[dict[str, Any]],
    scope: oa.DianxiaomiProductScope,
    fetch_profits: Callable[[list[str]], dict[str, dict[str, Any]]],
    summary: dict[str, int],
) -> list[dict[str, Any]]:
    matched_orders: list[dict[str, Any]] = []
    skipped_total = 0
    for order in orders:
        rows, skipped = oa.normalize_dianxiaomi_order(order, scope, {})
        skipped_total += skipped
        if rows:
            matched_orders.append(order)
    summary["skipped_lines"] += skipped_total
    if not matched_orders:
        return []

    package_ids = [str(order.get("id")) for order in matched_orders if order.get("id")]
    profits = fetch_profits(package_ids) if package_ids else {}
    page_rows: list[dict[str, Any]] = []
    for order in matched_orders:
        rows, _skipped = oa.normalize_dianxiaomi_order(order, scope, profits)
        summary["fetched_lines"] += len(rows)
        page_rows.extend(rows)
    return page_rows


def _extract_profit_rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ensure_dianxiaomi_success(payload)
    data = payload.get("data") or {}
    if isinstance(data, dict):
        rows = data.get("list") or data.get("orderProfitList") or data.get("data") or data
    else:
        rows = data
    if isinstance(rows, dict):
        return {str(key): value for key, value in rows.items() if isinstance(value, dict)}
    result: dict[str, dict[str, Any]] = {}
    if isinstance(rows, list):
        for item in rows:
            if not isinstance(item, dict):
                continue
            package_id = str(item.get("packageId") or item.get("id") or item.get("orderPackageId") or "").strip()
            if package_id:
                result[package_id] = item
    return result


def run_import(
    *,
    start_date: date,
    end_date: date,
    site_codes: list[str],
    states: list[str],
    fetch_orders: Callable[[date, int, str], dict[str, Any]],
    fetch_profits: Callable[[list[str]], dict[str, dict[str, Any]]],
    dry_run: bool = False,
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date 不能早于 start_date")
    site_codes = [code.strip().lower() for code in site_codes if code.strip()]
    states = [state.strip() for state in states if state.strip()]
    scope = oa.build_dianxiaomi_product_scope(site_codes)
    batch_id = None
    summary = _summary_template()
    if not dry_run:
        batch_id = oa.start_dianxiaomi_order_import_batch(
            start_date.isoformat(),
            end_date.isoformat(),
            site_codes,
            len(scope.by_shopify_id),
        )
    try:
        for state in states:
            for day in iter_dates(start_date, end_date):
                print(f"[dianxiaomi-order-import] state={state} day={day.isoformat()}", flush=True)
                first_payload = fetch_orders(day, 1, state)
                first_page = extract_order_page(first_payload)
                total_page = max(first_page.total_page, 1 if first_page.orders else 0)
                for page_no in range(1, total_page + 1):
                    page = first_page if page_no == 1 else extract_order_page(fetch_orders(day, page_no, state))
                    summary["total_pages"] += 1
                    summary["fetched_orders"] += len(page.orders)
                    page_rows = _normalize_page_orders(
                        orders=page.orders,
                        scope=scope,
                        fetch_profits=fetch_profits,
                        summary=summary,
                    )
                    if page_rows and not dry_run:
                        result = oa.upsert_dianxiaomi_order_lines(int(batch_id), page_rows)
                        summary["inserted_lines"] += int(result.get("rows") or 0)
                        summary["updated_lines"] += max(0, int(result.get("affected") or 0) - int(result.get("rows") or 0))
        status = "dry_run" if dry_run else "success"
        if batch_id is not None:
            oa.finish_dianxiaomi_order_import_batch(batch_id, status, summary)
        return {
            "batch_id": batch_id,
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
            "site_codes": site_codes,
            "states": states,
            "dry_run": dry_run,
            "summary": summary,
        }
    except Exception as exc:
        if batch_id is not None:
            oa.finish_dianxiaomi_order_import_batch(batch_id, "failed", summary, error_message=str(exc))
        raise


def _post_form_via_page(page, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, 5):
        try:
            result = page.evaluate(
                """
                async ({ url, payload }) => {
                  const body = new URLSearchParams();
                  for (const [key, value] of Object.entries(payload)) {
                    body.append(key, String(value ?? ""));
                  }
                  const response = await fetch(url, {
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
                {"url": url, "payload": payload},
            )
        except Exception as exc:
            last_error = exc
            print(
                f"[dianxiaomi-order-import] retry {attempt}/4 url={url} error={exc}",
                flush=True,
            )
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            page.wait_for_timeout(1500 * attempt)
            continue
        text = str(result.get("text") or "")
        if result.get("ok"):
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                last_error = RuntimeError(f"店小秘接口返回了非 JSON 内容：{text[:200]}")
        else:
            last_error = RuntimeError(f"店小秘接口请求失败：HTTP {result.get('status')}")
        print(
            f"[dianxiaomi-order-import] retry {attempt}/4 url={url} error={last_error}",
            flush=True,
        )
        page.wait_for_timeout(1500 * attempt)
    raise last_error or RuntimeError("店小秘接口请求失败")


def _fetch_orders_via_page(page, day: date, page_no: int, state: str) -> dict[str, Any]:
    return _post_form_via_page(page, ORDER_URL, build_order_payload(day, page_no, state))


def _fetch_profits_via_page(page, package_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not package_ids:
        return {}
    payload = _post_form_via_page(page, PROFIT_URL, {"packageIds": ",".join(package_ids)})
    return _extract_profit_rows(payload)


def run_import_from_server_browser(
    *,
    start_date_text: str,
    end_date_text: str,
    site_codes: list[str],
    states: list[str] | None = None,
    dxm_env: str = "DXM-01",
    browser_cdp_url: str | None = None,
    dry_run: bool = False,
    skip_login_prompt: bool = True,
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    env = DXM_ENVIRONMENTS.get(dxm_env)
    if not env:
        raise ValueError(f"未知店小秘环境编号：{dxm_env}")
    resolved_cdp_url = browser_cdp_url or str(env["cdp_url"])
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(resolved_cdp_url)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(ORDER_PAGE_URL, wait_until="domcontentloaded")
            if not skip_login_prompt:
                input("如果还没登录，请先登录店小秘；登录完成后按回车继续...")
                page.goto(ORDER_PAGE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
            report = run_import(
                start_date=_parse_date(start_date_text),
                end_date=_parse_date(end_date_text),
                site_codes=site_codes,
                states=states or DEFAULT_STATES,
                fetch_orders=lambda day, page_no, state: _fetch_orders_via_page(page, day, page_no, state),
                fetch_profits=lambda package_ids: _fetch_profits_via_page(page, package_ids),
                dry_run=dry_run,
            )
            report["dxm_env"] = dxm_env
            report["dxm_env_label"] = env["label"]
            report["browser_cdp_url"] = resolved_cdp_url
            return report
        finally:
            browser.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从店小秘订单接口导入 NewJoy / omurio 订单明细")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--sites", default="newjoy,omurio")
    parser.add_argument("--states", default=",".join(DEFAULT_STATES))
    parser.add_argument("--dxm-env", choices=sorted(DXM_ENVIRONMENTS.keys()), default="DXM-01")
    parser.add_argument("--browser-mode", choices=BROWSER_MODES, default=os.environ.get("DXM_ORDER_BROWSER_MODE", "auto"))
    parser.add_argument("--browser-cdp-url", default=os.environ.get("DXM_ORDER_BROWSER_CDP_URL", ""))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-login-prompt", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.browser_mode not in ("auto", "server-cdp"):
        raise ValueError(f"Unsupported browser mode: {args.browser_mode}")
    report = run_import_from_server_browser(
        start_date_text=args.start_date,
        end_date_text=args.end_date,
        site_codes=_normalize_csv_list(args.sites),
        states=_normalize_csv_list(args.states),
        dxm_env=args.dxm_env,
        browser_cdp_url=args.browser_cdp_url or None,
        dry_run=args.dry_run,
        skip_login_prompt=args.skip_login_prompt,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - manual execution entrypoint
    raise SystemExit(main())
