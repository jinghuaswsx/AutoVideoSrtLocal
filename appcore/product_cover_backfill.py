from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import requests

from appcore import local_media_storage, medias, object_keys, pushes
from appcore.db import query, query_one
from appcore.link_check_fetcher import extract_images_from_html

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 15 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 AutoVideoSrt-CoverBackfill"
REQUEST_TIMEOUT = 20
PAGE_TIMEOUT_MS = 30_000
CAROUSEL_WAIT_TIMEOUT_MS = 10_000
CAROUSEL_SELECTOR = (
    "[data-media-id] img, "
    ".t4s-product__media-item img, "
    ".product__media img, "
    ".featured img"
)


def ensure_playwright_browser_path() -> None:
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    candidates = [
        Path.cwd() / ".playwright-browsers",
        Path(__file__).resolve().parents[1] / ".playwright-browsers",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(candidate)
            return


def find_missing_cover_products(*, product_code: str | None = None) -> list[dict]:
    where = [
        "p.deleted_at IS NULL",
        "(c.object_key IS NULL OR c.object_key='')",
        "COALESCE(p.product_code, '') <> ''",
    ]
    args: list[object] = []
    if product_code:
        where.append("p.product_code=%s")
        args.append(product_code.strip())

    sql = (
        "SELECT p.* "
        "FROM media_products p "
        "LEFT JOIN media_product_covers c "
        "  ON c.product_id=p.id AND c.lang='en' "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY p.id ASC"
    )
    return query(sql, tuple(args))


def get_product_by_code(product_code: str) -> dict | None:
    code = (product_code or "").strip()
    if not code:
        return None
    return query_one(
        "SELECT * FROM media_products WHERE product_code=%s AND deleted_at IS NULL",
        (code,),
    )


def pick_first_carousel_image(images: list[dict]) -> str:
    for item in images or []:
        if (item.get("kind") or "carousel") != "carousel":
            continue
        url = str(item.get("source_url") or "").strip()
        lowered = url.lower()
        if not url or lowered.startswith("data:"):
            continue
        if ".svg" in lowered or "placeholder" in lowered:
            continue
        return url
    return ""


def fetch_carousel_images(product_url: str) -> list[dict]:
    ensure_playwright_browser_path()

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(product_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            try:
                page.wait_for_selector(CAROUSEL_SELECTOR, timeout=CAROUSEL_WAIT_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                log.info("cover backfill carousel selector not found before timeout: %s", product_url)
            html = page.content()
            base_url = page.url or product_url
        finally:
            browser.close()

    return [
        item for item in extract_images_from_html(html, base_url=base_url)
        if (item.get("kind") or "") == "carousel"
    ]


def _content_type_for_response(response, image_url: str) -> str:
    content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if not content_type:
        content_type = mimetypes.guess_type(urlparse(image_url).path)[0] or "image/jpeg"
    return content_type


def _image_extension(content_type: str, image_url: str) -> str:
    parsed_suffix = Path(urlparse(image_url).path).suffix.lower()
    if parsed_suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return parsed_suffix
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type, ".jpg")


def download_image_to_media_storage(image_url: str, product_id: int, user_id: int) -> str:
    parsed = urlparse((image_url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("cover image URL must be http/https")

    response = requests.get(
        image_url,
        timeout=REQUEST_TIMEOUT,
        stream=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    content_type = _content_type_for_response(response, image_url)
    if not content_type.startswith("image/"):
        raise ValueError(f"cover URL is not an image: {content_type}")

    payload = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        payload.extend(chunk)
        if len(payload) > MAX_IMAGE_BYTES:
            raise ValueError("cover image too large (>15MB)")

    basename = os.path.basename(parsed.path or "") or "cover"
    ext = _image_extension(content_type, image_url)
    filename = f"auto_cover_{basename}"
    if not filename.lower().endswith(ext):
        filename += ext
    object_key = object_keys.build_media_object_key(user_id, product_id, filename)
    local_media_storage.write_bytes(object_key, bytes(payload))
    return object_key


def backfill_product_cover(product: dict) -> dict:
    product_id = int(product["id"])
    user_id = int(product["user_id"])
    product_url = pushes.resolve_product_page_url("en", product)
    if not product_url:
        return {"status": "skipped", "product_id": product_id, "reason": "missing_product_url"}

    images = fetch_carousel_images(product_url)
    image_url = pick_first_carousel_image(images)
    if not image_url:
        return {"status": "skipped", "product_id": product_id, "reason": "missing_carousel_image"}

    object_key = download_image_to_media_storage(image_url, product_id, user_id)
    medias.set_product_cover(product_id, "en", object_key)
    log.info(
        "backfilled product cover product_id=%s product_code=%s image_url=%s object_key=%s",
        product_id,
        product.get("product_code"),
        image_url,
        object_key,
    )
    return {
        "status": "backfilled",
        "product_id": product_id,
        "product_url": product_url,
        "image_url": image_url,
        "object_key": object_key,
    }


def backfill_product_cover_by_code(product_code: str) -> dict:
    product = get_product_by_code(product_code)
    if not product:
        return {"status": "skipped", "product_code": product_code, "reason": "product_not_found"}
    return backfill_product_cover(product)


def backfill_all_missing_covers() -> dict:
    products = find_missing_cover_products()
    summary = {"total": len(products), "backfilled": 0, "failed": 0, "skipped": 0}
    for product in products:
        try:
            result = backfill_product_cover(product)
        except Exception:
            summary["failed"] += 1
            log.exception(
                "product cover backfill failed product_id=%s product_code=%s",
                product.get("id"),
                product.get("product_code"),
            )
            continue
        if result.get("status") == "backfilled":
            summary["backfilled"] += 1
        elif result.get("status") == "skipped":
            summary["skipped"] += 1
        else:
            summary["failed"] += 1
    return summary

