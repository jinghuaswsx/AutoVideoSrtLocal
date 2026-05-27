from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


class LocaleLockError(RuntimeError):
    pass


def _add_cache_buster(url: str) -> str:
    import time
    try:
        parsed = urlparse(url)
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        
        # Determine if it's a Shopify image CDN or already contains 'v' version parameter
        netloc_lower = parsed.netloc.lower()
        is_shopify = "shopify" in netloc_lower or "shopifycdn" in netloc_lower
        has_v_param = any(k == "v" for k, v in query_pairs)
        
        if is_shopify or has_v_param:
            # For Shopify-related CDN where 'v' acts as the cache key, rewrite it to force origin fetch
            query_pairs = [(k, v) for k, v in query_pairs if k not in ("nocache", "t", "_", "v")]
            timestamp = str(int(time.time() * 1000))
            query_pairs.append(("v", timestamp))
            query_pairs.append(("nocache", timestamp))
        else:
            # For general URLs, stick to the safe, standard nocache parameter to ensure backward and test compatibility
            query_pairs = [(k, v) for k, v in query_pairs if k not in ("nocache", "t", "_")]
            query_pairs.append(("nocache", str(int(time.time() * 1000))))
            
        query = urlencode(query_pairs, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))
    except Exception:
        return url


class ImageRedirectMismatchError(RuntimeError):
    pass


def _accept_language(code: str) -> str:
    mapping = {
        "de": "de-DE,de;q=0.9,en;q=0.8",
        "fr": "fr-FR,fr;q=0.9,en;q=0.8",
        "pt": "pt-PT,pt;q=0.9,en;q=0.8",
    }
    return mapping.get(code, f"{code};q=0.9,en;q=0.8")


def _absolute_image_url(raw_url: str, base_url: str) -> str:
    absolute = urljoin(base_url, raw_url)
    parsed = urlparse(absolute)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def _normalized_page_url(url: str) -> str:
    parsed = urlparse(url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    normalized_query = urlencode(query_pairs, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, normalized_query, ""))


