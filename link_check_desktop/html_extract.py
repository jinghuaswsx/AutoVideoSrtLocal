from __future__ import annotations

import json
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup


def _absolute_image_url(raw_url: str, base_url: str) -> str:
    absolute = urljoin(base_url, raw_url)
    parsed = urlparse(absolute)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def _image_dedupe_key(image_url: str) -> str:
    parsed = urlparse(image_url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _is_placeholder_src(src: str) -> bool:
    lowered = src.strip().lower()
    return lowered.startswith("data:image/svg") or lowered.startswith("data:") or "svg" in lowered and "placeholder" in lowered


def _image_source(node) -> str | None:
    for attr in ("data-master", "data-src", "src"):
        value = node.get(attr)
        if value and not (attr == "src" and _is_placeholder_src(value)):
            return value
    return None


def _append_image(items: list[dict], seen: set[str], *, source_url: str, kind: str) -> None:
    dedupe_key = _image_dedupe_key(source_url)
    if dedupe_key in seen:
        return
    seen.add(dedupe_key)
    items.append({"kind": kind, "source_url": source_url})


def _selected_variant_id(page_url: str) -> str:
    for key, value in parse_qsl(urlparse(page_url).query, keep_blank_values=True):
        if key == "variant":
            return value.strip()
    return ""


def _script_payloads(soup: BeautifulSoup):
    for node in soup.find_all("script"):
        body = (node.string or node.get_text() or "").strip()
        if not body or body[0] not in "[{":
            continue
        try:
            yield json.loads(body)
        except Exception:
            continue


def _variant_candidates(payload) -> list[dict]:
    if isinstance(payload, list) and payload and all(isinstance(item, dict) for item in payload):
        return payload
    if isinstance(payload, dict):
        variants = payload.get("variants")
        if isinstance(variants, list) and all(isinstance(item, dict) for item in variants):
            return variants
    return []


def _variant_featured_image_url(variant: dict) -> str:
    featured_media = variant.get("featured_media") or {}
    if isinstance(featured_media, dict):
        preview = featured_media.get("preview_image") or {}
        if isinstance(preview, dict) and preview.get("src"):
            return str(preview.get("src") or "").strip()
    featured_image = variant.get("featured_image") or {}
    if isinstance(featured_image, dict) and featured_image.get("src"):
        return str(featured_image.get("src") or "").strip()
    image = variant.get("image") or {}
    if isinstance(image, dict) and image.get("src"):
        return str(image.get("src") or "").strip()
    return ""


def _variant_featured_images(soup: BeautifulSoup, *, base_url: str) -> list[str]:
    selected_variant = _selected_variant_id(base_url)
    if not selected_variant:
        return []

    urls: list[str] = []
    for payload in _script_payloads(soup):
        for variant in _variant_candidates(payload):
            variant_id = str(variant.get("id") or "").strip()
            if variant_id != selected_variant:
                continue
            source_url = _variant_featured_image_url(variant)
            if source_url:
                urls.append(_absolute_image_url(source_url, base_url))
    return urls


def extract_images_from_html(html: str, *, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()

    carousel_selectors = [
        "[data-media-id] img",
        ".t4s-product__media-item img",
        ".product__media img",
        ".featured img",
    ]
    detail_selectors = [
        ".t4s-rte.t4s-tab-content img",
        ".rte img",
        ".product__description img",
        "[class*='description'] img",
    ]

    for source_url in _variant_featured_images(soup, base_url=base_url):
        _append_image(items, seen, source_url=source_url, kind="carousel")

    for selector in carousel_selectors:
        for node in soup.select(selector):
            src = _image_source(node)
            if not src:
                continue
            _append_image(items, seen, source_url=_absolute_image_url(src, base_url), kind="carousel")

    for selector in detail_selectors:
        for node in soup.select(selector):
            src = _image_source(node)
            if not src:
                continue
            _append_image(items, seen, source_url=_absolute_image_url(src, base_url), kind="detail")

    return items
