from __future__ import annotations

import argparse
import base64
import csv
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from random import uniform

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore.browser_automation_lock import BrowserAutomationLockTimeout
from appcore.meta_ads_cdp import DEFAULT_META_ADS_CDP_URL, meta_ads_cdp_lock


ACCOUNT_ID = os.environ.get("META_AD_EXPORT_ACCOUNT_ID", "1861285821213497")
BUSINESS_ID = os.environ.get("META_AD_EXPORT_BUSINESS_ID", "476723373113063")
CDP_URL = os.environ.get("META_AD_EXPORT_CDP_URL", DEFAULT_META_ADS_CDP_URL)
DEFAULT_CSV_PREFIX = "newjoyloo"
# 默认 column preset 沿用旧户 2110407576446225 在 Meta UI 里手工存的列模板（含购物
# 转化价值 / ROAS - 购物 / 加入购物车次数 等）。新增账户时必须在该账户下另建
# 同款 preset，并把 ID 配到 system_settings.meta_ad_accounts[*].column_preset，
# 否则导出会落到一组裸列、缺购买相关字段。
# Docs-anchor: docs/superpowers/specs/2026-05-09-ads-purchase-value-order-fallback-design.md
DEFAULT_COLUMN_PRESET = "1658418688523178"
LEVELS = {
    "campaigns": ("campaigns", "campaigns"),
    "adsets": ("adsets", "adsets"),
    "ads": ("ads", "ads"),
}
AUTH_FAILED = "auth_failed"
DOWNLOAD_URL_PATTERN = "*download_report*"
META_VALUE_COLUMNS = (
    "购物转化价值",
    "购买转化价值",
    "成效价值",
    "Website purchases conversion value",
    "Purchase conversion value",
    "Result value",
    "Results value",
)
META_ROAS_COLUMNS = (
    "广告花费回报 (ROAS) - 购物",
    "成效广告花费回报",
    "Purchase ROAS (return on ad spend)",
    "ROAS",
)


class ExportColumnValidationError(RuntimeError):
    pass


def _read_csv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        try:
            return [str(item or "").strip() for item in next(csv.reader(file_obj))]
        except StopIteration:
            return []


def validate_export_csv_has_meta_performance_columns(path: Path) -> dict[str, object]:
    header = _read_csv_header(path)
    header_set = {item for item in header if item}
    missing: list[str] = []
    if not any(column in header_set for column in META_VALUE_COLUMNS):
        missing.append("meta_value")
    if not any(column in header_set for column in META_ROAS_COLUMNS):
        missing.append("meta_roas")
    return {
        "ok": not missing,
        "missing": missing,
        "columns": header,
        "column_count": len(header),
    }


def validate_export_csv_or_raise(
    path: Path,
    *,
    account_id: str,
    level: str,
    day: date | str,
    column_preset: str,
) -> dict[str, object]:
    report = validate_export_csv_has_meta_performance_columns(path)
    if report["ok"]:
        return report
    raise ExportColumnValidationError(
        "Meta export CSV missing required Meta value/ROAS columns; "
        f"account_id={account_id} level={level} day={day} "
        f"column_preset={column_preset!r} missing={','.join(report['missing'])} "
        f"columns={report['columns']} file={path}"
    )


def _quarantine_invalid_export(path: Path) -> Path | None:
    if not path.exists():
        return None
    invalid = path.with_name(path.name + ".invalid_columns")
    invalid.unlink(missing_ok=True)
    path.replace(invalid)
    return invalid


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_url(
    level: str,
    day: date,
    *,
    account_id: str = ACCOUNT_ID,
    business_id: str = BUSINESS_ID,
    column_preset: str = DEFAULT_COLUMN_PRESET,
) -> str:
    ds = day.isoformat()
    preset = (column_preset or DEFAULT_COLUMN_PRESET).strip() or DEFAULT_COLUMN_PRESET
    return (
        f"https://adsmanager.facebook.com/adsmanager/manage/{level}?"
        f"act={account_id}&business_id={business_id}&global_scope_id={business_id}"
        f"&attribution_windows=default&column_preset={preset}"
        f"&date={ds}_{ds}&insights_date={ds}_{ds}&insights_selected_metrics=cpm"
    )


