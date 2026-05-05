from __future__ import annotations


def test_parse_archived_filter_defaults_to_unarchived_and_all_disables_filter():
    from web.services.openapi_materials_listing import parse_archived_filter

    assert parse_archived_filter("") == 0
    assert parse_archived_filter("0") == 0
    assert parse_archived_filter("1") == 1
    assert parse_archived_filter("all") is None
    assert parse_archived_filter("unexpected") == 0


def test_batch_cover_langs_groups_only_rows_with_object_keys():
    from web.services.openapi_materials_listing import batch_cover_langs

    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [
            {"product_id": 1, "lang": "en", "object_key": "cover-en"},
            {"product_id": 1, "lang": "de", "object_key": "cover-de"},
            {"product_id": 2, "lang": "fr", "object_key": ""},
        ]

    result = batch_cover_langs([1, 2], query_fn=fake_query)

    assert result == {1: ["en", "de"]}
    assert calls[0][1] == (1, 2)
    assert "media_product_covers" in calls[0][0]


def test_batch_copywriting_langs_groups_languages_with_english_default():
    from web.services.openapi_materials_listing import batch_copywriting_langs

    result = batch_copywriting_langs(
        [1, 2],
        query_fn=lambda sql, args: [
            {"product_id": 1, "lang": "de"},
            {"product_id": 1, "lang": ""},
            {"product_id": 2, "lang": "fr"},
        ],
    )

    assert result == {1: ["de", "en"], 2: ["fr"]}


def test_batch_item_lang_counts_returns_per_language_and_totals():
    from web.services.openapi_materials_listing import batch_item_lang_counts

    per_lang, totals = batch_item_lang_counts(
        [1, 2],
        query_fn=lambda sql, args: [
            {"product_id": 1, "lang": "en", "c": 3},
            {"product_id": 1, "lang": "de", "c": 1},
            {"product_id": 2, "lang": "", "c": 2},
        ],
    )

    assert per_lang == {1: {"en": 3, "de": 1}, 2: {"en": 2}}
    assert totals == {1: 4, 2: 2}


def test_batch_helpers_skip_query_for_empty_product_ids():
    from web.services.openapi_materials_listing import (
        batch_copywriting_langs,
        batch_cover_langs,
        batch_item_lang_counts,
    )

    def fail_query(sql, args):
        raise AssertionError("query should not run")

    assert batch_cover_langs([], query_fn=fail_query) == {}
    assert batch_copywriting_langs([], query_fn=fail_query) == {}
    assert batch_item_lang_counts([], query_fn=fail_query) == ({}, {})
