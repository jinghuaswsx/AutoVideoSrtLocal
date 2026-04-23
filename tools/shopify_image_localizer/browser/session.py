from __future__ import annotations

import re
from pathlib import Path


STORE_SLUG = "0ixug9-pv"
LOGIN_URL_HINTS = ("login", "signin", "sign-in", "account", "identity")
LOGIN_TEXT_HINTS = (
    "log in",
    "sign in",
    "continue with email",
    "enter your email",
    "shopify",
)


def build_ez_url(shopify_product_id: str) -> str:
    return (
        f"https://admin.shopify.com/store/{STORE_SLUG}/apps/"
        f"ez-product-image-translate/product/{shopify_product_id}"
    )


def build_translate_url(shopify_product_id: str, shop_locale: str) -> str:
    return (
        f"https://admin.shopify.com/store/{STORE_SLUG}/apps/translate-and-adapt/localize/product"
        f"?highlight=handle&id={shopify_product_id}&shopLocale={shop_locale}"
    )


def launch_persistent_context(playwright, user_data_dir: str):
    launch_kwargs = {
        "user_data_dir": user_data_dir,
        "headless": False,
        "no_viewport": True,
        "args": ["--start-maximized"],
    }
    try:
        return playwright.chromium.launch_persistent_context(
            channel="msedge",
            **launch_kwargs,
        )
    except Exception:
        return playwright.chromium.launch_persistent_context(**launch_kwargs)


def page_requires_login(page) -> bool:
    current_url = (page.url or "").strip().lower()
    if any(token in current_url for token in LOGIN_URL_HINTS):
        return True

    try:
        body_text = (page.locator("body").inner_text(timeout=1500) or "").strip().lower()
    except Exception:
        body_text = ""

    if body_text:
        condensed = re.sub(r"\s+", " ", body_text)
        if any(token in condensed for token in LOGIN_TEXT_HINTS):
            if "admin.shopify.com/store/" not in current_url:
                return True
    return False


def ensure_target_page(page, target_url: str, *, status_cb=None, label: str = "Shopify") -> None:
    if status_cb is not None:
        status_cb(f"正在打开 {label} 页面")
    page.goto(target_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    warned = False
    while page_requires_login(page):
        if status_cb is not None and not warned:
            status_cb("检测到 Shopify 未登录，请在浏览器中手动登录，程序会自动继续")
        warned = True
        page.wait_for_timeout(2000)

    if warned:
        if status_cb is not None:
            status_cb(f"检测到 Shopify 登录恢复，正在回到 {label} 页面")
        page.goto(target_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)


def save_page_snapshot(page, output_dir: Path, filename: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    page.screenshot(path=str(path), full_page=True)
    return str(path)


def capture_visible_images(
    page,
    output_dir: Path,
    *,
    prefix: str,
    selectors: list[str],
    min_width: float = 80.0,
    min_height: float = 80.0,
    max_count: int = 40,
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    captured: list[dict] = []
    seen_keys: set[tuple[str, int, int]] = set()

    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = min(locator.count(), max_count)
        except Exception:
            continue

        for index in range(count):
            if len(captured) >= max_count:
                return captured

            candidate = locator.nth(index)
            try:
                box = candidate.bounding_box()
                if not box:
                    continue
                if float(box.get("width") or 0.0) < min_width or float(box.get("height") or 0.0) < min_height:
                    continue
                src = (
                    candidate.evaluate("(node) => node.currentSrc || node.src || ''")
                    if hasattr(candidate, "evaluate")
                    else ""
                )
                dedupe_key = (
                    str(src or selector),
                    int(box.get("width") or 0),
                    int(box.get("height") or 0),
                )
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)

                slot_id = f"{prefix}-{len(captured) + 1:03d}"
                local_path = output_dir / f"{slot_id}.png"
                candidate.screenshot(path=str(local_path))
                captured.append({
                    "slot_id": slot_id,
                    "local_path": str(local_path),
                    "selector": selector,
                    "index": index,
                    "src": str(src or ""),
                    "width": float(box.get("width") or 0.0),
                    "height": float(box.get("height") or 0.0),
                })
            except Exception:
                continue

    return captured


def click_slot(page, slot: dict) -> None:
    selector = str(slot.get("selector") or "")
    index = int(slot.get("index") or 0)
    if not selector:
        return
    locator = page.locator(selector)
    if locator.count() <= index:
        return
    locator.nth(index).click(timeout=3000)
    page.wait_for_timeout(500)


def upload_file_to_page(page, local_path: str) -> bool:
    file_inputs = page.locator("input[type='file']")
    try:
        count = file_inputs.count()
    except Exception:
        count = 0
    for index in range(count):
        try:
            file_inputs.nth(index).set_input_files(local_path)
            page.wait_for_timeout(1200)
            return True
        except Exception:
            continue

    button_patterns = [
        re.compile(r"add", re.I),
        re.compile(r"upload", re.I),
        re.compile(r"replace", re.I),
        re.compile(r"image", re.I),
        re.compile(r"photo", re.I),
        re.compile(r"media", re.I),
    ]
    for pattern in button_patterns:
        try:
            button = page.get_by_role("button", name=pattern).first
            with page.expect_file_chooser(timeout=2000) as chooser:
                button.click()
            chooser.value.set_files(local_path)
            page.wait_for_timeout(1200)
            return True
        except Exception:
            continue
    return False
