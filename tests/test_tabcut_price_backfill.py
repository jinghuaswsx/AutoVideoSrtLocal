import json

from tools import tabcut_price_backfill


def test_resolve_candidate_price_fields_prefers_video_raw_item_price():
    fields = tabcut_price_backfill.resolve_candidate_price_fields(
        candidate_json=json.dumps(
            {
                "video": {
                    "raw": {
                        "itemList": [
                            {
                                "itemId": "i1",
                                "skuPrice": "$12.34",
                                "currencySymbol": "$",
                            }
                        ]
                    }
                },
                "goods": {"price_min": 9.99, "price_max": 10.99},
            }
        ),
        video_raw_json=None,
        goods_price_min=None,
        goods_price_max=None,
    )

    assert fields == {
        "primary_item_price_min": 12.34,
        "primary_item_price_max": 12.34,
        "price_currency": "$",
    }


def test_resolve_candidate_price_fields_falls_back_to_goods_snapshot():
    fields = tabcut_price_backfill.resolve_candidate_price_fields(
        candidate_json=json.dumps({"video": {}, "goods": {}}),
        video_raw_json=None,
        goods_price_min="7.50",
        goods_price_max="8.25",
    )

    assert fields["primary_item_price_min"] == 7.5
    assert fields["primary_item_price_max"] == 8.25


def test_backfill_candidate_prices_updates_missing_rows():
    calls = []

    def fake_query(sql, params=()):
        calls.append(("query", sql, params))
        return [
            {
                "id": 42,
                "candidate_json": json.dumps(
                    {
                        "video": {
                            "raw": {
                                "itemList": [
                                    {
                                        "skuPrice": "$12.34",
                                        "currencySymbol": "$",
                                    }
                                ]
                            }
                        }
                    }
                ),
                "video_raw_json": None,
                "goods_price_min": None,
                "goods_price_max": None,
            }
        ]

    def fake_execute(sql, params=()):
        calls.append(("execute", sql, params))
        return 1

    summary = tabcut_price_backfill.backfill_candidate_prices(
        batch_size=100,
        limit=100,
        dry_run=False,
        query_fn=fake_query,
        execute_fn=fake_execute,
    )

    execute_calls = [call for call in calls if call[0] == "execute"]
    assert summary == {"scanned": 1, "updated": 1, "skipped": 0, "dry_run": False}
    assert "UPDATE tabcut_video_candidates" in execute_calls[0][1]
    assert execute_calls[0][2] == [12.34, 12.34, "$", 42]
