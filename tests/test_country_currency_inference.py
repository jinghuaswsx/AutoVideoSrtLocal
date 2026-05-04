"""国家→presentment_currency 推断测试。

策略 C 用：店小秘 raw_order_json 没有 presentment_currency 字段，
按 buyerCountry 映射推断（多数客户使用本地货币结账）。
"""
from __future__ import annotations

import pytest

from appcore.order_analytics.shopify_fee import (
    COUNTRY_TO_CURRENCY,
    infer_presentment_currency_from_country,
)


@pytest.mark.parametrize("country", ["DE", "IT", "FR", "ES", "PT", "IE", "NL", "BE", "AT"])
def test_eurozone_countries_infer_eur(country):
    assert infer_presentment_currency_from_country(country) == "EUR"


def test_uk_infers_gbp():
    assert infer_presentment_currency_from_country("GB") == "GBP"


def test_us_infers_usd():
    assert infer_presentment_currency_from_country("US") == "USD"


@pytest.mark.parametrize("country,expected", [
    ("AU", "AUD"),
    ("CA", "CAD"),
    ("NZ", "NZD"),
    ("CH", "CHF"),
    ("SE", "SEK"),
    ("NO", "NOK"),
    ("DK", "DKK"),
    ("JP", "JPY"),
    ("MX", "MXN"),
])
def test_other_major_markets(country, expected):
    assert infer_presentment_currency_from_country(country) == expected


def test_lowercase_input_normalized():
    assert infer_presentment_currency_from_country("de") == "EUR"
    assert infer_presentment_currency_from_country("gb") == "GBP"


def test_unknown_country_falls_back_to_usd():
    """未在映射表里的国家保守 fallback 到 USD（店铺结算币种）。"""
    assert infer_presentment_currency_from_country("XX") == "USD"
    assert infer_presentment_currency_from_country("ZZ") == "USD"


def test_none_country_falls_back_to_usd():
    assert infer_presentment_currency_from_country(None) == "USD"


def test_empty_string_falls_back_to_usd():
    assert infer_presentment_currency_from_country("") == "USD"


def test_country_to_currency_mapping_contains_all_eurozone():
    """完整的欧元区国家清单覆盖（业务主战场，不能漏）。"""
    eurozone = {"AT", "BE", "DE", "ES", "FI", "FR", "IE", "IT", "LU", "NL",
                "PT", "GR", "MT", "CY", "EE", "LV", "LT", "SK", "SI", "HR"}
    for country in eurozone:
        assert COUNTRY_TO_CURRENCY.get(country) == "EUR", (
            f"欧元区国家 {country} 缺失或映射错误"
        )
