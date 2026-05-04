"""Unit tests for appcore.supply_pairing.

Focus on extract_1688_url branches, which were extended on 2026-05-05 to
fall back to alibabaProductId when sourceUrl is not a 1688 link. This
matters because dianxiaomi auto-attaches an alibabaProductId to ~100% of
status=1 (waiting list) supply pairing items, and that ID can be turned
into a real https://detail.1688.com/offer/{id}.html link.
"""
from __future__ import annotations

import pytest

from appcore.supply_pairing import extract_1688_url


def test_returns_source_url_when_already_1688():
    item = {"sourceUrl": "https://detail.1688.com/offer/123.html?spm=foo"}
    assert (
        extract_1688_url(item)
        == "https://detail.1688.com/offer/123.html?spm=foo"
    )


def test_prefers_source_url_over_constructed_when_both_1688():
    # Even if alibabaProductId is set, the canonical sourceUrl (with spm
    # tracking params from the user's actual click) takes precedence.
    item = {
        "sourceUrl": "https://detail.1688.com/offer/123.html?spm=foo",
        "alibabaProductId": "999",
    }
    assert "spm=foo" in extract_1688_url(item)


def test_falls_back_to_alibaba_product_list_source_url():
    item = {
        "sourceUrl": None,
        "alibabaProductList": [
            {"sourceUrl": "https://detail.1688.com/offer/456.html"},
        ],
    }
    assert extract_1688_url(item) == "https://detail.1688.com/offer/456.html"


def test_constructs_from_alibaba_product_id_when_source_url_null():
    # The status=1 'waiting list' case: dxm has auto-matched a 1688 candidate
    # but the user has not pressed 'confirm pair', so sourceUrl is null.
    item = {
        "sourceUrl": None,
        "alibabaProductId": "597547488363",
    }
    assert (
        extract_1688_url(item)
        == "https://detail.1688.com/offer/597547488363.html"
    )


def test_constructs_when_source_url_is_amazon_but_alibaba_id_present():
    # User originally bought from Amazon, but dxm still attached a 1688
    # candidate via alibabaProductId. The 1688 link should win because the
    # field semantic is purchase_1688_url.
    item = {
        "sourceUrl": "https://www.amazon.com/dp/B0DFCRMNCZ?tag=foo",
        "alibabaProductId": "843803878006",
    }
    assert (
        extract_1688_url(item)
        == "https://detail.1688.com/offer/843803878006.html"
    )


def test_constructs_when_source_url_is_empty_string():
    item = {"sourceUrl": "", "alibabaProductId": "111"}
    assert extract_1688_url(item) == "https://detail.1688.com/offer/111.html"


@pytest.mark.parametrize("bad_id", ["", "0", "null", "None", "  "])
def test_ignores_placeholder_alibaba_product_ids(bad_id):
    item = {"sourceUrl": None, "alibabaProductId": bad_id}
    assert extract_1688_url(item) is None


def test_returns_non_1688_source_url_when_no_alibaba_id():
    # Last-resort: if there's a real Amazon/TikTok URL and no 1688 signal,
    # caller can still see the URL and decide what to do. backfill_1688_urls
    # filters this out separately via its own _is_1688() guard.
    item = {
        "sourceUrl": "https://www.amazon.com/dp/B0DFCRMNCZ",
        "alibabaProductId": None,
    }
    assert extract_1688_url(item) == "https://www.amazon.com/dp/B0DFCRMNCZ"


def test_returns_none_when_nothing_present():
    assert extract_1688_url({}) is None
    assert extract_1688_url({"sourceUrl": None, "alibabaProductId": None}) is None
    assert (
        extract_1688_url({"sourceUrl": None, "alibabaProductList": []}) is None
    )
