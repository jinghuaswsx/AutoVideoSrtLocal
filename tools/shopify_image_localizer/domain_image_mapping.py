from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


_TOKEN_RE = re.compile(r"([a-f0-9]{28,})", re.I)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I | re.S)
_IMG_ATTR_RE = re.compile(r"\b(src|alt)\s*=\s*(['\"])(.*?)\2", re.I | re.S)


def md5_token(value: str | None) -> str:
    match = _TOKEN_RE.search(str(value or "").lower())
    return match.group(1).lower() if match else ""


def _normalize_src(src: str | None) -> str:
    value = str(src or "").strip()
    if value.startswith("//"):
        return f"https:{value}"
    return value


def source_name_key(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = urlparse(raw).path if "://" in raw or raw.startswith("//") else raw.split("?", 1)[0]
    name = Path(unquote(path)).name
    if not name:
        return ""
    stem = Path(name).stem.strip().lower()
    return f"name:{stem}" if stem else ""


def _image_basename(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = urlparse(raw).path if "://" in raw or raw.startswith("//") else raw.split("?", 1)[0]
    return Path(unquote(path)).name.lower()


def product_image_sources(product: dict[str, Any] | None) -> list[str]:
    images = (product or {}).get("images") or []
    srcs: list[str] = []
    for image in images:
        if isinstance(image, str):
            src = image
        elif isinstance(image, dict):
            src = image.get("src") or image.get("url") or ""
        else:
            src = ""
        src = _normalize_src(src)
        if src:
            srcs.append(src)
    return srcs


def extract_detail_srcs(product: dict[str, Any] | None) -> list[str]:
    html = str((product or {}).get("description") or (product or {}).get("body_html") or "")
    srcs: list[str] = []
    for tag_match in _IMG_TAG_RE.finditer(html):
        attrs: dict[str, str] = {}
        for attr_match in _IMG_ATTR_RE.finditer(tag_match.group(0)):
            attrs[attr_match.group(1).lower()] = attr_match.group(3)
        src = _normalize_src(attrs.get("src") or "")
        if src and not src.lower().split("?", 1)[0].endswith(".gif"):
            srcs.append(src)
    return srcs


@dataclass(frozen=True)
class ImageAlias:
    target_index: int
    canonical_index: int
    target_src: str
    canonical_src: str
    match_method: str
    confidence: str
    target_token: str = ""
    canonical_token: str = ""
    target_name_key: str = ""
    canonical_name_key: str = ""


@dataclass
class DomainImageMapping:
    target_domain: str = ""
    canonical_domain: str = ""
    carousel_aliases: list[ImageAlias] = field(default_factory=list)
    detail_aliases: list[ImageAlias] = field(default_factory=list)
    carousel_source_index_by_key: dict[str, int] = field(default_factory=dict)
    carousel_canonical_token_by_key: dict[str, str] = field(default_factory=dict)
    detail_source_index_by_key: dict[str, int] = field(default_factory=dict)
    detail_canonical_token_by_key: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.carousel_aliases and not self.detail_aliases

    def carousel_source_index_for(self, src: str, slot_index: int | None = None) -> int | None:
        for key in _source_keys(src):
            if key in self.carousel_source_index_by_key:
                return self.carousel_source_index_by_key[key]
        if slot_index is None:
            return None
        for alias in self.carousel_aliases:
            if alias.target_index == slot_index:
                return alias.canonical_index
        return None

    def carousel_canonical_token_for(self, src: str) -> str:
        for key in _source_keys(src):
            token = self.carousel_canonical_token_by_key.get(key)
            if token:
                return token
        return ""


def _source_keys(src: str | None) -> list[str]:
    token = md5_token(src)
    name_key = source_name_key(src)
    return [key for key in (token, name_key, _normalize_src(src).lower()) if key]


def _alias_for_pair(
    *,
    target_index: int,
    canonical_index: int,
    target_src: str,
    canonical_src: str,
) -> ImageAlias:
    target_name = _image_basename(target_src)
    canonical_name = _image_basename(canonical_src)
    same_name = bool(target_name and canonical_name and target_name == canonical_name)
    return ImageAlias(
        target_index=target_index,
        canonical_index=canonical_index,
        target_src=target_src,
        canonical_src=canonical_src,
        match_method="position+filename" if same_name else "position",
        confidence="high" if same_name else "medium",
        target_token=md5_token(target_src),
        canonical_token=md5_token(canonical_src),
        target_name_key=source_name_key(target_src),
        canonical_name_key=source_name_key(canonical_src),
    )


def _register_alias(
    *,
    alias: ImageAlias,
    source_index_by_key: dict[str, int],
    canonical_token_by_key: dict[str, str],
) -> None:
    for key in _source_keys(alias.target_src):
        source_index_by_key[key] = alias.canonical_index
        if alias.canonical_token:
            canonical_token_by_key[key] = alias.canonical_token


def build_domain_image_mapping(
    *,
    canonical_product: dict[str, Any] | None,
    target_product: dict[str, Any] | None,
    canonical_detail_product: dict[str, Any] | None = None,
    target_detail_product: dict[str, Any] | None = None,
    canonical_domain: str = "",
    target_domain: str = "",
) -> DomainImageMapping:
    canonical_carousel = product_image_sources(canonical_product)
    target_carousel = product_image_sources(target_product)
    canonical_detail = extract_detail_srcs(canonical_detail_product or canonical_product)
    target_detail = extract_detail_srcs(target_detail_product or target_product)
    mapping = DomainImageMapping(
        target_domain=target_domain,
        canonical_domain=canonical_domain,
    )

    for idx, target_src in enumerate(target_carousel):
        if idx >= len(canonical_carousel):
            continue
        alias = _alias_for_pair(
            target_index=idx,
            canonical_index=idx,
            target_src=target_src,
            canonical_src=canonical_carousel[idx],
        )
        mapping.carousel_aliases.append(alias)
        _register_alias(
            alias=alias,
            source_index_by_key=mapping.carousel_source_index_by_key,
            canonical_token_by_key=mapping.carousel_canonical_token_by_key,
        )

    carousel_count = len(canonical_carousel)
    for idx, target_src in enumerate(target_detail):
        if idx >= len(canonical_detail):
            continue
        alias = _alias_for_pair(
            target_index=idx,
            canonical_index=carousel_count + idx,
            target_src=target_src,
            canonical_src=canonical_detail[idx],
        )
        mapping.detail_aliases.append(alias)
        _register_alias(
            alias=alias,
            source_index_by_key=mapping.detail_source_index_by_key,
            canonical_token_by_key=mapping.detail_canonical_token_by_key,
        )

    return mapping


def summarize_domain_image_mapping(mapping: DomainImageMapping | None) -> dict[str, Any]:
    mapping = mapping or DomainImageMapping()
    carousel_low = [alias for alias in mapping.carousel_aliases if alias.confidence != "high"]
    detail_low = [alias for alias in mapping.detail_aliases if alias.confidence != "high"]
    return {
        "target_domain": mapping.target_domain,
        "canonical_domain": mapping.canonical_domain,
        "carousel_mapped_count": len(mapping.carousel_aliases),
        "detail_mapped_count": len(mapping.detail_aliases),
        "carousel_low_confidence_count": len(carousel_low),
        "detail_low_confidence_count": len(detail_low),
        "carousel_low_confidence": [_alias_payload(alias) for alias in carousel_low[:20]],
        "detail_low_confidence": [_alias_payload(alias) for alias in detail_low[:20]],
    }


def _alias_payload(alias: ImageAlias) -> dict[str, Any]:
    return {
        "target_index": alias.target_index,
        "canonical_index": alias.canonical_index,
        "target_src": alias.target_src,
        "canonical_src": alias.canonical_src,
        "match_method": alias.match_method,
        "confidence": alias.confidence,
    }

