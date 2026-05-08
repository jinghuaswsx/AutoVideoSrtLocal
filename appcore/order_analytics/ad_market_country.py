"""Meta 广告命名中的市场国家解析。

该模块只解析运营命名里的市场标签，不代表 Meta API country breakdown。
"""
from __future__ import annotations

import re


MULTI_MARKET = "MULTI"

_CHINESE_COUNTRY_LABELS: tuple[tuple[str, str], ...] = (
    ("澳大利亚", "AU"),
    ("新西兰", "NZ"),
    ("西班牙", "ES"),
    ("意大利", "IT"),
    ("葡萄牙", "PT"),
    ("加拿大", "CA"),
    ("英国", "GB"),
    ("美国", "US"),
    ("法国", "FR"),
    ("德国", "DE"),
    ("日本", "JP"),
    ("荷兰", "NL"),
    ("澳洲", "AU"),
)

_MULTI_MARKET_LABELS: tuple[str, ...] = (
    "16国",
    "多国",
    "欧洲",
    "澳新",
    "E5",
)

_COUNTRY_CODE_ALIASES: dict[str, str] = {
    "US": "US",
    "USA": "US",
    "UK": "GB",
    "GB": "GB",
    "FR": "FR",
    "DE": "DE",
    "ES": "ES",
    "IT": "IT",
    "JP": "JP",
    "PT": "PT",
    "NL": "NL",
    "CA": "CA",
    "AU": "AU",
    "NZ": "NZ",
}

_CODE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (
        re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", re.IGNORECASE),
        code,
    )
    for alias, code in sorted(_COUNTRY_CODE_ALIASES.items(), key=lambda item: -len(item[0]))
)


def normalize_market_country(country: str | None) -> str | None:
    """归一化筛选参数中的国家代码；空 / all 返回 None。"""
    value = str(country or "").strip().upper()
    if not value or value == "ALL":
        return None
    return _COUNTRY_CODE_ALIASES.get(value, value)


def is_single_market_country(country: str | None) -> bool:
    """是否为可参与单国家过滤的 ISO-like 国家代码。"""
    value = normalize_market_country(country)
    return bool(value and value != MULTI_MARKET and re.fullmatch(r"[A-Z]{2}", value))


def extract_market_country(name: str | None) -> str | None:
    """从单个广告层级名称中解析市场国家代码。

    返回两位国家码、``MULTI`` 或 None。优先解析明确单国家标签，再识别多市场标签。
    """
    text = str(name or "").strip()
    if not text:
        return None

    for label, code in _CHINESE_COUNTRY_LABELS:
        if label in text:
            return code

    for pattern, code in _CODE_PATTERNS:
        if pattern.search(text):
            return code

    for label in _MULTI_MARKET_LABELS:
        if label in text:
            return MULTI_MARKET
    return None


def extract_market_country_from_names(
    *,
    ad_name: str | None = None,
    adset_name: str | None = None,
    campaign_name: str | None = None,
) -> str | None:
    """按 ad → adset → campaign 优先级解析市场国家。"""
    for value in (ad_name, adset_name, campaign_name):
        country = extract_market_country(value)
        if country:
            return country
    return None
