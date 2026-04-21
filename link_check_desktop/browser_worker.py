from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from link_check_desktop.html_extract import extract_images_from_html
from link_check_desktop.storage import executable_root


_SUPPORTED_RASTER_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".avif",
}
_NOT_FOUND_TOKENS = (
    "404",
    "not found",
    "nicht gefunden",
    "no encontrado",
    "non trouve",
    "non trouvé",
)


def _report(status_cb, message: str) -> None:
    if status_cb is not None:
        status_cb(message)


def _is_locked(html_lang: str, target_language: str) -> bool:
    return (html_lang or "").strip().lower().startswith((target_language or "").strip().lower())


def _looks_not_found(page_title: str) -> bool:
    lowered = (page_title or "").strip().lower()
    return any(token in lowered for token in _NOT_FOUND_TOKENS)


def _sanitize_extension(url: str, content_type: str | None) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
    return guessed or ".jpg"


def _same_image_target(requested_url: str, resolved_url: str) -> bool:
    requested = urlparse(requested_url)
    resolved = urlparse(resolved_url)
    return (
        requested.netloc.lower() == resolved.netloc.lower()
        and requested.path == resolved.path
    )


def _supported_raster_asset(url: str, content_type: str | None) -> bool:
    normalized_type = ((content_type or "").split(";")[0] or "").strip().lower()
    extension = Path(urlparse(url).path).suffix.lower()

    if normalized_type == "image/svg+xml" or extension == ".svg":
        return False
    if normalized_type and not normalized_type.startswith("image/"):
        return False
    if extension and extension not in _SUPPORTED_RASTER_EXTENSIONS:
        return False
    return True


def _build_session(context, user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    for cookie in context.cookies():
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path") or "/",
        )
    return session


def _download_image(session: requests.Session, url: str, output_path: Path) -> dict:
    response = session.get(url, timeout=30, allow_redirects=True)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type") or ""
    if not _supported_raster_asset(response.url, content_type):
        raise ValueError(f"unsupported image content type: {content_type or response.url}")
    output_path.write_bytes(response.content)
    return {
        "requested_url": url,
        "resolved_url": response.url,
        "redirected": response.url != url,
        "preserved_asset": _same_image_target(url, response.url),
        "content_type": content_type,
    }


def _response_status(response) -> int | None:
    return getattr(response, "status", None) if response is not None else None


def _version_key(path: Path) -> tuple[int, str]:
    match = re.search(r"(\d+)$", path.name)
    return (int(match.group(1)) if match else -1, path.name)


def _find_bundled_chromium_executable() -> Path | None:
    browsers_root = executable_root() / "ms-playwright"
    if not browsers_root.is_dir():
        return None

    candidates: list[Path] = []
    for browser_dir in sorted(browsers_root.glob("chromium-*"), key=_version_key, reverse=True):
        for relative in ("chrome-win64/chrome.exe", "chrome-win/chrome.exe"):
            candidate = browser_dir / relative
            if candidate.is_file():
                candidates.append(candidate)
    return candidates[0] if candidates else None


def _launch_visible_browser(playwright):
    bundled_executable = _find_bundled_chromium_executable()
    if bundled_executable is not None:
        try:
            return playwright.chromium.launch(
                executable_path=str(bundled_executable),
                headless=False,
            )
        except Exception:
            pass

    try:
        return playwright.chromium.launch(channel="msedge", headless=False)
    except Exception as exc:
        raise RuntimeError(
            "未找到可用浏览器运行时。绿色包请确认 exe 同目录下带有 ms-playwright，"
            "或在目标 Windows 机器安装 Microsoft Edge 后再运行。"
        ) from exc


def _assert_valid_page(response, page) -> tuple[int | None, str]:
    status_code = _response_status(response)
    title = page.title()
    if status_code is not None and status_code >= 400:
        raise RuntimeError(f"target page returned HTTP {status_code}")
    if _looks_not_found(title):
        raise RuntimeError(f"target page looks like not found: {title}")
    return status_code, title


