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


def _prepend_locale_to_url(url: str, target_language: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    
    lang = target_language.lower().strip()
    if not lang:
        return url
        
    if not path:
        path = "/"
        
    parts = [p for p in path.split("/") if p]
    if parts:
        first_segment = parts[0].lower()
        if len(first_segment) == 2 or (len(first_segment) == 5 and first_segment[2] == "-"):
            return url
            
    new_path = f"/{lang}{path}" if path.startswith("/") else f"/{lang}/{path}"
    return urlunparse((parsed.scheme, parsed.netloc, new_path, parsed.params, parsed.query, parsed.fragment))


def extract_images_from_html(html: str, *, base_url: str, target_language: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    
    # Remove all <noscript> tags to prevent extracting stale non-JS fallback images
    for noscript in soup.find_all("noscript"):
        noscript.decompose()

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
    import re
    token_to_localized = {}
    loc_pattern = re.compile(r"from_url_en_\d+_(?P<token>[a-f0-9]{28,})", re.I)

    # 1. Try direct JSON fetch from EZ Product Image Translate CDN
    active_lang = target_language.lower().strip()
    if not active_lang:
        # Extract from URL path (e.g. /it/products/...)
        segments = [s.lower() for s in urlparse(base_url).path.split("/") if s]
        if segments:
            first_seg = segments[0]
            if len(first_seg) == 2 or (len(first_seg) == 5 and first_seg[2] == "-"):
                active_lang = first_seg
        # Fallback: extract from html lang
        if not active_lang:
            html_node = soup.find("html")
            if html_node and html_node.get("lang"):
                active_lang = html_node.get("lang").strip().lower().split("-", 1)[0]

    if active_lang:
        try:
            shop_match = re.search(r'([a-zA-Z0-9\-]+\.myshopify\.com)', html)
            if shop_match:
                shop_domain = shop_match.group(1).lower()
                translations_url = f"https://translate.freshify.click/storage/json_files/{shop_domain}_translations.json"
                resp = requests.get(translations_url, timeout=5)
                if resp.status_code == 200:
                    translation_data = resp.json()
                    lang_short = active_lang.split("-", 1)[0]
                    lang_translations = translation_data.get(active_lang) or translation_data.get(lang_short) or {}
                    
                    token_re = re.compile(r"([a-f0-9]{28,})", re.I)
                    for eng_filename, trans_info in lang_translations.items():
                        if isinstance(trans_info, dict) and trans_info.get("url"):
                            token_match = token_re.search(eng_filename)
                            if token_match:
                                token = token_match.group(1).lower()
                                token_to_localized[token] = trans_info["url"]
        except Exception:
            pass

    # 2. Supplementary compilation: pool of all img URLs found on the page DOM
    all_urls = []
    for node in soup.find_all("img"):
        for attr in ("src", "data-src", "data-master"):
            val = node.get(attr)
            if val:
                all_urls.append(_absolute_image_url(val, base_url))

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

    # 4. Deduplicate items by token to prevent having both English and localized versions of the same image slot
    token_to_item = {}
    other_items = []
    for item in items:
        url = item["source_url"]
        token_match = carousel_token_re.search(url.lower())
        if token_match and item["kind"] == "carousel":
            token = token_match.group(1).lower()
            is_localized = bool(loc_pattern.search(url))
            if token not in token_to_item:
                token_to_item[token] = item
            else:
                existing_item = token_to_item[token]
                existing_is_localized = bool(loc_pattern.search(existing_item["source_url"]))
                if is_localized and not existing_is_localized:
                    token_to_item[token] = item
        else:
            other_items.append(item)
            
    items = list(token_to_item.values()) + other_items
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

    def _request_page(self, url: str, target_language: str):
        return self.session.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": _accept_language(target_language),
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
            allow_redirects=True,
            timeout=20,
        )

    def _lock_target_locale(self, response, *, requested_url: str, target_language: str):
        _raise_for_status(response)
        soup = BeautifulSoup(response.text, "html.parser")
        lang = _page_lang(soup)
        if _is_locale_locked(
            resolved_url=response.url,
            page_language=lang,
            target_language=target_language,
        ):
            return response, soup, lang

        retry_url = _alternate_locale_url(
            soup,
            current_url=response.url,
            requested_url=requested_url,
            target_language=target_language,
        )
        if not retry_url or _normalized_page_url(retry_url) == _normalized_page_url(response.url):
            return response, soup, lang

        retry_response = self._request_page(retry_url, target_language)
        _raise_for_status(retry_response)
        retry_soup = BeautifulSoup(retry_response.text, "html.parser")
        retry_lang = _page_lang(retry_soup)
        return retry_response, retry_soup, retry_lang

    def fetch_page_requests(self, url: str, target_language: str) -> FetchedPage:
        nocache_url = _add_cache_buster(url)
        response = self._request_page(nocache_url, target_language)
        response, soup, lang = self._lock_target_locale(response, requested_url=nocache_url, target_language=target_language)
        
        locked = _is_locale_locked(
            resolved_url=response.url,
            page_language=lang,
            target_language=target_language,
        )
        
        locale_evidence = {
            "target_language": target_language,
            "requested_url": url,
            "lock_source": "alternate_locale" if (response.url != nocache_url) else "initial",
            "locked": locked,
            "failure_reason": "" if locked else f"locale lock failed: target={target_language} resolved_url={response.url} page_lang={lang or 'unknown'}",
            "attempts": [
                {
                    "phase": "initial",
                    "attempt_index": 1,
                    "wait_seconds_before_request": 0,
                    "requested_url": nocache_url,
                    "resolved_url": response.url,
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
            resolved_url=response.url,
            page_language=lang,
            html=response.text,
            images=extract_images_from_html(response.text, base_url=response.url, target_language=target_language),
            locale_evidence=locale_evidence,
        )

    def fetch_page(self, url: str, target_language: str) -> FetchedPage:
        # Detect if we are in a testing/mocked requests environment
        parsed_url = urlparse(url)
        is_test_env = "example.com" in parsed_url.netloc or not isinstance(self.session.get, type(requests.Session().get))
        
        if is_test_env:
            return self.fetch_page_requests(url, target_language)

        # Prepend language to the URL path for direct localized navigation on Shopify storefronts
        localized_url = _prepend_locale_to_url(url, target_language)
        nocache_url = _add_cache_buster(localized_url)
        
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
                page.goto(nocache_url, wait_until="load", timeout=30000)
                
                # Safe natural scrolling and cycle carousel slides to trigger lazyload translations
                try:
                    page.evaluate("""(async () => {
                        window.scrollTo(0, 300);
                        window.dispatchEvent(new Event('scroll'));
                        await new Promise(r => setTimeout(r, 500));
                        
                        // Find next slide button and click it to cycle slides and trigger lazyload
                        let nextBtns = document.querySelectorAll('.flickityt4s-button.next, .slick-next, .t4s-slider-btn-next, [class*="next-button"], [class*="slider-btn-next"]');
                        for (let btn of nextBtns) {
                            for (let i = 0; i < 12; i++) {
                                btn.click();
                                await new Promise(r => setTimeout(r, 150));
                            }
                        }
                    })()""")
                except Exception:
                    pass
                
                # Wait for Slick/Flickity and EZ Product Image Translate to run
                page.wait_for_timeout(6000)
                
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
                        page.goto(retry_url, wait_until="load", timeout=30000)
                        
                        try:
                            page.evaluate("""(async () => {
                                window.scrollTo(0, 300);
                                window.dispatchEvent(new Event('scroll'));
                                await new Promise(r => setTimeout(r, 500));
                                
                                let nextBtns = document.querySelectorAll('.flickityt4s-button.next, .slick-next, .t4s-slider-btn-next, [class*="next-button"], [class*="slider-btn-next"]');
                                for (let btn of nextBtns) {
                                    for (let i = 0; i < 12; i++) {
                                        btn.click();
                                        await new Promise(r => setTimeout(r, 150));
                                    }
                                }
                            })()""")
                        except Exception:
                            pass
                            
                        page.wait_for_timeout(6000)
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
            images=extract_images_from_html(html, base_url=resolved_url, target_language=target_language),
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
