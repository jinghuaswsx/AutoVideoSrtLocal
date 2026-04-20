from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


class LocaleLockError(RuntimeError):
    pass


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


def _image_dedupe_key(image_url: str) -> str:
    parsed = urlparse(image_url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


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
    for attr in ("data-master", "data-src", "src"):
        value = node.get(attr)
        if value and not (attr == "src" and _is_placeholder_src(value)):
            return value
    return None


def _empty_locale_evidence(requested_url: str, target_language: str) -> dict:
    return {
        "target_language": target_language,
        "requested_url": requested_url,
        "lock_source": "",
        "locked": False,
        "failure_reason": "",
        "attempts": [],
    }


def _append_attempt(
    evidence: dict,
    *,
    phase: str,
    attempt_index: int,
    wait_seconds: int,
    requested_url: str,
    resolved_url: str,
    page_language: str,
    locked: bool,
) -> None:
    evidence["attempts"].append(
        {
            "phase": phase,
            "attempt_index": attempt_index,
            "wait_seconds_before_request": wait_seconds,
            "requested_url": requested_url,
            "resolved_url": resolved_url,
            "page_language": page_language,
            "locked": locked,
        }
    )


def _alternate_locale_url(soup: BeautifulSoup, *, current_url: str, requested_url: str, target_language: str) -> str:
    normalized_target = _locale_prefix(target_language)
    for node in soup.select("link[rel='alternate'][hreflang]"):
        href = (node.get("href") or "").strip()
        hreflang = _locale_prefix(node.get("hreflang") or "")
        if not href or hreflang != normalized_target:
            continue
        alternate = urlparse(urljoin(current_url, href))
        fallback_query = urlparse(current_url).query or urlparse(requested_url).query
        query = alternate.query or fallback_query
        return urlunparse((alternate.scheme, alternate.netloc, alternate.path, alternate.params, query, ""))
    return ""


def _build_download_evidence(item: dict, resolved_url: str, *, preserved_asset: bool) -> dict:
    return {
        "requested_source_url": item["source_url"],
        "resolved_source_url": resolved_url,
        "redirect_preserved_asset": preserved_asset,
        "variant_selected": bool(item.get("variant_selected")),
        "evidence_status": "ok" if preserved_asset else "mismatch",
        "evidence_reason": "" if preserved_asset else "final image URL did not preserve the original asset path",
    }


def _same_image_target(requested_url: str, resolved_url: str) -> bool:
    return _image_dedupe_key(requested_url) == _image_dedupe_key(resolved_url)


def extract_images_from_html(html: str, *, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen: dict[str, int] = {}

    carousel_selectors = [
        ("[data-media-id] img", False),
        (".t4s-product__media-item img", False),
        (".product__media img", False),
        (".featured img", True),
    ]
    detail_selectors = [
        ".t4s-rte.t4s-tab-content img",
        ".rte img",
        ".product__description img",
        "[class*='description'] img",
    ]

    for selector, variant_selected in carousel_selectors:
        for node in soup.select(selector):
            src = _image_source(node)
            if not src:
                continue
            absolute = _absolute_image_url(src, base_url)
            dedupe_key = _image_dedupe_key(absolute)
            if dedupe_key in seen:
                if variant_selected:
                    items[seen[dedupe_key]]["variant_selected"] = True
                continue
            seen[dedupe_key] = len(items)
            item = {"kind": "carousel", "source_url": absolute}
            if variant_selected:
                item["variant_selected"] = True
            items.append(item)

    for selector in detail_selectors:
        for node in soup.select(selector):
            src = _image_source(node)
            if not src:
                continue
            absolute = _absolute_image_url(src, base_url)
            dedupe_key = _image_dedupe_key(absolute)
            if dedupe_key in seen:
                continue
            seen[dedupe_key] = len(items)
            items.append({"kind": "detail", "source_url": absolute})

    return items


@dataclass
class FetchedPage:
    requested_url: str
    resolved_url: str
    page_language: str
    html: str
    images: list[dict]
    locale_evidence: dict


class LinkCheckFetcher:
    def __init__(self, *, sleep_func=None) -> None:
        self.session = requests.Session()
        self._sleep = sleep_func or time.sleep

    def _request_page(self, url: str, target_language: str):
        return self.session.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": _accept_language(target_language)},
            allow_redirects=True,
            timeout=20,
        )

    def _lock_target_locale(self, requested_url: str, target_language: str):
        evidence = _empty_locale_evidence(requested_url, target_language)
        attempt_index = 0
        response = None
        soup = None
        lang = ""
        alternate_soup = None
        alternate_current_url = requested_url

        for phase, wait_seconds in [("initial", 0), ("warmup", 2), ("warmup", 2)]:
            if phase == "warmup":
                self._sleep(wait_seconds)
            attempt_index += 1
            response = self._request_page(requested_url, target_language)
            _raise_for_status(response)
            soup = BeautifulSoup(response.text, "html.parser")
            if alternate_soup is None:
                alternate_soup = soup
                alternate_current_url = response.url
            lang = _page_lang(soup)
            locked = _is_locale_locked(
                resolved_url=response.url,
                page_language=lang,
                target_language=target_language,
            )
            _append_attempt(
                evidence,
                phase=phase,
                attempt_index=attempt_index,
                wait_seconds=wait_seconds if phase == "warmup" else 0,
                requested_url=requested_url,
                resolved_url=response.url,
                page_language=lang,
                locked=locked,
            )
            if locked:
                evidence["locked"] = True
                evidence["lock_source"] = "initial" if phase == "initial" else f"warmup_attempt_{attempt_index}"
                return response, soup, lang, evidence

        retry_url = _alternate_locale_url(
            alternate_soup or soup,
            current_url=alternate_current_url,
            requested_url=requested_url,
            target_language=target_language,
        )
        if retry_url:
            attempt_index += 1
            response = self._request_page(retry_url, target_language)
            _raise_for_status(response)
            soup = BeautifulSoup(response.text, "html.parser")
            lang = _page_lang(soup)
            locked = _is_locale_locked(
                resolved_url=response.url,
                page_language=lang,
                target_language=target_language,
            )
            _append_attempt(
                evidence,
                phase="alternate_locale",
                attempt_index=attempt_index,
                wait_seconds=0,
                requested_url=retry_url,
                resolved_url=response.url,
                page_language=lang,
                locked=locked,
            )
            if locked:
                evidence["locked"] = True
                evidence["lock_source"] = "alternate_locale"
                return response, soup, lang, evidence

        failure_reason = (
            f"locale lock failed: target={target_language} "
            f"resolved_url={response.url} page_lang={lang or 'unknown'}"
        )
        evidence["failure_reason"] = failure_reason
        raise LocaleLockError(failure_reason)

    def fetch_page(self, url: str, target_language: str) -> FetchedPage:
        response, _, lang, evidence = self._lock_target_locale(url, target_language)
        return FetchedPage(
            requested_url=url,
            resolved_url=response.url,
            page_language=lang,
            html=response.text,
            images=extract_images_from_html(response.text, base_url=response.url),
            locale_evidence=evidence,
        )

    def download_images(self, images: list[dict], task_dir: str | Path) -> list[dict]:
        output_dir = Path(task_dir) / "site_images"
        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded = []
        for index, item in enumerate(images):
            response = self.session.get(
                item["source_url"],
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
                timeout=20,
            )
            _raise_for_status(response)
            preserved_asset = _same_image_target(item["source_url"], response.url)
            if not preserved_asset:
                raise ImageRedirectMismatchError("final image URL did not preserve the original asset path")
            suffix = Path(urlparse(item["source_url"]).path).suffix or ".jpg"
            local_path = output_dir / f"site_{index:03d}{suffix}"
            local_path.write_bytes(response.content)
            downloaded.append(
                {
                    **item,
                    "id": f"site-{index}",
                    "resolved_source_url": response.url,
                    "local_path": str(local_path),
                    "download_evidence": _build_download_evidence(
                        item,
                        response.url,
                        preserved_asset=preserved_asset,
                    ),
                }
            )
        return downloaded
