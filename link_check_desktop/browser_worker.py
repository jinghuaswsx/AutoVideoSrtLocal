from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import requests


def _report(status_cb, message: str) -> None:
    if status_cb is not None:
        status_cb(message)


def _is_locked(html_lang: str, target_language: str) -> bool:
    return (html_lang or "").strip().lower().startswith((target_language or "").strip().lower())


def _sanitize_extension(url: str, content_type: str | None) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
    return guessed or ".jpg"


def _collect_image_urls(page) -> list[str]:
    return page.eval_on_selector_all(
        "img",
        """
        elements => {
            const urls = [];
            for (const element of elements) {
                const src = element.currentSrc || element.src || "";
                if (!src || src.startsWith("data:")) {
                    continue;
                }
                urls.push(src);
            }
            return Array.from(new Set(urls));
        }
        """,
    )


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
    output_path.write_bytes(response.content)
    return {
        "requested_url": url,
        "resolved_url": response.url,
        "redirected": response.url != url,
        "preserved_asset": True,
        "content_type": response.headers.get("Content-Type") or "",
    }


def capture_page(*, target_url: str, target_language: str, workspace, status_cb=None) -> dict:
    from playwright.sync_api import sync_playwright

    _report(status_cb, "正在打开可视浏览器")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=False)
        context = browser.new_context(locale=target_language)
        page = context.new_page()
        page.set_default_timeout(30000)

        _report(status_cb, "第一次打开目标页")
        page.goto(target_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        first_final_url = page.url
        first_html_lang = page.eval_on_selector("html", "el => el.lang || ''")
        redirected = first_final_url != target_url

        if redirected or not _is_locked(first_html_lang, target_language):
            _report(status_cb, "检测到重定向或语种未锁定，第二次打开原始目标页")
            page.goto(target_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

        final_url = page.url
        html_lang = page.eval_on_selector("html", "el => el.lang || ''")
        locked = _is_locked(html_lang, target_language)
        html = page.content()
        (workspace.root / "page.html").write_text(html, encoding="utf-8")

        image_urls: list[str] = []
        downloaded_images: list[dict] = []

        if locked:
            _report(status_cb, "正在提取页面图片")
            image_urls = _collect_image_urls(page)
            user_agent = page.evaluate("() => navigator.userAgent")
            session = _build_session(context, user_agent)
            for index, image_url in enumerate(image_urls, start=1):
                extension = _sanitize_extension(image_url, None)
                local_path = workspace.site_dir / f"site-{index:03d}{extension}"
                evidence = _download_image(session, image_url, local_path)
                downloaded_images.append({
                    "id": f"site-{index:03d}",
                    "kind": "page_image",
                    "source_url": image_url,
                    "local_path": str(local_path),
                    "download_evidence": evidence,
                })

        browser.close()

    return {
        "requested_url": target_url,
        "first_final_url": first_final_url,
        "final_url": final_url,
        "html_lang": html_lang,
        "locked": locked,
        "image_urls": image_urls,
        "downloaded_images": downloaded_images,
    }
