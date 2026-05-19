from __future__ import annotations


def test_compact_dry_run_counts_rows_without_writing():
    from tools import compact_dianxiaomi_ranking_assets as compact

    def fake_query(sql, args=()):
        assert "FROM dianxiaomi_rankings" in sql
        assert args in {(), (500,)}
        return [{"cnt": 143405}]

    def fail_execute(*_args, **_kwargs):
        raise AssertionError("dry-run must not write")

    summary = compact.run_compaction(
        dry_run=True,
        query_fn=fake_query,
        execute_fn=fail_execute,
    )

    assert summary == {
        "dry_run": True,
        "legacy_rows_with_asset_payload": 143405,
        "rank_limit": 500,
        "ranking_rows_over_limit": 143405,
        "ranking_rows_compacted": 0,
        "ranking_rows_pruned": 0,
        "optimized_table": False,
    }


def test_compact_clears_only_duplicate_asset_columns_and_keeps_product_code():
    from tools import compact_dianxiaomi_ranking_assets as compact

    executed = []

    def fake_query(_sql, _args=()):
        return [{"cnt": 12}]

    def fake_execute(sql, args=()):
        executed.append((sql, args))
        return 12

    summary = compact.run_compaction(
        dry_run=False,
        query_fn=fake_query,
        execute_fn=fake_execute,
    )

    sql, args = executed[0]
    assert "UPDATE dianxiaomi_rankings" in sql
    assert "product_main_image_url=NULL" in sql
    assert "product_detail_images_json=NULL" in sql
    assert "product_cn_name=NULL" in sql
    assert "product_code=NULL" not in sql
    assert "WHERE" in sql
    assert args == ()
    assert summary["ranking_rows_compacted"] == 12


def test_compact_can_optimize_table_after_clearing_payload():
    from tools import compact_dianxiaomi_ranking_assets as compact

    executed = []

    summary = compact.run_compaction(
        dry_run=False,
        optimize_table=True,
        query_fn=lambda _sql, _args=(): [{"cnt": 1}],
        execute_fn=lambda sql, args=(): executed.append((sql, args)) or 1,
    )

    assert any("OPTIMIZE TABLE dianxiaomi_rankings" in sql for sql, _args in executed)
    assert summary["optimized_table"] is True


def test_compact_can_prune_historical_rankings_over_top500():
    from tools import compact_dianxiaomi_ranking_assets as compact

    executed = []

    def fake_query(sql, args=()):
        if "rank_position > %s" in sql:
            assert args == (500,)
            return [{"cnt": 99}]
        return [{"cnt": 0}]

    summary = compact.run_compaction(
        dry_run=False,
        prune_rankings=True,
        rank_limit=500,
        query_fn=fake_query,
        execute_fn=lambda sql, args=(): executed.append((sql, args)) or 99,
    )

    assert any("DELETE FROM dianxiaomi_rankings" in sql and "rank_position > %s" in sql for sql, _args in executed)
    assert summary["ranking_rows_over_limit"] == 99
    assert summary["ranking_rows_pruned"] == 99
