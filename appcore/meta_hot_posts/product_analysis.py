from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from appcore import llm_client
from appcore.meta_hot_posts.category_route import CATEGORY_MODEL, CATEGORY_PROVIDER
from appcore.meta_hot_posts.categories import TIKTOK_SHOP_US_L1_CATEGORIES


_PRODUCT_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class ProductAnalysisResult:
    title: str = ""
    main_image_url: str = ""
    price_min: float | None = None
    price_max: float | None = None
    currency: str = "USD"
    skus: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "main_image_url": self.main_image_url,
            "price_min": self.price_min,
            "price_max": self.price_max,
            "currency": self.currency,
            "skus": self.skus,
            "raw": self.raw,
        }


def detect_product_link_type(product_url: str) -> str:
    parsed = urlparse(str(product_url or "").strip())
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "tiktok.com" in host and "/shop/pdp" in path:
        return "tiktok_shop"
    if "/products/" in path or host.endswith(".myshopify.com"):
        return "shopify_product"
    return "generic_product"


def _absolute_url(value: Any, base_url: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    absolute = urljoin(base_url, text)
    parsed = urlparse(absolute)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def _price(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, Mapping):
        for key in ("amount", "local", "region", "price"):
            parsed = _price(value.get(key))
            if parsed is not None:
                return parsed
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"-?\d+(?:[,.]\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _shopify_price(value: Any) -> float | None:
    parsed = _price(value)
    if parsed is None:
        return None
    text = str(value or "").strip()
    if "." not in text and parsed >= 100:
        return round(parsed / 100, 2)
    return parsed


def _price_range(skus: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    prices = [float(item["price"]) for item in skus if item.get("price") is not None]
    if not prices:
        return None, None
    return min(prices), max(prices)


def _images_from_product(product: Mapping[str, Any], base_url: str) -> str:
    image = product.get("image")
    if isinstance(image, Mapping):
        return _absolute_url(image.get("src") or image.get("url"), base_url)
    if isinstance(image, list) and image:
        first = image[0]
        if isinstance(first, Mapping):
            return _absolute_url(first.get("src") or first.get("url"), base_url)
        return _absolute_url(first, base_url)
    if image:
        return _absolute_url(image, base_url)
    images = product.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, Mapping):
            return _absolute_url(first.get("src") or first.get("url"), base_url)
        return _absolute_url(first, base_url)
    return ""


def parse_shopify_product_json(payload: Mapping[str, Any], *, base_url: str) -> ProductAnalysisResult:
    product = payload.get("product") if isinstance(payload.get("product"), Mapping) else payload
    if not isinstance(product, Mapping):
        product = {}
    skus: list[dict[str, Any]] = []
    for variant in product.get("variants") or []:
        if not isinstance(variant, Mapping):
            continue
        price = _shopify_price(variant.get("price"))
        skus.append(
            {
                "sku": str(variant.get("sku") or variant.get("id") or "").strip(),
                "title": str(variant.get("title") or variant.get("name") or "").strip(),
                "price": price,
                "currency": str(variant.get("currency") or "USD").strip() or "USD",
            }
        )
    price_min, price_max = _price_range(skus)
    return ProductAnalysisResult(
        title=str(product.get("title") or product.get("name") or "").strip(),
        main_image_url=_images_from_product(product, base_url),
        price_min=price_min,
        price_max=price_max,
        currency=skus[0]["currency"] if skus else "USD",
        skus=skus,
        raw={"source": "shopify_json", "product": product},
    )


def _jsonld_payloads(soup: BeautifulSoup) -> list[Any]:
    payloads: list[Any] = []
    for node in soup.find_all("script", {"type": "application/ld+json"}):
        body = (node.string or node.get_text() or "").strip()
        if not body:
            continue
        try:
            payloads.append(json.loads(body))
        except json.JSONDecodeError:
            continue
    return payloads


def _iter_products(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_products(item)
    elif isinstance(payload, Mapping):
        graph = payload.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _iter_products(item)
        item_type = payload.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if "Product" in types:
            yield payload


def _offers_to_skus(offers: Any) -> tuple[list[dict[str, Any]], str]:
    if isinstance(offers, Mapping):
        offers = [offers]
    if not isinstance(offers, list):
        return [], "USD"
    skus: list[dict[str, Any]] = []
    currency = "USD"
    for offer in offers:
        if not isinstance(offer, Mapping):
            continue
        currency = str(offer.get("priceCurrency") or currency or "USD").strip() or "USD"
        skus.append(
            {
                "sku": str(offer.get("sku") or offer.get("mpn") or "").strip(),
                "title": str(offer.get("name") or "").strip(),
                "price": _price(offer.get("price") or offer.get("lowPrice")),
                "currency": currency,
            }
        )
    return skus, currency


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        node = soup.find("meta", {"property": name}) or soup.find("meta", {"name": name})
        if node and node.get("content"):
            return str(node.get("content") or "").strip()
    return ""


def _script_json_payloads(soup: BeautifulSoup) -> list[Any]:
    payloads: list[Any] = []
    for node in soup.find_all("script"):
        body = (node.string or node.get_text() or "").strip()
        if not body:
            continue
        if body[0] in "[{":
            try:
                payloads.append(json.loads(body))
                continue
            except json.JSONDecodeError:
                pass
        if "variants" not in body:
            continue
        match = re.search(r"(\{[^<]*\"variants\"\s*:\s*\[[^<]*\][^<]*\})", body, re.S)
        if match:
            try:
                payloads.append(json.loads(match.group(1)))
            except json.JSONDecodeError:
                continue
    return payloads


def parse_product_html(html: str, *, base_url: str) -> ProductAnalysisResult:
    soup = BeautifulSoup(html or "", "html.parser")
    for payload in _jsonld_payloads(soup):
        for product in _iter_products(payload):
            skus, currency = _offers_to_skus(product.get("offers"))
            price_min, price_max = _price_range(skus)
            return ProductAnalysisResult(
                title=str(product.get("name") or "").strip(),
                main_image_url=_images_from_product(product, base_url),
                price_min=price_min,
                price_max=price_max,
                currency=currency,
                skus=skus,
                raw={"source": "jsonld", "product": dict(product)},
            )

    title = _meta_content(soup, "og:title", "twitter:title")
    if not title and soup.title:
        title = soup.title.get_text(strip=True)
    image = _meta_content(soup, "og:image", "twitter:image")
    for payload in _script_json_payloads(soup):
        if isinstance(payload, Mapping) and isinstance(payload.get("variants"), list):
            result = parse_shopify_product_json(payload, base_url=base_url)
            if not result.title:
                result.title = title
            if not result.main_image_url and image:
                result.main_image_url = _absolute_url(image, base_url)
            result.raw["source"] = "shopify_embedded_json"
            return result
    result = ProductAnalysisResult(
        title=title,
        main_image_url=_absolute_url(image, base_url) if image else "",
        raw={"source": "html_meta", "link_type": detect_product_link_type(base_url)},
    )
    return result


def build_shopify_json_candidates(product_url: str) -> list[str]:
    parsed = urlparse(product_url)
    base = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
    candidates: list[str] = []
    if base.endswith(".json"):
        candidates.append(base)
    else:
        candidates.append(base + ".json")
    return candidates


def fetch_product_analysis(product_url: str, *, session: requests.Session | None = None) -> ProductAnalysisResult:
    http = session or requests.Session()
    link_type = detect_product_link_type(product_url)
    headers = dict(_PRODUCT_FETCH_HEADERS)
    if link_type == "shopify_product":
        for candidate in build_shopify_json_candidates(product_url):
            try:
                resp = http.get(candidate, headers=headers, timeout=25)
                if resp.ok and "json" in (resp.headers.get("content-type") or "").lower():
                    result = parse_shopify_product_json(resp.json(), base_url=product_url)
                    result.raw["link_type"] = link_type
                    return result
            except Exception:
                continue
    resp = http.get(product_url, headers=headers, timeout=25)
    resp.raise_for_status()
    result = parse_product_html(resp.text, base_url=resp.url or product_url)
    result.raw.setdefault("link_type", link_type)
    return result


_CATEGORY_BY_LOWER = {item.lower(): item for item in TIKTOK_SHOP_US_L1_CATEGORIES}


def _clean_category_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.lower().startswith("text"):
            text = text[4:]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = lines[0] if lines else text
    if ":" in text and text.split(":", 1)[0].strip().lower() in {"category", "category_l1", "类目"}:
        text = text.split(":", 1)[1].strip()
    return text.strip("`'\" .。；;，,")


def _strict_category(value: object) -> str | None:
    text = _clean_category_text(value)
    if not text:
        return None
    exact = _CATEGORY_BY_LOWER.get(text.lower())
    if exact:
        return exact
    lower = text.lower()
    matches = [item for item in TIKTOK_SHOP_US_L1_CATEGORIES if item.lower() in lower]
    if len(matches) == 1:
        return matches[0]
    return None


def build_category_prompt(*, product_title: str, product_url: str = "") -> str:
    category_pool = "\n".join(f"- {item}" for item in TIKTOK_SHOP_US_L1_CATEGORIES)
    return (
        "你是 TikTok Shop US 产品类目判断器。\n"
        "只允许从下面的 category_pool 中选择一个一级类目；如果无法判断，选择 Other。\n"
        "只返回一个类目名称，不要 JSON，不要 Markdown，不要解释。\n\n"
        f"category_pool:\n{category_pool}\n\n"
        f"product_title: {product_title}\n"
        "返回示例：Kitchenware"
    )


def normalize_category_response(response: Mapping[str, Any]) -> dict[str, Any]:
    payload: Any = response.get("json")
    if not isinstance(payload, Mapping):
        text = str(response.get("text") or "").strip()
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = {"category": text}
        else:
            payload = {}
    raw_category = str(payload.get("category") or "").strip()
    category = _strict_category(raw_category)
    try:
        confidence = float(payload.get("confidence") or (1.0 if category else 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if category is None:
        confidence = 0.0
    return {
        "category": category,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(payload.get("reason") or "").strip(),
        "raw_category": raw_category,
        "raw_response": dict(response),
    }


def categorize_product(
    *,
    product_title: str,
    product_url: str,
    user_id: int | None = None,
    invoke_fn=llm_client.invoke_generate,
) -> dict[str, Any]:
    response = invoke_fn(
        "meta_hot_posts.categorize",
        prompt=build_category_prompt(product_title=product_title, product_url=product_url),
        user_id=user_id,
        temperature=0,
        max_output_tokens=64,
        billing_extra={"source": "meta_hot_posts"},
    )
    result = normalize_category_response(response)
    result["provider"] = response.get("provider") or CATEGORY_PROVIDER
    result["model"] = response.get("model") or CATEGORY_MODEL
    return result