def _image_dedupe_key(image_url: str) -> str:
    parsed = urlparse(image_url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _same_image_target(requested_url: str, resolved_url: str) -> bool:
    requested = urlparse(requested_url)
    resolved = urlparse(resolved_url)
    return (
        requested.netloc.lower() == resolved.netloc.lower()
        and requested.path == resolved.path
    )


def _page_lang(soup: BeautifulSoup) -> str:
    html = soup.find("html")
    return (html.get("lang") or "").strip().lower() if html else ""


def _locale_prefix(value: str) -> str:
    return value.strip().lower().split("-", 1)[0]


def _resolved_url_matches_locale(url: str, target_language: str) -> bool:
    normalized_target = _locale_prefix(target_language)
    segments = [segment.lower() for segment in urlparse(url).path.split("/") if segment]
    if not segments:
        return False
    return _locale_prefix(segments[0]) == normalized_target


def _is_locale_locked(*, resolved_url: str, page_language: str, target_language: str) -> bool:
    normalized_target = _locale_prefix(target_language)
    page_language = page_language.strip().lower()
    page_matches = bool(page_language) and _locale_prefix(page_language) == normalized_target
    if page_language and not page_matches:
        return False
    return page_matches or _resolved_url_matches_locale(resolved_url, normalized_target)


def _raise_for_status(response) -> None:
    checker = getattr(response, "raise_for_status", None)
    if callable(checker):
        checker()
        return
    status_code = getattr(response, "status_code", 200)
    if status_code >= 400:
        raise requests.HTTPError(f"HTTP {status_code}")


def _is_placeholder_src(src: str) -> bool:
    lowered = src.strip().lower()
    return lowered.startswith("data:image/svg") or lowered.startswith("data:") or "svg" in lowered and "placeholder" in lowered


def _image_source(node) -> str | None:
    # 1. Prefer 'src' if it is present and NOT a placeholder (e.g. populated after lazy load or browser execution)
    src_val = node.get("src")
    if src_val and not _is_placeholder_src(src_val):
        return src_val

    # 2. Otherwise fall back to lazy-loading attributes or placeholder 'src'
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


def _rel_tokens(node) -> set[str]:
    raw = node.get("rel") or []
    if isinstance(raw, str):
        return {raw.strip().lower()} if raw.strip() else set()
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def _canonical_url(soup: BeautifulSoup, current_url: str) -> str:
    for node in soup.find_all("link", href=True):
        if "canonical" in _rel_tokens(node):
            return _absolute_image_url(node["href"], current_url)
    return ""


def _alternate_locale_url(soup: BeautifulSoup, *, current_url: str, requested_url: str, target_language: str) -> str:
    normalized_target = _locale_prefix(target_language)
    for node in soup.find_all("link", href=True):
        rel = _rel_tokens(node)
        if "alternate" not in rel:
            continue
        hreflang = _locale_prefix(str(node.get("hreflang") or "").strip())
        if hreflang != normalized_target:
            continue
        href = str(node.get("href") or "").strip()
        if href:
            return _merge_requested_query(_absolute_image_url(href, current_url), requested_url)

    canonical = _canonical_url(soup, current_url)
    if canonical and _resolved_url_matches_locale(canonical, normalized_target):
        return _merge_requested_query(canonical, requested_url)

    if _resolved_url_matches_locale(requested_url, normalized_target):
        return requested_url
    return ""


def _merge_requested_query(target_url: str, requested_url: str) -> str:
    target = urlparse(target_url)
    merged: dict[str, str] = {key: value for key, value in parse_qsl(target.query, keep_blank_values=True)}
    for key, value in parse_qsl(urlparse(requested_url).query, keep_blank_values=True):
        merged[key] = value
    query = urlencode(list(merged.items()), doseq=True)
    return urlunparse((target.scheme, target.netloc, target.path, target.params, query, ""))


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
            absolute = _absolute_image_url(src, base_url)
            _append_image(items, seen, source_url=absolute, kind="carousel")

    for selector in detail_selectors:
        for node in soup.select(selector):
            src = _image_source(node)
            if not src:
                continue
            absolute = _absolute_image_url(src, base_url)
            _append_image(items, seen, source_url=absolute, kind="detail")

    # Swapping matching English carousel URLs with localized URLs
    # 1. Compile a pool of all img URLs found on the page
    all_urls = []
    for node in soup.find_all("img"):
        for attr in ("src", "data-src", "data-master"):
            val = node.get(attr)
            if val:
                all_urls.append(_absolute_image_url(val, base_url))

    # 2. Map tokens to their corresponding localized image URLs
    # Naming convention: loc_from_url_en_XX_{token}.jpg
    import re
    token_to_localized = {}
    loc_pattern = re.compile(r"from_url_en_\d+_(?P<token>[a-f0-9]{28,})", re.I)
    for url in all_urls:
        match = loc_pattern.search(url)
        if match:
            token = match.group("token").lower()
            token_to_localized[token] = url

    # 3. Swap English carousel URLs with matching localized URLs
    carousel_token_re = re.compile(r"([a-f0-9]{28,})", re.I)
    for item in items:
        if item["kind"] == "carousel":
            url = item["source_url"]
            if loc_pattern.search(url):
                continue
            token_match = carousel_token_re.search(url.lower())
            if token_match:
                token = token_match.group(1).lower()
                if token in token_to_localized:
                    item["source_url"] = token_to_localized[token]

    return items


@dataclass
class FetchedPage:
    requested_url: str
    resolved_url: str
    page_language: str
    html: str
    images: list[dict]
    locale_evidence: dict | None = None


class LinkCheckFetcher:
    def __init__(self) -> None:
        self.session = requests.Session()

    def fetch_page(self, url: str, target_language: str) -> FetchedPage:
        nocache_url = _add_cache_buster(url)
        
        with sync_playwright() as p:
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            locale_map = {
                "de": "de-DE",
                "fr": "fr-FR",
                "pt": "pt-PT",
                "it": "it-IT",
                "es": "es-ES",
                "ja": "ja-JP",
            }
            locale = locale_map.get(target_language.lower(), f"{target_language}-{target_language.upper()}")
            
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=user_agent,
                locale=locale,
                extra_http_headers={
                    "Accept-Language": _accept_language(target_language),
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
            page = context.new_page()
            
            try:
                page.goto(nocache_url, wait_until="domcontentloaded", timeout=30000)
                # Wait for dynamic translations/scripts (e.g. EZ Product Image Translate)
                page.wait_for_timeout(3000)
                
                resolved_url = page.url
                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                lang = _page_lang(soup)
                
                locked = _is_locale_locked(
                    resolved_url=resolved_url,
                    page_language=lang,
                    target_language=target_language,
                )
                
                if not locked:
                    retry_url = _alternate_locale_url(
                        soup,
                        current_url=resolved_url,
                        requested_url=nocache_url,
                        target_language=target_language,
                    )
                    if retry_url and _normalized_page_url(retry_url) != _normalized_page_url(resolved_url):
                        page.goto(retry_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3000)
                        resolved_url = page.url
                        html = page.content()
                        soup = BeautifulSoup(html, "html.parser")
                        lang = _page_lang(soup)
                        locked = _is_locale_locked(
                            resolved_url=resolved_url,
                            page_language=lang,
                            target_language=target_language,
                        )
            finally:
                browser.close()
                
        locale_evidence = {
            "target_language": target_language,
            "requested_url": url,
            "lock_source": "alternate_locale" if (resolved_url != nocache_url) else "initial",
            "locked": locked,
            "failure_reason": "" if locked else f"locale lock failed: target={target_language} resolved_url={resolved_url} page_lang={lang or 'unknown'}",
            "attempts": [
                {
                    "phase": "initial",
                    "attempt_index": 1,
                    "wait_seconds_before_request": 0,
                    "requested_url": nocache_url,
                    "resolved_url": resolved_url,
                    "page_language": lang,
                    "locked": locked,
                }
            ],
        }

        if not locked:
            exc = LocaleLockError(locale_evidence["failure_reason"])
            exc.locale_evidence = locale_evidence
            raise exc

        return FetchedPage(
            requested_url=url,
            resolved_url=resolved_url,
            page_language=lang,
            html=html,
            images=extract_images_from_html(html, base_url=resolved_url),
            locale_evidence=locale_evidence,
        )

    def download_images(self, images: list[dict], task_dir: str | Path) -> list[dict]:
        output_dir = Path(task_dir) / "site_images"
        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded = []
        for index, item in enumerate(images):
            nocache_url = _add_cache_buster(item["source_url"])
            response = self.session.get(
                nocache_url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
                allow_redirects=True,
                timeout=20,
            )
            _raise_for_status(response)
            if not _same_image_target(item["source_url"], response.url):
                raise ImageRedirectMismatchError(
                    f"image redirect mismatch: requested={item['source_url']} resolved={response.url}"
                )
            suffix = Path(urlparse(item["source_url"]).path).suffix or ".jpg"
            local_path = output_dir / f"site_{index:03d}{suffix}"
            local_path.write_bytes(response.content)
            downloaded.append(
                {
                    **item,
                    "id": f"site-{index}",
                    "local_path": str(local_path),
                    "resolved_source_url": response.url,
                }
            )
        return downloaded
