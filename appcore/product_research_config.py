"""Country configuration for single-product AI research.

8 target countries: DE, FR, IT, ES, NL, PT, SE, JP.
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
    },
    {
        "country_code": "FR",
        "country_name": "France",
        "country_name_zh": "法国",
        "language": "French",
        "language_zh": "法语",
        "currency": "EUR",
        "marketplaces": ["Amazon.fr", "Cdiscount", "eBay.fr"],
    },
    {
        "country_code": "IT",
        "country_name": "Italy",
        "country_name_zh": "意大利",
        "language": "Italian",
        "language_zh": "意大利语",
        "currency": "EUR",
        "marketplaces": ["Amazon.it", "eBay.it"],
    },
    {
        "country_code": "ES",
        "country_name": "Spain",
        "country_name_zh": "西班牙",
        "language": "Spanish",
        "language_zh": "西班牙语",
        "currency": "EUR",
        "marketplaces": ["Amazon.es", "eBay.es", "AliExpress Spain"],
    },
    {
        "country_code": "NL",
        "country_name": "Netherlands",
        "country_name_zh": "荷兰",
        "language": "Dutch",
        "language_zh": "荷兰语",
        "currency": "EUR",
        "marketplaces": ["Amazon.nl", "bol.com", "Coolblue"],
    },
    {
        "country_code": "PT",
        "country_name": "Portugal",
        "country_name_zh": "葡萄牙",
        "language": "Portuguese",
        "language_zh": "葡萄牙语",
        "currency": "EUR",
        "marketplaces": ["Amazon.es", "Worten", "Fnac Portugal", "KuantoKusta"],
    },
    {
        "country_code": "SE",
        "country_name": "Sweden",
        "country_name_zh": "瑞典",
        "language": "Swedish",
        "language_zh": "瑞典语",
        "currency": "SEK",
        "marketplaces": ["Amazon.se", "CDON", "Prisjakt"],
    },
    {
        "country_code": "JP",
        "country_name": "Japan",
        "country_name_zh": "日本",
        "language": "Japanese",
        "language_zh": "日语",
        "currency": "JPY",
        "marketplaces": ["Amazon.co.jp", "楽天市場", "Yahoo!ショッピング"],
    },
)

DEFAULT_COUNTRY_CODES: tuple[str, ...] = tuple(c["country_code"] for c in COUNTRIES)
_COUNTRY_BY_CODE: dict[str, dict[str, Any]] = {c["country_code"]: c for c in COUNTRIES}

SCORE_WEIGHTS: dict[str, float] = {
    "product_market_fit_score": 0.25,
    "video_selling_fit_score": 0.25,
    "pricing_score": 0.20,
    "shipping_strategy_score": 0.10,
    "landing_page_localization_score": 0.10,
    "operational_fit_score": 0.05,
    "risk_score": 0.05,
}

DECISION_THRESHOLDS: dict[str, int] = {
    "GO": 75,
    "TEST": 60,
}

PIPELINE_STEPS: tuple[dict[str, str], ...] = (
    {"card_id": "input_validation", "title": "输入校验", "subtitle": "校验必填字段与数据完整性"},
    {"card_id": "product_facts", "title": "产品事实抽取", "subtitle": "AI 抽取产品属性、卖点、关键词"},
    {"card_id": "media_understanding", "title": "主图与短视频分析", "subtitle": "AI 分析视觉素材与带货适配度"},
    {"card_id": "country_DE", "title": "德国市场调研", "subtitle": "竞品价格、市场适配、短视频带货适配、定价策略"},
    {"card_id": "country_FR", "title": "法国市场调研", "subtitle": "竞品价格、市场适配、短视频带货适配、定价策略"},
    {"card_id": "country_IT", "title": "意大利市场调研", "subtitle": "竞品价格、市场适配、短视频带货适配、定价策略"},
    {"card_id": "country_ES", "title": "西班牙市场调研", "subtitle": "竞品价格、市场适配、短视频带货适配、定价策略"},
    {"card_id": "country_NL", "title": "荷兰市场调研", "subtitle": "竞品价格、市场适配、短视频带货适配、定价策略"},
    {"card_id": "country_PT", "title": "葡萄牙市场调研", "subtitle": "竞品价格、市场适配、短视频带货适配、定价策略"},
    {"card_id": "country_SE", "title": "瑞典市场调研", "subtitle": "竞品价格、市场适配、短视频带货适配、定价策略"},
    {"card_id": "country_JP", "title": "日本市场调研", "subtitle": "竞品价格、市场适配、短视频带货适配、定价策略"},
    {"card_id": "pricing_strategy", "title": "定价与运费策略", "subtitle": "8 国定价聚合与运费策略推荐"},
    {"card_id": "final_conclusion", "title": "最终结论", "subtitle": "8 国排名、GO/TEST/HOLD、下一步动作"},
)


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


def compute_overall_score(scores: dict[str, int]) -> int:
    if not scores:
        return 0
    weighted = 0.0
    total_weight = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        if key in scores:
            weighted += scores[key] * weight
            total_weight += weight
    if total_weight == 0:
        return 0
    return int(round(weighted / total_weight))


def decision_from_score(overall_score: int, blocking_issues: list[str] | None = None) -> str:
    if blocking_issues:
        return "HOLD"
    if overall_score >= DECISION_THRESHOLDS["GO"]:
        return "GO"
    if overall_score >= DECISION_THRESHOLDS["TEST"]:
        return "TEST"
    return "HOLD"