def _disable_browser_cache(context, page) -> None:
    try:
        cdp_session = context.new_cdp_session(page)
    except Exception:
        return

    try:
        cdp_session.send("Network.enable")
        cdp_session.send("Network.setCacheDisabled", {"cacheDisabled": True})
    except Exception:
        return


def _hard_refresh_page(page) -> None:
    try:
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.keyboard.press("Control+F5")
    except Exception:
        reload_fn = getattr(page, "reload", None)
        if reload_fn is None:
            raise
        reload_fn(wait_until="domcontentloaded")

    wait_for_load_state = getattr(page, "wait_for_load_state", None)
    if callable(wait_for_load_state):
        try:
            wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
    page.wait_for_timeout(1500)


def _refresh_page_assets(page, context, status_cb=None, *, refresh_count: int = 2) -> None:
    _disable_browser_cache(context, page)
    for index in range(refresh_count):
        _report(status_cb, f"页面已锁定，正在强制刷新 {index + 1}/{refresh_count}")
        _hard_refresh_page(page)


def capture_page(*, target_url: str, target_language: str, workspace, status_cb=None) -> dict:
    from playwright.sync_api import sync_playwright

    _report(status_cb, "正在打开可视浏览器")
    with sync_playwright() as playwright:
        browser = _launch_visible_browser(playwright)
        context = browser.new_context(locale=target_language)
        page = context.new_page()
        page.set_default_timeout(30000)

        _report(status_cb, "第一次打开目标页")
        first_response = page.goto(target_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        first_final_url = page.url
        first_html_lang = page.eval_on_selector("html", "el => el.lang || ''")
        first_status, first_page_title = _assert_valid_page(first_response, page)
        redirected = first_final_url != target_url

        final_response = first_response
        if redirected or not _is_locked(first_html_lang, target_language):
            _report(status_cb, "检测到重定向或语种未锁定，第二次打开原始目标页")
            final_response = page.goto(target_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

        final_status, page_title = _assert_valid_page(final_response, page)
        final_url = page.url
        html_lang = page.eval_on_selector("html", "el => el.lang || ''")
        locked = _is_locked(html_lang, target_language)

        if locked:
            _refresh_page_assets(page, context, status_cb)
            _assert_valid_page(None, page)
            final_url = page.url
            html_lang = page.eval_on_selector("html", "el => el.lang || ''")
            page_title = page.title()

        html = page.content()
        (workspace.root / "page.html").write_text(html, encoding="utf-8")

        image_entries = extract_images_from_html(html, base_url=final_url)
        image_urls = [item["source_url"] for item in image_entries]
        downloaded_images: list[dict] = []
        skipped_images: list[dict] = []

        if locked:
            _report(status_cb, "正在提取页面图片")
            user_agent = page.evaluate("() => navigator.userAgent")
            session = _build_session(context, user_agent)
            for index, item in enumerate(image_entries, start=1):
                image_url = item["source_url"]
                extension = _sanitize_extension(image_url, None)
                local_path = workspace.site_dir / f"site-{index:03d}{extension}"
                try:
                    evidence = _download_image(session, image_url, local_path)
                except ValueError as exc:
                    skipped_images.append({
                        "source_url": image_url,
                        "kind": item.get("kind") or "page_image",
                        "reason": str(exc),
                    })
                    continue
                downloaded_images.append({
                    "id": f"site-{index:03d}",
                    "kind": item.get("kind") or "page_image",
                    "source_url": image_url,
                    "local_path": str(local_path),
                    "download_evidence": evidence,
                })

        browser.close()

    return {
        "requested_url": target_url,
        "first_status": first_status,
        "first_final_url": first_final_url,
        "first_page_title": first_page_title,
        "final_status": final_status,
        "final_url": final_url,
        "page_title": page_title,
        "html_lang": html_lang,
        "locked": locked,
        "image_urls": image_urls,
        "downloaded_images": downloaded_images,
        "skipped_images": skipped_images,
    }
