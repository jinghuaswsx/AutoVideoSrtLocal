from __future__ import annotations

"""
Shopify Image Localizer DOM probe.

通过 CDP 连接到本机 7777 端口的 Chrome（没起就先用独立 profile 启动一个），
依次打开 EZ Product Image 和 Translate and Adapt 两个插件页面，落盘 HTML、
截图和候选选择器命中统计，便于离线分析 DOM 结构。

- 不启动 Playwright 自己的 chromium，不会出现"自动化控制"横幅
- 自己开新 tab（context.new_page），不改动用户已有的 tabs
- 跑完后断开 CDP 但不关闭 Chrome

Usage:
    python -m tools.shopify_image_localizer.diag.dom_probe \
        --product-id 8552296546477 \
        --lang de \
        --user-data-dir C:\\chrome-shopify-image \
        --out tmp_probe \
        --port 7777
"""

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from tools.shopify_image_localizer.browser import session


EZ_CANDIDATE_SELECTORS = [
    "main img",
    "main img[src]",
    "[role='main'] img",
    "[class*='Polaris-Card'] img",
    "[class*='MediaCard'] img",
    "[class*='Thumbnail'] img",
    "[class*='media'] img",
    "[class*='image-item'] img",
    "[data-testid*='image'] img",
    "button:has(img)",
    "li:has(img)",
    "figure img",
    "img[src^='https://cdn.shopify.com']",
    "img[src^='blob:']",
]

TAA_CANDIDATE_SELECTORS = [
    "main img",
    "main img[src]",
    "[role='main'] img",
    "[contenteditable='true'] img",
    "[class*='Editor'] img",
    "[class*='Translate'] img",
    "[data-testid*='translation'] img",
    "[data-testid*='image'] img",
    "img[src^='https://cdn.shopify.com']",
    "img[src^='https://shopify-assets']",
    "img[src^='data:']",
    "figure img",
    "section img",
]

UPLOAD_PROBE_SELECTORS = [
    "input[type='file']",
    "button:has-text('Upload')",
    "button:has-text('Add')",
    "button:has-text('Replace')",
    "button:has-text('Change')",
    "button:has-text('Add image')",
    "button:has-text('Upload image')",
    "[role='button']:has-text('Upload')",
    "[role='button']:has-text('Replace')",
    "[data-testid*='upload']",
    "[data-testid*='replace']",
    "[aria-label*='Upload']",
    "[aria-label*='Replace']",
    "[aria-label*='Change']",
]

LOGIN_URL_HINTS = (
    "accounts.shopify.com",
    "identity.shopify.com",
    "/login",
    "/signin",
    "/sign-in",
    "/auth",
)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:80] or "probe"


def _is_logged_in(page) -> bool:
    try:
        current_url = (page.url or "").strip().lower()
    except Exception:
        return False
    if not current_url:
        return False
    if any(token in current_url for token in LOGIN_URL_HINTS):
        return False
    if "admin.shopify.com/store/" not in current_url:
        return False
    return True


def _wait_for_logged_in(page, target_url: str, *, timeout_s: int = 900):
    """在当前 page 上反复检测登录态，登录完成后回到 target_url。"""
    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightError:
        pass
    page.wait_for_timeout(1500)

    if _is_logged_in(page):
        return page

    print("[probe] Shopify 未登录；请在打开的浏览器窗口中完成 Shopify 登录。")
    print("[probe] 登录后本脚本会自动继续，请不要关闭这个 tab。")

    deadline = time.time() + timeout_s
    announced = False
    while time.time() < deadline:
        if _is_logged_in(page):
            if not announced:
                print("[probe] 登录已恢复，准备继续。")
                announced = True
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
            except PlaywrightError as exc:
                print(f"[probe] 回到目标页时报错：{exc}，重试中")
                time.sleep(3)
                continue
            if _is_logged_in(page):
                return page
        time.sleep(3)

    raise RuntimeError("登录等待超时，请重试")


