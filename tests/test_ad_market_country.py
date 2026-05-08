"""Meta 广告名称市场国家解析测试。"""
from __future__ import annotations

from appcore.order_analytics.ad_market_country import (
    extract_market_country,
    extract_market_country_from_names,
    is_single_market_country,
)


def test_extracts_chinese_country_labels_from_ad_names():
    assert extract_market_country("glow-go-insect-set-rjc 新素材 法国(04.16)-AP-AA") == "FR"
    assert extract_market_country("fully-automatic-water-blaster-rjc 德国 05.07") == "DE"
    assert extract_market_country("sonic-lens-refresher-rjc 美国-素材") == "US"


def test_extracts_uppercase_iso_tokens_with_boundaries():
    assert extract_market_country("Glow Set - DE - winners") == "DE"
    assert extract_market_country("ARP9_US_scale") == "US"
    assert extract_market_country("focus-product-rjc") is None


def test_multimarket_labels_do_not_count_as_single_country():
    assert extract_market_country("glow-go-insect-set-rjc E5 test") == "MULTI"
    assert extract_market_country("glow-go-insect-set-rjc 澳新 04.16") == "MULTI"
    assert is_single_market_country("MULTI") is False
    assert is_single_market_country("FR") is True


def test_ad_name_wins_over_adset_and_campaign_fallbacks():
    assert extract_market_country_from_names(
        ad_name="素材 法国",
        adset_name="广告组 德国",
        campaign_name="Campaign 美国",
    ) == "FR"
    assert extract_market_country_from_names(
        ad_name="",
        adset_name="广告组 德国",
        campaign_name="Campaign 美国",
    ) == "DE"
    assert extract_market_country_from_names(
        ad_name=None,
        adset_name=None,
        campaign_name="Campaign 美国",
    ) == "US"
