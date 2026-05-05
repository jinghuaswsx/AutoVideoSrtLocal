from __future__ import annotations


def test_build_mk_selection_response_handles_legacy_rankings_schema_without_mk_columns():
    from web.services.media_mk_selection import build_mk_selection_response

    calls: list[tuple[str, list]] = []

    def fake_ranking_columns():
        return {
            "id",
            "product_id",
            "product_name",
            "product_url",
            "store",
            "sales_count",
            "order_count",
            "revenue_main",
            "revenue_split",
            "media_product_id",
            "snapshot_date",
            "rank_position",
        }

    def fake_db_query(sql, args=()):
        calls.append((sql, list(args)))
        if "SELECT COUNT(*) AS cnt" in sql:
            assert "mk_product_name" not in sql
            assert args == ["2026-04-23", "%tooth%"]
            return [{"cnt": 0}]
        if "FROM dianxiaomi_rankings dr" in sql:
            assert "NULL AS mk_product_id" in sql
            assert "NULL AS mk_product_name" in sql
            assert "0 AS mk_total_spends" in sql
            assert "0 AS mk_video_count" in sql
            assert "0 AS mk_total_ads" in sql
            assert "ORDER BY dr.rank_position ASC" in sql
            assert args == ["2026-04-23", "%tooth%", 50, 0]
            return []
        raise AssertionError(sql)

    result = build_mk_selection_response(
        {"keyword": "tooth"},
        ranking_columns_fn=fake_ranking_columns,
        db_query_fn=fake_db_query,
    )

    assert result.status_code == 200
    assert result.payload == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 50,
    }
    assert len(calls) == 2


def test_build_mk_selection_response_rejects_invalid_pagination_without_db_query():
    from web.services.media_mk_selection import build_mk_selection_response

    def fail_ranking_columns():
        raise AssertionError("invalid pagination should stop before schema lookup")

    def fail_db_query(*_args, **_kwargs):
        raise AssertionError("invalid pagination should stop before db query")

    result = build_mk_selection_response(
        {"page": "bad"},
        ranking_columns_fn=fail_ranking_columns,
        db_query_fn=fail_db_query,
    )

    assert result.status_code == 400
    assert result.payload["error"] == "invalid_pagination"
