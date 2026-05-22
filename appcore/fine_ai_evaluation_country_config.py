"""Country configuration for single-product fine AI evaluation.

Docs-anchor:
docs/superpowers/specs/2026-05-22-single-product-five-country-ai-evaluation-design.md
"""

from __future__ import annotations

from typing import Any


COUNTRIES: tuple[dict[str, Any], ...] = (
    {
        "country_code": "DE",
        "country_name": "Germany",
        "country_name_zh": "德国",
        "language": "German",
        "language_zh": "德语",
        "currency": "EUR",
        "marketplaces": ["Amazon.de", "OTTO", "eBay.de", "Kaufland.de"],
        "generic_query_terms": ["buy online", "best seller", "Amazon.de", "price", "review"],
    },
    {
        "country_code": "FR",
        "country_name": "France",
        "country_name_zh": "法国",
        "language": "French",
        "language_zh": "法语",
        "currency": "EUR",
        "marketplaces": ["Amazon.fr", "Cdiscount", "eBay.fr"],
        "generic_query_terms": ["acheter en ligne", "meilleures ventes", "Amazon.fr", "prix", "avis"],
    },
    {
        "country_code": "IT",
        "country_name": "Italy",
        "country_name_zh": "意大利",
        "language": "Italian",
        "language_zh": "意大利语",
        "currency": "EUR",
        "marketplaces": ["Amazon.it", "eBay.it"],
        "generic_query_terms": ["comprare online", "più venduti", "Amazon.it", "prezzo", "recensioni"],
    },
    {
        "country_code": "ES",
        "country_name": "Spain",
        "country_name_zh": "西班牙",
        "language": "Spanish",
        "language_zh": "西班牙语",
        "currency": "EUR",
        "marketplaces": ["Amazon.es", "eBay.es", "AliExpress Spain"],
        "generic_query_terms": ["comprar online", "más vendidos", "Amazon.es", "precio", "reseñas"],
    },
    {
        "country_code": "JP",
        "country_name": "Japan",
        "country_name_zh": "日本",
        "language": "Japanese",
        "language_zh": "日语",
        "currency": "JPY",
        "marketplaces": ["Amazon.co.jp", "楽天市場", "Yahoo!ショッピング"],
        "generic_query_terms": ["通販", "売れ筋", "Amazon.co.jp", "価格", "レビュー"],
    },
)

DEFAULT_COUNTRY_CODES: tuple[str, ...] = tuple(country["country_code"] for country in COUNTRIES)
_COUNTRY_BY_CODE = {country["country_code"]: country for country in COUNTRIES}


def get_country_config(country_code: str) -> dict[str, Any]:
    code = str(country_code or "").strip().upper()
    if code not in _COUNTRY_BY_CODE:
        raise ValueError(f"unsupported country_code: {country_code}")
    return dict(_COUNTRY_BY_CODE[code])


def normalize_country_codes(countries: list[str] | tuple[str, ...] | None = None) -> list[str]:
    requested = list(countries or DEFAULT_COUNTRY_CODES)
    normalized: list[str] = []
    for raw in requested:
        code = str(raw or "").strip().upper()
        if not code:
            continue
        if code not in _COUNTRY_BY_CODE:
            raise ValueError(f"unsupported country_code: {raw}")
        if code not in normalized:
            normalized.append(code)
    return normalized or list(DEFAULT_COUNTRY_CODES)


def country_configs(countries: list[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    return [get_country_config(code) for code in normalize_country_codes(countries)]
