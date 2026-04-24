from __future__ import annotations

"""
交互式 DOM probe：弄清楚 EZ 和 TAA 真实的图片翻译 UX。

EZ：
  - iframe 里只有 9 张预览图，没 button/input
  - 假设必须先点图才展开上传面板 → 点第一张图 → 等 3s → 再抓 DOM
TAA：
  - iframe 里有 ~13 个 ResourceItem__Button
  - 这些 button 通常对应产品字段（title / description / images / seo / options / ...）
  - 列出每个 button 外层 li 的文本内容，定位 "images" 条目 → 点击 → 等 3s → 抓 DOM

Usage:
    python -m tools.shopify_image_localizer.diag.interactive_probe \
        --product-id 8552296546477 --lang de
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.diag import dom_probe


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe(v: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", v)[:80] or "probe"


def _find_plugin_frame(page, host_hint: str):
    """返回 page.frames 中 url 含 host_hint 的 frame；否则返回 None。"""
    for fr in page.frames:
        if host_hint in (fr.url or ""):
            return fr
    return None


def _dump_all(page, out_dir: Path, label: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(out_dir / f"{label}.png"), full_page=True)
    except Exception:
        pass
    for idx, fr in enumerate(page.frames):
        url = fr.url or "about_blank"
        tag = _safe(f"{label}_frame_{idx:02d}_{url[:60]}")
        try:
            (out_dir / f"{tag}.html").write_text(fr.content(), encoding="utf-8")
        except Exception as exc:
            (out_dir / f"{tag}.html").write_text(f"<!-- {exc} -->", encoding="utf-8")
        try:
            body = fr.locator("body").inner_text(timeout=2000) or ""
        except Exception:
            body = ""
        (out_dir / f"{tag}.txt").write_text(body, encoding="utf-8")


def _probe_upload_signals(frame) -> dict:
    """统计上传相关控件的命中。"""
    queries = [
        "input[type='file']",
        "[role='button'][aria-label*='Upload' i]",
        "[role='button'][aria-label*='Replace' i]",
        "[role='button'][aria-label*='Add' i]",
        "[role='button'][aria-label*='Image' i]",
        "button:has-text('Upload')",
        "button:has-text('Replace')",
        "button:has-text('Add')",
        "button:has-text('Add image')",
        "button:has-text('Save')",
        "button:has-text('Publish')",
        "button:has-text('Update')",
        "[data-testid*='upload']",
        "[data-testid*='replace']",
        "[data-testid*='add']",
        "[data-testid*='save']",
    ]
    out: list[dict] = []
    for q in queries:
        try:
            cnt = frame.locator(q).count()
        except Exception:
            cnt = 0
        if cnt > 0:
            out.append({"q": q, "count": cnt})
    return {"frame_url": frame.url, "hits": out}


def probe_ez(page, shopify_product_id: str, out_dir: Path) -> dict:
    url = session.build_ez_url(shopify_product_id)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)

    plugin = _find_plugin_frame(page, "translate.freshify.click")
    if plugin is None:
        _dump_all(page, out_dir, "ez_pre_click_no_plugin")
        return {"status": "no_plugin_iframe"}

    _dump_all(page, out_dir, "ez_pre_click")
    plugin_before = _probe_upload_signals(plugin)

    # 点第一张产品图
    clicked = False
    first_img = plugin.locator("img.actual-image").first
    try:
        if first_img.count() > 0:
            first_img.click(timeout=5000)
            page.wait_for_timeout(3500)
            clicked = True
    except Exception as exc:
        clicked_err = str(exc)[:200]
        _dump_all(page, out_dir, "ez_click_failed")
        return {"status": "click_failed", "error": clicked_err}

    # refresh plugin frame ref (URL 可能已变)
    plugin = _find_plugin_frame(page, "translate.freshify.click") or plugin
    _dump_all(page, out_dir, "ez_post_click")

    # 点击后也扫 page 全部 frame，看是否冒出新的 iframe（比如 upload dialog）
    frame_urls = [fr.url for fr in page.frames]
    plugin_after = _probe_upload_signals(plugin)

    return {
        "status": "ok",
        "clicked_first_image": clicked,
        "frame_urls": frame_urls,
        "plugin_before": plugin_before,
        "plugin_after": plugin_after,
    }


def probe_taa(page, shopify_product_id: str, shop_locale: str, out_dir: Path) -> dict:
    url = session.build_translate_url(shopify_product_id, shop_locale)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)

    plugin = _find_plugin_frame(page, "store-localization.shopifyapps.com")
    if plugin is None:
        _dump_all(page, out_dir, "taa_pre_click_no_plugin")
        return {"status": "no_plugin_iframe"}

    _dump_all(page, out_dir, "taa_pre_click")

    # 枚举所有 ResourceItem：它们通常是 <li> with a Polaris button 内
    items_info: list[dict] = []
    items_locator = plugin.locator(".Polaris-ResourceItem, [class*='ResourceItem']")
    try:
        total = items_locator.count()
    except Exception:
        total = 0
    for i in range(min(total, 30)):
        try:
            node = items_locator.nth(i)
            text = (node.inner_text(timeout=2000) or "").strip()
            items_info.append({"index": i, "text": text[:200]})
        except Exception as exc:
            items_info.append({"index": i, "error": str(exc)[:200]})

    # 找包含 image/images 文本的项
    image_item_index = None
    for info in items_info:
        text_l = (info.get("text") or "").lower()
        if "image" in text_l or "media" in text_l or "photo" in text_l:
            image_item_index = info["index"]
            break

    clicked = False
    if image_item_index is not None:
        try:
            items_locator.nth(image_item_index).click(timeout=5000)
            page.wait_for_timeout(3500)
            clicked = True
        except Exception as exc:
            _dump_all(page, out_dir, "taa_click_failed")
            return {
                "status": "click_failed",
                "items": items_info,
                "image_item_index": image_item_index,
                "error": str(exc)[:200],
            }

    plugin = _find_plugin_frame(page, "store-localization.shopifyapps.com") or plugin
    _dump_all(page, out_dir, "taa_post_click")

    return {
        "status": "ok",
        "items": items_info,
        "image_item_index": image_item_index,
        "clicked_image_item": clicked,
        "frame_urls": [fr.url for fr in page.frames],
        "plugin_signals": _probe_upload_signals(plugin),
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
    base_out = Path(args.out).resolve() / f"interactive_{product_id}_{lang}_{_timestamp()}"
    base_out.mkdir(parents=True, exist_ok=True)

    session.ensure_chrome_running(args.user_data_dir, port=args.port)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.set_default_timeout(20000)

        page = context.new_page()
        try:
            print("[iprobe] 先打开店铺主面板等待登录")
            dom_probe._wait_for_logged_in(page, f"https://admin.shopify.com/store/{session.STORE_SLUG}")

            print("[iprobe] 抓 EZ 点击前/点击后")
            ez = probe_ez(page, product_id, base_out / "ez")

            print("[iprobe] 抓 TAA 列表 + 点击图片项后")
            taa = probe_taa(page, product_id, lang, base_out / "taa")

            summary = {"ez": ez, "taa": taa, "out_dir": str(base_out)}
            (base_out / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[iprobe] 完成：{base_out}")
        finally:
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
