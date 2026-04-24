from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
CHROME_USER_DATA_DIR = Path(r"C:\chrome-shopifyid-diaoxiaomi")
ONLINE_URL = "https://www.dianxiaomi.com/web/shopifyProduct/online"
API_URL = "https://www.dianxiaomi.com/api/shopifyProduct/pageList.json"
OUTPUT_DIR = REPO_ROOT / "output" / "shopifyid_dianxiaomi_sync"
CHROME_EXECUTABLES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)
SSH_HOST = "172.30.254.14"
SSH_USER = "root"
SSH_KEY_PATH = Path(r"C:\Users\admin\.ssh\CC.pem")
REMOTE_MEDIA_TABLE = "media_products"
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
REMOTE_ENVS = {
    "prod": {
        "db_name": "auto_video",
        "label": "正式库",
    },
    "test": {
        "db_name": "auto_video_test",
        "label": "测试库",
    },
}

DEFAULT_PAYLOAD = {
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


def build_payload(page_no: int) -> dict[str, object]:
    payload = dict(DEFAULT_PAYLOAD)
    payload["pageNo"] = page_no
    return payload


def extract_page_summary(payload: dict[str, Any]) -> dict[str, int]:
    page = ((payload.get("data") or {}).get("page") or {})
    return {
        "total_size": int(page.get("totalSize") or 0),
        "total_page": int(page.get("totalPage") or 0),
        "page_size": int(page.get("pageSize") or 0),
        "page_no": int(page.get("pageNo") or 0),
    }


def extract_products(payload: dict[str, Any]) -> list[dict[str, str]]:
    page = ((payload.get("data") or {}).get("page") or {})
    items = page.get("list") or []
    rows: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        handle = str(item.get("handle") or "").strip()
        shopifyid = str(item.get("shopifyProductId") or "").strip()
        if not handle or not shopifyid:
            continue
        rows.append({
            "handle": handle,
            "shopifyid": shopifyid,
            "title": str(item.get("title") or "").strip(),
            "shop_id": str(item.get("shopId") or "").strip(),
        })
    return rows


def ensure_dianxiaomi_success(payload: dict[str, Any]) -> None:
    try:
        code = int(payload.get("code"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"店小秘接口返回异常：缺少可识别的 code 字段，payload={payload!r}") from exc
    if code != 0:
        raise RuntimeError(f"店小秘接口返回异常：code={payload.get('code')} msg={payload.get('msg')}")


def build_remote_handle_map(rows: list[dict[str, str]]) -> tuple[dict[str, str], list[dict[str, object]]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        handle = str(row.get("handle") or "").strip()
        shopifyid = str(row.get("shopifyid") or "").strip()
        if not handle or not shopifyid:
            continue
        grouped[handle].add(shopifyid)

    remote_map: dict[str, str] = {}
    conflicts: list[dict[str, object]] = []
    for handle, values in sorted(grouped.items()):
        if len(values) == 1:
            remote_map[handle] = next(iter(values))
            continue
        conflicts.append({
            "handle": handle,
            "shopifyids": sorted(values),
            "status": "remote_conflict",
        })
    return remote_map, conflicts


def build_remote_column_exists_sql(db_name: str) -> str:
    return (
        "SELECT COUNT(*) "
        "FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA='{db_name}' "
        f"AND TABLE_NAME='{REMOTE_MEDIA_TABLE}' "
        "AND COLUMN_NAME='shopifyid';\n"
    )


def build_remote_add_column_sql() -> str:
    return (
        f"ALTER TABLE {REMOTE_MEDIA_TABLE} "
        "ADD COLUMN shopifyid VARCHAR(32) NULL AFTER product_code;\n"
    )


def build_remote_select_products_sql() -> str:
    return (
        f"SELECT id, product_code, IFNULL(shopifyid, '') AS shopifyid "
        f"FROM {REMOTE_MEDIA_TABLE} "
        "WHERE deleted_at IS NULL "
        "ORDER BY id ASC;\n"
    )


def parse_remote_products_tsv(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        if len(parts) < 3:
            raise ValueError(f"远端 products TSV 行格式不正确：{raw_line!r}")
        product_id_text, product_code, shopifyid = parts[:3]
        product_code = product_code.strip()
        if not product_code:
            continue
        rows.append({
            "id": int(product_id_text.strip()),
            "product_code": product_code,
            "shopifyid": shopifyid.strip() or None,
        })
    return rows


def build_remote_batch_update_sql(updates: list[dict[str, object]]) -> str:
    if not updates:
        return ""

    lines = ["START TRANSACTION;"]
    for item in updates:
        product_id = int(item["id"])
        shopifyid = str(item["shopifyid"]).strip()
        if not shopifyid.isdigit():
            raise ValueError("shopifyid 必须是纯数字字符串")
        lines.append(
            f"UPDATE {REMOTE_MEDIA_TABLE} "
            f"SET shopifyid='{shopifyid}' "
            f"WHERE id={product_id} "
            "AND deleted_at IS NULL "
            "AND (shopifyid IS NULL OR shopifyid='');"
        )
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def plan_backfill_updates(remote_map: dict[str, str], local_products: list[dict[str, Any]]) -> dict[str, list[dict[str, object]]]:
    updates: list[dict[str, object]] = []
    unchanged: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []
    unmatched_local: list[dict[str, object]] = []

    local_codes: set[str] = set()
    for product in local_products:
        code = str(product.get("product_code") or "").strip()
        if not code:
            continue
        local_codes.add(code)
        remote_shopifyid = remote_map.get(code)
        if remote_shopifyid is None:
            unmatched_local.append({
                "id": product.get("id"),
                "product_code": code,
                "status": "unmatched_local",
            })
            continue

        existing = product.get("shopifyid")
        existing_text = None if existing in (None, "") else str(existing).strip()
        if not existing_text:
            updates.append({
                "id": product.get("id"),
                "product_code": code,
                "shopifyid": remote_shopifyid,
            })
            continue
        if existing_text == remote_shopifyid:
            unchanged.append({
                "id": product.get("id"),
                "product_code": code,
                "shopifyid": remote_shopifyid,
                "status": "unchanged",
            })
            continue
        conflicts.append({
            "id": product.get("id"),
            "product_code": code,
            "existing_shopifyid": existing_text,
            "incoming_shopifyid": remote_shopifyid,
            "status": "conflict",
        })

    unmatched_remote = [
        {"product_code": code, "shopifyid": shopifyid, "status": "unmatched_remote"}
        for code, shopifyid in sorted(remote_map.items())
        if code not in local_codes
    ]

    return {
        "updates": updates,
        "unchanged": unchanged,
        "conflicts": conflicts,
        "unmatched_local": unmatched_local,
        "unmatched_remote": unmatched_remote,
    }


def fetch_all_remote_products(fetch_page: Callable[[int], dict[str, Any]]) -> tuple[dict[str, int], list[dict[str, str]]]:
    first_payload = fetch_page(1)
    summary = extract_page_summary(first_payload)
    rows = extract_products(first_payload)
    total_page = summary["total_page"]
    for page_no in range(2, total_page + 1):
        rows.extend(extract_products(fetch_page(page_no)))
    return summary, rows


def write_report(output_dir: Path, report: dict[str, Any], now_text: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"shopifyid-dianxiaomi-sync-{now_text}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_sync(
    *,
    fetch_page: Callable[[int], dict[str, Any]],
    local_products: list[dict[str, Any]],
    apply_updates: Callable[[list[dict[str, object]]], None],
    output_dir: Path,
    now_text: str | None = None,
) -> dict[str, Any]:
    page_summary, remote_rows = fetch_all_remote_products(fetch_page)
    remote_map, remote_conflicts = build_remote_handle_map(remote_rows)
    plan = plan_backfill_updates(remote_map, local_products)

    if plan["updates"]:
        apply_updates(plan["updates"])

    matched_count = len(plan["updates"]) + len(plan["unchanged"]) + len(plan["conflicts"])
    report = {
        "page_summary": page_summary,
        "summary": {
            "total_size": page_summary["total_size"],
            "total_page": page_summary["total_page"],
            "fetched": len(remote_rows),
            "matched": matched_count,
            "updated": len(plan["updates"]),
            "unchanged": len(plan["unchanged"]),
            "conflict": len(plan["conflicts"]),
            "unmatched_local": len(plan["unmatched_local"]),
            "unmatched_remote": len(plan["unmatched_remote"]),
            "remote_conflict": len(remote_conflicts),
        },
        "updates": plan["updates"],
        "unchanged": plan["unchanged"],
        "conflicts": plan["conflicts"],
        "unmatched_local": plan["unmatched_local"],
        "unmatched_remote": plan["unmatched_remote"],
        "remote_conflicts": remote_conflicts,
    }
    output_file = write_report(output_dir, report, now_text or _now_text())
    report["output_file"] = str(output_file)
    return report


def _find_chrome_executable() -> str | None:
    for path in CHROME_EXECUTABLES:
        if path.exists():
            return str(path)
    return None


def _find_profile_process_ids() -> list[int]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" "
        "| Where-Object { $_.CommandLine -like '*C:\\chrome-shopifyid-diaoxiaomi*' } "
        "| Select-Object -ExpandProperty ProcessId",
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        return []
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        text = line.strip()
        if text.isdigit():
            pids.append(int(text))
    return pids


def _stop_profile_chrome_processes() -> None:
    deadline = time.time() + 10
    while True:
        pids = _find_profile_process_ids()
        if not pids:
            return
        for pid in pids:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
        if time.time() >= deadline:
            raise RuntimeError("无法关闭专用 Chrome 进程，请手动关闭后重试。")
        time.sleep(0.5)


def _wait_for_cdp_ready() -> None:
    deadline = time.time() + 15
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(f"{CDP_URL}/json/version", timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, OSError, TimeoutError) as exc:
            last_error = exc
        time.sleep(0.3)
    raise RuntimeError(f"Chrome 调试端口未就绪：{last_error}")


def _launch_browser_context(playwright):
    CHROME_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    chrome_path = _find_chrome_executable()
    if not chrome_path:
        raise RuntimeError("未找到本机 Chrome，可执行文件不存在。")
    _stop_profile_chrome_processes()
    browser_process = subprocess.Popen(
        [
            chrome_path,
            f"--user-data-dir={CHROME_USER_DATA_DIR}",
            "--no-first-run",
            f"--remote-debugging-port={CDP_PORT}",
            "--remote-allow-origins=*",
            "--disable-gpu",
            ONLINE_URL,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_cdp_ready()
        browser = playwright.chromium.connect_over_cdp(CDP_URL)
        deadline = time.time() + 5
        while time.time() < deadline:
            if browser.contexts:
                return browser_process, browser, browser.contexts[0]
            time.sleep(0.2)
        raise RuntimeError("已连接到 Chrome，但没有拿到浏览器上下文。")
    except Exception as exc:  # pragma: no cover - exercised manually
        if browser_process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(browser_process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
        raise RuntimeError(
            "无法启动专用 Chrome 会话，请确认 Chrome 可正常打开后重试。"
        ) from exc


def _fetch_page_via_browser(page, page_no: int) -> dict[str, Any]:
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
        {"apiUrl": API_URL, "payload": build_payload(page_no)},
    )
    if not result.get("ok"):
        raise RuntimeError(f"店小秘接口请求失败：HTTP {result.get('status')}")
    text = str(result.get("text") or "")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"店小秘接口返回了非 JSON 内容：{text[:200]}") from exc
    ensure_dianxiaomi_success(payload)
    return payload


def _run_remote_mysql(sql: str, db_name: str) -> str:
    if not SSH_KEY_PATH.exists():
        raise RuntimeError(f"SSH key 不存在：{SSH_KEY_PATH}")

    command = [
        "ssh",
        "-i",
        str(SSH_KEY_PATH),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"{SSH_USER}@{SSH_HOST}",
        f"mysql -N -B {db_name}",
    ]
    completed = subprocess.run(
        command,
        input=sql,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"远端 MySQL 执行失败：{stderr}")
    return completed.stdout


def _ensure_remote_shopifyid_column(db_name: str) -> None:
    count_text = _run_remote_mysql(build_remote_column_exists_sql(db_name), db_name).strip()
    exists = int(count_text or "0") > 0
    if exists:
        return
    _run_remote_mysql(build_remote_add_column_sql(), db_name)


def _load_remote_local_products(db_name: str) -> list[dict[str, Any]]:
    output = _run_remote_mysql(build_remote_select_products_sql(), db_name)
    return parse_remote_products_tsv(output)


def _apply_remote_updates(db_name: str, updates: list[dict[str, object]]) -> None:
    sql = build_remote_batch_update_sql(updates)
    if not sql:
        return
    _run_remote_mysql(sql, db_name)


def _now_text() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _print_report(report: dict[str, Any], *, remote_label: str, db_name: str) -> None:
    summary = report["summary"]
    print("同步完成：")
    print(f"  {remote_label}: {SSH_USER}@{SSH_HOST}:{db_name}")
    print(f"  店小秘在线商品总数: {summary['total_size']}")
    print(f"  抓取页数: {summary['total_page']}")
    print(f"  抓取商品数: {summary['fetched']}")
    print(f"  命中 product_code: {summary['matched']}")
    print(f"  新回填: {summary['updated']}")
    print(f"  已一致: {summary['unchanged']}")
    print(f"  本地冲突: {summary['conflict']}")
    print(f"  本地未匹配: {summary['unmatched_local']}")
    print(f"  远端未匹配: {summary['unmatched_remote']}")
    print(f"  远端 handle 冲突: {summary['remote_conflict']}")
    print(f"  结果日志: {report['output_file']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从店小秘 Shopify 在线商品库回填正式/测试库 media_products.shopifyid")
    parser.add_argument(
        "--env",
        choices=sorted(REMOTE_ENVS.keys()),
        default="prod",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--skip-login-prompt",
        action="store_true",
        help="跳过“登录后回车继续”的提示，适合已经确认登录态有效时使用",
    )
    args = parser.parse_args(argv)
    remote_config = REMOTE_ENVS[args.env]
    db_name = str(remote_config["db_name"])
    remote_label = str(remote_config["label"])

    _ensure_remote_shopifyid_column(db_name)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser_process, browser, context = _launch_browser_context(playwright)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(ONLINE_URL, wait_until="domcontentloaded")
            print(f"已打开店小秘页面：{ONLINE_URL}")
            if not args.skip_login_prompt:
                input("如果还没登录，请先登录店小秘；登录完成后按回车继续...")
                page.goto(ONLINE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            report = run_sync(
                fetch_page=lambda page_no: _fetch_page_via_browser(page, page_no),
                local_products=_load_remote_local_products(db_name),
                apply_updates=lambda updates: _apply_remote_updates(db_name, updates),
                output_dir=OUTPUT_DIR,
            )
        finally:
            browser.close()
            if browser_process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(browser_process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                )

    _print_report(report, remote_label=remote_label, db_name=db_name)
    return 0


if __name__ == "__main__":  # pragma: no cover - manual execution entrypoint
    raise SystemExit(main())
