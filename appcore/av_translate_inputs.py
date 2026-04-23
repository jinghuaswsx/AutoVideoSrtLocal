from __future__ import annotations

import copy
from typing import Any


AV_TARGET_LANGUAGE_OPTIONS = [
    {"code": "en", "label": "英语 English", "name": "English"},
    {"code": "de", "label": "德语 Deutsch", "name": "German"},
    {"code": "fr", "label": "法语 Francais", "name": "French"},
    {"code": "ja", "label": "日语 Japanese", "name": "Japanese"},
    {"code": "es", "label": "西语 Espanol", "name": "Spanish"},
    {"code": "pt", "label": "葡语 Portugues", "name": "Portuguese"},
    {"code": "nl", "label": "荷兰语 Dutch", "name": "Dutch"},
    {"code": "sv", "label": "瑞典语 Swedish", "name": "Swedish"},
    {"code": "fi", "label": "芬兰语 Finnish", "name": "Finnish"},
]

AV_TARGET_MARKET_OPTIONS = [
    {"code": "US", "label": "US"},
    {"code": "UK", "label": "UK"},
    {"code": "AU", "label": "AU"},
    {"code": "CA", "label": "CA"},
    {"code": "SEA", "label": "SEA"},
    {"code": "JP", "label": "JP"},
    {"code": "NL", "label": "NL"},
    {"code": "SE", "label": "SE"},
    {"code": "FI", "label": "FI"},
    {"code": "OTHER", "label": "OTHER"},
]

AV_TARGET_LANGUAGE_NAME_MAP = {
    item["code"]: item["name"] for item in AV_TARGET_LANGUAGE_OPTIONS
}
AV_TARGET_LANGUAGE_CODES = set(AV_TARGET_LANGUAGE_NAME_MAP.keys())
AV_TARGET_MARKET_CODES = {
    item["code"] for item in AV_TARGET_MARKET_OPTIONS
}

DEFAULT_TARGET_LANGUAGE = "en"
DEFAULT_TARGET_MARKET = "US"

_OVERRIDE_KEYS = (
    "product_name",
    "brand",
    "selling_points",
    "price",
    "target_audience",
    "extra_info",
)


def build_default_av_translate_inputs() -> dict[str, Any]:
    return {
        "target_language": DEFAULT_TARGET_LANGUAGE,
        "target_language_name": AV_TARGET_LANGUAGE_NAME_MAP[DEFAULT_TARGET_LANGUAGE],
        "target_market": DEFAULT_TARGET_MARKET,
        "product_overrides": {
            "product_name": None,
            "brand": None,
            "selling_points": None,
            "price": None,
            "target_audience": None,
            "extra_info": None,
        },
    }


def _normalize_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_selling_points(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        items = [
            line.strip()
            for line in str(value).replace("\r\n", "\n").split("\n")
        ]
    cleaned = [item for item in items if item]
    return cleaned or None


def normalize_av_translate_inputs(
    raw: dict[str, Any] | None,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = build_default_av_translate_inputs()
    sources = [base, raw]

    for source in sources:
        if not isinstance(source, dict):
            continue
        target_language = str(source.get("target_language") or "").strip().lower()
        if target_language:
            result["target_language"] = target_language
        target_market = str(source.get("target_market") or "").strip().upper()
        if target_market:
            result["target_market"] = target_market
        target_language_name = str(source.get("target_language_name") or "").strip()
        if target_language_name:
            result["target_language_name"] = target_language_name

        overrides = source.get("product_overrides") or {}
        if isinstance(overrides, dict):
            for key in _OVERRIDE_KEYS:
                if key not in overrides:
                    continue
                if key == "selling_points":
                    result["product_overrides"][key] = _normalize_selling_points(
                        overrides.get(key)
                    )
                else:
                    result["product_overrides"][key] = _normalize_optional_text(
                        overrides.get(key)
                    )

    result["target_language_name"] = AV_TARGET_LANGUAGE_NAME_MAP.get(
        result["target_language"],
        result.get("target_language_name") or result["target_language"],
    )
    result["product_overrides"] = copy.deepcopy(result["product_overrides"])
    return result