def _probe_selectors(page, selectors: list[str]) -> list[dict]:
    rows: list[dict] = []
    for selector in selectors:
        try:
            loc = page.locator(selector)
            count = loc.count()
        except Exception as exc:
            rows.append({"selector": selector, "count": 0, "error": str(exc)[:200]})
            continue

        samples: list[dict] = []
        shown = min(count, 6)
        for i in range(shown):
            try:
                node = loc.nth(i)
                box = None
                try:
                    box = node.bounding_box()
                except Exception:
                    box = None
                tag = node.evaluate("(el) => el.tagName")
                src = ""
                try:
                    src = node.evaluate(
                        "(el) => el.currentSrc || el.src || el.getAttribute('src') || ''"
                    )
                except Exception:
                    src = ""
                aria = ""
                try:
                    aria = node.evaluate("(el) => el.getAttribute('aria-label') || ''")
                except Exception:
                    aria = ""
                testid = ""
                try:
                    testid = node.evaluate("(el) => el.getAttribute('data-testid') || ''")
                except Exception:
                    testid = ""
                cls = ""
                try:
                    cls = node.evaluate("(el) => el.getAttribute('class') || ''")
                except Exception:
                    cls = ""
                samples.append({
                    "tag": tag,
                    "box": box,
                    "src": (src or "")[:200],
                    "aria_label": aria,
                    "data_testid": testid,
                    "class": (cls or "")[:200],
                })
            except Exception as exc:
                samples.append({"error": str(exc)[:200]})
        rows.append({
            "selector": selector,
            "count": count,
            "samples": samples,
        })
    return rows


def _dump_page(page, out_dir: Path, tag: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{tag}.html"
    shot_path = out_dir / f"{tag}.png"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        html_path.write_text(f"<!-- failed to dump: {exc} -->", encoding="utf-8")
    try:
        page.screenshot(path=str(shot_path), full_page=True)
    except Exception:
        pass
    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=2000) or ""
    except Exception:
        body_text = ""
    (out_dir / f"{tag}.txt").write_text(body_text, encoding="utf-8")
    return {
        "html_path": str(html_path),
        "screenshot_path": str(shot_path),
        "body_text_path": str(out_dir / f"{tag}.txt"),
        "url": getattr(page, "url", ""),
        "title": page.title() if page else "",
    }


def _probe_page(page, label: str, url: str, selectors: list[str], out_dir: Path) -> dict:
    print(f"[probe] 打开 {label}: {url}")
    page = _wait_for_logged_in(page, url)

    for wait_s in (2, 2, 3, 3):
        page.wait_for_timeout(int(wait_s * 1000))
        try:
            if page.locator("main").count() > 0 or page.locator("#app").count() > 0:
                break
        except Exception:
            pass

    dump = _dump_page(page, out_dir, tag=_safe_name(label))

    print(f"[probe] 正在扫描 {label} 选择器命中数")
    slot_rows = _probe_selectors(page, selectors)
    upload_rows = _probe_selectors(page, UPLOAD_PROBE_SELECTORS)

    return {
        "label": label,
        "dump": dump,
        "slot_selectors": slot_rows,
        "upload_selectors": upload_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-id", default="8552296546477")
    parser.add_argument("--lang", default="de")
    parser.add_argument("--user-data-dir", default=r"C:\chrome-shopify-image")
    parser.add_argument("--out", default="tmp_probe")
    parser.add_argument("--port", type=int, default=session.DEFAULT_CDP_PORT)
    args = parser.parse_args()

    product_id = str(args.product_id).strip()
    lang = str(args.lang).strip().lower()
    base_out = Path(args.out).resolve() / f"{product_id}_{lang}_{_timestamp()}"
    base_out.mkdir(parents=True, exist_ok=True)

    admin_home = f"https://admin.shopify.com/store/{session.STORE_SLUG}"
    ez_url = session.build_ez_url(product_id)
    translate_url = session.build_translate_url(product_id, lang)

    started_chrome = session.ensure_chrome_running(args.user_data_dir, port=args.port)
    print(f"[probe] Chrome 已就绪（本次{'刚启动' if started_chrome else '复用已有'}）")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.set_default_timeout(20000)

        page = context.new_page()

        try:
            print("[probe] 先打开店铺主面板检测登录态")
            _wait_for_logged_in(page, admin_home)

            ez_report = _probe_page(page, "ez", ez_url, EZ_CANDIDATE_SELECTORS, base_out / "ez")
            translate_report = _probe_page(
                page, "translate", translate_url, TAA_CANDIDATE_SELECTORS, base_out / "translate"
            )

            summary = {
                "product_id": product_id,
                "lang": lang,
                "ez": ez_report,
                "translate": translate_report,
                "out_dir": str(base_out),
            }
            (base_out / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[probe] 完成，结果已落盘：{base_out}")
        finally:
            # 自己开的 tab 跑完关掉；不关 Chrome、不关用户的 tabs
            try:
                page.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