def _export_button(page):
    selectors = [
        'div[role="button"]:has-text("导出")',
        'div[role="button"]:has-text("Export")',
        'div[aria-label="导出"]',
        'div[aria-label="Export"]',
    ]
    for selector in selectors:
        button = page.locator(selector).first
        try:
            if button.count() > 0:
                return button
        except Exception:
            continue
    return page.locator(selectors[0]).first


def _is_login_page(page) -> bool:
    current_url = page.url.lower()
    if "business.facebook.com/business/loginpage" in current_url or "facebook.com/login" in current_url:
        return True
    try:
        body = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        return False
    return "log into ads manager" in body or "log in with facebook" in body


def _click_export_and_save(page, button, target: Path, *, timeout_ms: int = 150000) -> None:
    """Save the Meta CSV from the download response before Chrome cancels it."""
    state = {"done": False, "error": None}
    cdp = page.context.new_cdp_session(page)

    def on_request_paused(params):
        request_id = params["requestId"]
        url = params.get("request", {}).get("url", "")
        status = int(params.get("responseStatusCode") or 0)
        try:
            body_info = cdp.send("Fetch.getResponseBody", {"requestId": request_id})
            raw_body = body_info.get("body") or ""
            data = (
                base64.b64decode(raw_body)
                if body_info.get("base64Encoded")
                else raw_body.encode("utf-8")
            )
            content_type = ""
            for header in params.get("responseHeaders") or []:
                if str(header.get("name") or "").lower() == "content-type":
                    content_type = str(header.get("value") or "")
                    break
            if status != 200 or b"<html" in data[:200].lower() or len(data) <= 100:
                state["error"] = (
                    f"download response invalid: status={status}, "
                    f"content_type={content_type}, bytes={len(data)}, url={url[:200]}"
                )
            else:
                target.write_bytes(data)
                state["done"] = True
                print("INTERCEPT_SAVED", target.name, len(data), flush=True)
            cdp.send("Fetch.fulfillRequest", {"requestId": request_id, "responseCode": 204, "body": ""})
        except Exception as exc:  # noqa: BLE001 - keep the original export retry loop in control.
            state["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            try:
                cdp.send("Fetch.continueRequest", {"requestId": request_id})
            except Exception:
                pass

    cdp.on("Fetch.requestPaused", on_request_paused)
    cdp.send("Fetch.enable", {"patterns": [{"urlPattern": DOWNLOAD_URL_PATTERN, "requestStage": "Response"}]})
    try:
        button.click(timeout=30000)
        deadline = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < deadline:
            if state["done"]:
                return
            if state["error"]:
                raise RuntimeError(str(state["error"]))
            page.wait_for_timeout(1000)
        raise TimeoutError(f"Timed out waiting for Meta download response: {target.name}")
    finally:
        try:
            cdp.send("Fetch.disable")
        except Exception:
            pass


def export_one(
    page,
    out_dir: Path,
    level: str,
    label: str,
    day: date,
    *,
    account_id: str = ACCOUNT_ID,
    business_id: str = BUSINESS_ID,
    csv_prefix: str = DEFAULT_CSV_PREFIX,
    column_preset: str = DEFAULT_COLUMN_PRESET,
) -> bool:
    target = out_dir / f"{csv_prefix}_{label}_{day.isoformat()}.csv"
    if target.exists() and target.stat().st_size > 100:
        try:
            report = validate_export_csv_or_raise(
                target,
                account_id=account_id,
                level=label,
                day=day,
                column_preset=column_preset,
            )
        except ExportColumnValidationError as exc:
            invalid = _quarantine_invalid_export(target)
            print(
                "INVALID_EXISTING_COLUMNS",
                target.name,
                "moved_to",
                invalid.name if invalid else "",
                str(exc)[:300],
                flush=True,
            )
        else:
            print(
                "SKIP existing",
                target.name,
                target.stat().st_size,
                "columns",
                report["column_count"],
                flush=True,
            )
            return True

    for attempt in range(1, 4):
        try:
            print("OPEN", label, day.isoformat(), "attempt", attempt, flush=True)
            page.goto(
                build_url(
                    level,
                    day,
                    account_id=account_id,
                    business_id=business_id,
                    column_preset=column_preset,
                ),
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(9000)
            if _is_login_page(page):
                print("FAILED_AUTH", label, day.isoformat(), page.url[:300], flush=True)
                return AUTH_FAILED
            button = _export_button(page)
            _click_export_and_save(page, button, target)
            try:
                report = validate_export_csv_or_raise(
                    target,
                    account_id=account_id,
                    level=label,
                    day=day,
                    column_preset=column_preset,
                )
            except ExportColumnValidationError:
                _quarantine_invalid_export(target)
                raise
            print("SAVED", target.name, target.stat().st_size, "columns", report["column_count"], flush=True)
            return True
        except Exception as exc:  # noqa: BLE001 - backfill should keep moving after transient UI errors.
            print(
                "RETRYABLE_FAIL",
                label,
                day.isoformat(),
                "attempt",
                attempt,
                type(exc).__name__,
                str(exc)[:200],
                flush=True,
            )
            page.wait_for_timeout(15000)

    print("FAILED_FINAL", label, day.isoformat(), flush=True)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--account-id", default=ACCOUNT_ID)
    parser.add_argument("--business-id", default=BUSINESS_ID)
    parser.add_argument("--csv-prefix", default=DEFAULT_CSV_PREFIX)
    parser.add_argument(
        "--column-preset",
        default=DEFAULT_COLUMN_PRESET,
        help=(
            "Meta Ads Manager 列模板 ID（账户内 UI 手工创建后复制 column_preset URL 参数）。"
            "缺省走旧户预设；新增账户时必须为该账户独立配置以保证 CSV 含购买列。"
        ),
    )
    parser.add_argument("--cdp-url", default=CDP_URL)
    parser.add_argument(
        "--levels",
        default="campaigns,ads",
        help="Comma-separated Ads Manager levels to export: campaigns,adsets,ads.",
    )
    parser.add_argument("--long-rest-every-days", type=int, default=7)
    parser.add_argument(
        "--min-day-seconds",
        type=float,
        default=0,
        help="Minimum elapsed seconds for each day window, including both campaign and ad exports.",
    )
    args = parser.parse_args(argv)

    start = parse_date(args.start)
    end = parse_date(args.end)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    selected_levels = []
    for raw in str(args.levels or "").split(","):
        key = raw.strip()
        if not key:
            continue
        if key not in LEVELS:
            raise ValueError(f"unsupported level: {key}")
        if key not in selected_levels:
            selected_levels.append(key)
    if not selected_levels:
        raise ValueError("at least one level is required")

    failures: list[tuple[str, str]] = []
    attempted = 0

    try:
        with meta_ads_cdp_lock(
            task_code="meta_ads_manager_export",
            command=f"{args.csv_prefix} {start.isoformat()}..{end.isoformat()}",
        ):
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(args.cdp_url)
                context = browser.contexts[0]
                page = context.new_page()
                day = start
                days_done = 0
                while day <= end:
                    day_started_at = time.monotonic()
                    for level_key in selected_levels:
                        level, label = LEVELS[level_key]
                        result = export_one(
                            page,
                            out_dir,
                            level,
                            label,
                            day,
                            account_id=args.account_id,
                            business_id=args.business_id,
                            csv_prefix=args.csv_prefix,
                            column_preset=args.column_preset,
                        )
                        if result == AUTH_FAILED:
                            failures.append((day.isoformat(), f"{label}:auth"))
                            print("DONE attempted", attempted + 1, "failures", failures, flush=True)
                            return 2
                        if not result:
                            failures.append((day.isoformat(), label))
                        attempted += 1
                        sleep_s = uniform(10, 18)
                        print("SLEEP", round(sleep_s, 1), flush=True)
                        time.sleep(sleep_s)

                    days_done += 1
                    elapsed = time.monotonic() - day_started_at
                    if args.min_day_seconds > 0 and elapsed < args.min_day_seconds and day < end:
                        pacing_s = args.min_day_seconds - elapsed
                        print("DAY_PACING_AFTER", day.isoformat(), round(pacing_s, 1), flush=True)
                        time.sleep(pacing_s)
                    if day < end and days_done % args.long_rest_every_days == 0:
                        rest_s = uniform(180, 260)
                        print("LONG_REST_AFTER", day.isoformat(), round(rest_s, 1), flush=True)
                        time.sleep(rest_s)
                    day += timedelta(days=1)
                page.close()
                # Connected over CDP to the shared server browser; process exit closes the
                # websocket without shutting down the long-lived browser service.
    except BrowserAutomationLockTimeout as exc:
        print("LOCK_TIMEOUT", str(exc), flush=True)
        return 75

    print("DONE attempted", attempted, "failures", failures, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
