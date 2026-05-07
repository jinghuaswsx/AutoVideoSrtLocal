from __future__ import annotations

import csv

from tools import backfill_1688_urls as backfill


def test_product_keywords_filter_generic_words_and_ascii_ngrams():
    mixed = backfill._product_keywords("super新水枪")

    assert "super" in mixed
    assert "水枪" in mixed
    assert "su" not in mixed
    assert "pe" not in mixed

    generic = backfill._product_keywords("多功能汽车防滑垫")

    assert "多功能" not in generic
    assert "汽车" not in generic
    assert "防滑垫" in generic


def test_is_1688_url_requires_real_1688_domain():
    assert backfill.is_1688_url("https://detail.1688.com/offer/123.html")
    assert backfill.is_1688_url("https://www.1688.com/")
    assert not backfill.is_1688_url("https://not1688.com/offer/123.html")
    assert not backfill.is_1688_url("https://amazon.com/dp/1688.com")
    assert not backfill.is_1688_url("")


def test_build_match_candidates_auto_applies_exact_sku_only():
    products = [{"id": 10, "name": "水枪", "skus": {"SKU-1"}}]
    paired_items = [
        {
            "id": "p1",
            "sku": "SKU-1",
            "skuCode": "",
            "name": "全自动水枪",
            "alibabaProductId": "111",
        }
    ]

    candidates = backfill.build_match_candidates(products, paired_items)

    assert len(candidates) == 1
    assert candidates[0]["pid"] == 10
    assert candidates[0]["method"] == "exact_sku=SKU-1"
    assert candidates[0]["confidence"] == "high"
    assert candidates[0]["auto_apply"] is True
    assert candidates[0]["url"] == "https://detail.1688.com/offer/111.html"


def test_build_match_candidates_filters_generic_keyword_matches():
    products = [
        {"id": 1, "name": "多功能汽车防滑垫", "skus": set()},
        {"id": 2, "name": "皮革修复补丁", "skus": set()},
    ]
    paired_items = [
        {
            "id": "generic",
            "sku": "",
            "skuCode": "",
            "name": "多功能美工刀",
            "alibabaProductId": "111",
        },
        {
            "id": "real",
            "sku": "",
            "skuCode": "",
            "name": "皮革补丁",
            "alibabaProductId": "222",
        },
    ]

    candidates = backfill.build_match_candidates(products, paired_items)

    assert [c["pid"] for c in candidates] == [2]
    assert candidates[0]["method"] == "keyword=皮革"
    assert candidates[0]["confidence"] == "review"
    assert candidates[0]["auto_apply"] is False
    assert candidates[0]["url"] == "https://detail.1688.com/offer/222.html"


def test_export_candidates_csv_includes_review_columns(tmp_path):
    path = tmp_path / "candidates.csv"
    candidates = [
        {
            "pid": 2,
            "name": "皮革修复补丁",
            "url": "https://detail.1688.com/offer/222.html",
            "method": "keyword=皮革",
            "priority": 2,
            "confidence": "review",
            "auto_apply": False,
            "paired_id": "real",
            "paired_name": "皮革补丁",
            "matched_keyword": "皮革",
        }
    ]

    backfill.export_candidates_csv(candidates, path)

    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [
        {
            "product_id": "2",
            "product_name": "皮革修复补丁",
            "candidate_url": "https://detail.1688.com/offer/222.html",
            "match_method": "keyword=皮革",
            "confidence": "review",
            "auto_apply": "false",
            "paired_id": "real",
            "paired_name": "皮革补丁",
            "matched_keyword": "皮革",
            "reviewed_url": "",
            "review_note": "",
        }
    ]


def test_import_reviewed_csv_updates_only_valid_1688_urls(tmp_path):
    path = tmp_path / "reviewed.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["product_id", "reviewed_url"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "product_id": "10",
                "reviewed_url": "https://detail.1688.com/offer/111.html",
            }
        )
        writer.writerow({"product_id": "11", "reviewed_url": "https://not1688.com/x"})
        writer.writerow({"product_id": "12", "reviewed_url": ""})

    updates = []

    def fake_execute(sql, args):
        updates.append((sql, args))
        return 1

    summary = backfill.import_reviewed_csv(path, execute_fn=fake_execute)

    assert summary == {
        "rows": 3,
        "updated": 1,
        "skipped_blank": 1,
        "skipped_invalid": 1,
    }
    assert len(updates) == 1
    assert "purchase_1688_url = %s" in updates[0][0]
    assert "purchase_1688_url IS NULL OR purchase_1688_url = ''" in updates[0][0]
    assert updates[0][1] == ("https://detail.1688.com/offer/111.html", 10)
