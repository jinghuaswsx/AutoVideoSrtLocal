from __future__ import annotations

import io
from types import SimpleNamespace

from appcore import order_analytics as oa


def test_parse_meta_ad_file_reads_required_fields():
    csv_text = (
        "报告开始日期,报告结束日期,广告系列名称,成效,成效指标,已花费金额 (USD),购物转化价值,"
        "广告花费回报 (ROAS) - 购物,CPM（千次展示费用） (USD),单次链接点击费用 - 独立用户 (USD),"
        "链接点击率,广告系列投放,链接点击量,加入购物车次数,结账发起次数,单次加入购物车费用 (USD),"
        "单次发起结账费用 (USD),单次成效费用,平均购物转化价值,展示次数,视频平均播放时长\n"
        "2026-04-01,2026-04-22,Glow-Go-Insect-Set-RJC,787,actions:offsite_conversion.fb_pixel_purchase,"
        "19377.19,34829.05,1.797425,34.111528,1.729334,2.483743,active,14109,1725,"
        "1338,11.233154,14.482205,24.62158831,44.255464,568054,5\n"
    )

    rows = oa.parse_meta_ad_file(io.BytesIO(csv_text.encode("utf-8")), "meta.csv")

    assert len(rows) == 1
    assert rows[0]["campaign_name"] == "Glow-Go-Insect-Set-RJC"
    assert rows[0]["normalized_campaign_code"] == "glow-go-insect-set-rjc"
    assert rows[0]["report_start_date"].isoformat() == "2026-04-01"
    assert rows[0]["report_end_date"].isoformat() == "2026-04-22"
    assert rows[0]["spend_usd"] == 19377.19
    assert rows[0]["purchase_value_usd"] == 34829.05
    assert rows[0]["result_count"] == 787
    assert rows[0]["link_clicks"] == 14109
    assert rows[0]["impressions"] == 568054


def test_parse_meta_ad_file_reports_missing_required_columns():
    csv_text = "报告开始日期,报告结束日期,成效\n2026-04-01,2026-04-22,1\n"

    try:
        oa.parse_meta_ad_file(io.BytesIO(csv_text.encode("utf-8")), "meta.csv")
    except ValueError as exc:
        assert "广告系列名称" in str(exc)
        assert "已花费金额 (USD)" in str(exc)
    else:
        raise AssertionError("expected missing-column ValueError")


def test_product_code_candidates_for_ad_campaign_cover_rjc_suffix_variants():
    assert oa.product_code_candidates_for_ad_campaign("Glow-Go-Insect-Set") == [
        "glow-go-insect-set",
        "glow-go-insect-set-rjc",
    ]
    assert oa.product_code_candidates_for_ad_campaign("Glow-Go-Insect-Set-RJC") == [
        "glow-go-insect-set-rjc",
        "glow-go-insect-set",
    ]


def test_resolve_ad_product_match_tries_suffix_variants(monkeypatch):
    calls = []

    def fake_query_one(sql, args=()):
        calls.append(args)
        if args == ("glow-go-insect-set-rjc",):
            return {"id": 42, "product_code": "glow-go-insect-set-rjc", "name": "Glow Set"}
        return None

    monkeypatch.setattr(oa, "query_one", fake_query_one)

    product = oa.resolve_ad_product_match("Glow-Go-Insect-Set")

    assert product["id"] == 42
    assert product["product_code"] == "glow-go-insect-set-rjc"
    assert calls == [("glow-go-insect-set",), ("glow-go-insect-set-rjc",)]


def test_match_meta_ads_to_products_updates_report_and_daily_tables(monkeypatch):
    queries = []
    updates = []

    def fake_query(sql, args=()):
        queries.append(sql)
        if "FROM meta_ad_campaign_metrics" in sql:
            return [{"id": 1, "campaign_name": "Glow-Go-Insect-Set"}]
        if "FROM meta_ad_daily_campaign_metrics" in sql:
            return [{"id": 2, "campaign_name": "Glow-Go-Insect-Set"}]
        raise AssertionError(sql)

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(
        oa,
        "resolve_ad_product_match",
        lambda campaign_name: {"id": 42, "product_code": "glow-go-insect-set-rjc"},
    )
    monkeypatch.setattr(
        oa,
        "execute",
        lambda sql, args=(): updates.append((sql, args)) or 1,
    )

    assert oa.match_meta_ads_to_products() == 2
    assert any("FROM meta_ad_campaign_metrics" in sql for sql in queries)
    assert any("FROM meta_ad_daily_campaign_metrics" in sql for sql in queries)
    assert any("UPDATE meta_ad_campaign_metrics" in sql for sql, _args in updates)
    assert any("UPDATE meta_ad_daily_campaign_metrics" in sql for sql, _args in updates)
    assert updates[0][1] == (42, "glow-go-insect-set-rjc", 1)
    assert updates[1][1] == (42, "glow-go-insect-set-rjc", 2)


def test_manual_match_meta_ad_campaign_updates_both_tables(monkeypatch):
    queried = []
    updates = []

    def fake_query_one(sql, args=()):
        queried.append((sql, args))
        return {"id": 42, "product_code": "glow-go-insect-set-rjc", "name": "Glow Set"}

    def fake_execute(sql, args=()):
        updates.append((sql, args))
        return 3 if "meta_ad_campaign_metrics" in sql else 7

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "execute", fake_execute)

    result = oa.manual_match_meta_ad_campaign("  Glow-Go-Insect-Set-RJC  ", 42)

    assert result == {
        "matched_periodic": 3,
        "matched_daily": 7,
        "product_id": 42,
        "product_code": "glow-go-insect-set-rjc",
        "product_name": "Glow Set",
    }
    assert queried[0][1] == (42,)
    assert "FROM media_products" in queried[0][0]
    assert "deleted_at IS NULL" in queried[0][0]
    # 整合后 source of truth 是 campaign_product_overrides 表：
    # 一次 INSERT override + 两次 UPDATE 事实表
    assert any("INSERT INTO campaign_product_overrides" in u[0] for u in updates)
    update_steps = [(s, a) for s, a in updates if s.lstrip().startswith("UPDATE")]
    update_sqls = [s for s, _ in update_steps]
    assert any("UPDATE meta_ad_campaign_metrics" in s for s in update_sqls)
    assert any("UPDATE meta_ad_daily_campaign_metrics" in s for s in update_sqls)
    for sql, args in update_steps:
        assert "product_id IS NULL" in sql
        assert args == (42, "glow-go-insect-set-rjc", "glow-go-insect-set-rjc")


def test_manual_match_meta_ad_campaign_rejects_blank_code():
    import pytest

    with pytest.raises(ValueError):
        oa.manual_match_meta_ad_campaign("   ", 42)


def test_manual_match_meta_ad_campaign_rejects_non_positive_product_id():
    import pytest

    with pytest.raises(ValueError):
        oa.manual_match_meta_ad_campaign("glow-go-insect-set", 0)
    with pytest.raises(ValueError):
        oa.manual_match_meta_ad_campaign("glow-go-insect-set", "abc")


def test_manual_match_meta_ad_campaign_raises_when_product_missing(monkeypatch):
    import pytest

    monkeypatch.setattr(oa, "query_one", lambda sql, args=(): None)
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): 0)

    with pytest.raises(LookupError):
        oa.manual_match_meta_ad_campaign("glow-go-insect-set", 999)


def test_import_meta_ad_rows_creates_batch_and_upserts_metrics(monkeypatch):
    rows = [
        {
            "report_start_date": oa._parse_meta_date("2026-04-01"),
            "report_end_date": oa._parse_meta_date("2026-04-22"),
            "campaign_name": "Glow-Go-Insect-Set",
            "normalized_campaign_code": "glow-go-insect-set",
            "result_count": 10,
            "result_metric": "actions:offsite_conversion.fb_pixel_purchase",
            "spend_usd": 120.5,
            "purchase_value_usd": 320.0,
            "roas_purchase": 2.655,
            "cpm_usd": 4.2,
            "unique_link_click_cost_usd": 1.2,
            "link_ctr": 2.3,
            "campaign_delivery": "active",
            "link_clicks": 100,
            "add_to_cart_count": 20,
            "initiate_checkout_count": 12,
            "add_to_cart_cost_usd": 6.0,
            "initiate_checkout_cost_usd": 10.0,
            "cost_per_result_usd": 12.05,
            "average_purchase_value_usd": 32.0,
            "impressions": 2000,
            "video_avg_play_time": 5.0,
            "raw": {"广告系列名称": "Glow-Go-Insect-Set"},
        }
    ]
    inserted = []

    monkeypatch.setattr(
        oa,
        "resolve_ad_product_match",
        lambda campaign_name: {
            "id": 42,
            "product_code": "glow-go-insect-set-rjc",
            "name": "Glow Set",
        },
    )

    class FakeCursor:
        lastrowid = 77

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=None):
            inserted.append((sql, args))
            if "INSERT INTO meta_ad_campaign_metrics" in sql:
                self.rowcount = 1
            else:
                self.rowcount = 1

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(oa, "get_conn", lambda: FakeConn())

    result = oa.import_meta_ad_rows(
        rows,
        filename="meta.csv",
        file_bytes=b"csv-bytes",
        import_frequency="weekly",
    )

    assert result["batch_id"] == 77
    assert result["imported"] == 1
    assert result["matched"] == 1
    assert any("INSERT INTO meta_ad_import_batches" in sql for sql, _args in inserted)
    metric_args = [args for sql, args in inserted if "INSERT INTO meta_ad_campaign_metrics" in sql][0]
    assert metric_args[0] == 77
    assert metric_args[6] == "glow-go-insect-set-rjc"
    assert metric_args[7] == 42


def test_get_meta_ad_summary_aggregates_metric_rows_in_python(monkeypatch):
    report_start = oa._parse_meta_date("2026-04-01")
    report_end = oa._parse_meta_date("2026-04-22")
    queries = []

    def fake_query_one(sql, args=()):
        assert "meta_ad_import_batches" in sql
        assert args == (9,)
        return {
            "id": 9,
            "report_start_date": report_start,
            "report_end_date": report_end,
        }

    def fake_query(sql, args=()):
        queries.append(sql)
        if "FROM meta_ad_campaign_metrics m" in sql:
            return [
                {
                    "product_id": 42,
                    "product_name": "Glow Set",
                    "media_product_code": "glow-set-rjc",
                    "matched_product_code": "glow-set-rjc",
                    "campaign_name": "Campaign B",
                    "result_count": 4,
                    "spend_usd": 5.0,
                    "purchase_value_usd": 10.0,
                    "link_clicks": 20,
                    "add_to_cart_count": 3,
                    "initiate_checkout_count": 2,
                    "impressions": 100,
                },
                {
                    "product_id": 42,
                    "product_name": "Glow Set",
                    "media_product_code": "glow-set-rjc",
                    "matched_product_code": "glow-set-rjc",
                    "campaign_name": "Campaign A",
                    "result_count": 6,
                    "spend_usd": 15.0,
                    "purchase_value_usd": 40.0,
                    "link_clicks": 30,
                    "add_to_cart_count": 4,
                    "initiate_checkout_count": 3,
                    "impressions": 200,
                },
                {
                    "product_id": None,
                    "product_name": None,
                    "media_product_code": None,
                    "matched_product_code": None,
                    "campaign_name": "Unmatched Campaign",
                    "result_count": 1,
                    "spend_usd": 3.0,
                    "purchase_value_usd": 0.0,
                    "link_clicks": 5,
                    "add_to_cart_count": 0,
                    "initiate_checkout_count": 0,
                    "impressions": 50,
                },
            ]
        if "FROM shopify_orders" in sql:
            return [
                {
                    "product_id": 42,
                    "shopify_order_count": 2,
                    "shopify_quantity": 3,
                    "shopify_revenue": 99.0,
                }
            ]
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        if "FROM meta_ad_campaign_metrics" in sql and "product_id IS NULL" in sql:
            return [
                {
                    "id": 7,
                    "campaign_name": "Unmatched Campaign",
                    "normalized_campaign_code": "unmatched-campaign",
                    "spend_usd": 3.0,
                    "result_count": 1,
                    "purchase_value_usd": 0.0,
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "query", fake_query)

    summary = oa.get_meta_ad_summary(batch_id=9)

    assert len(summary["rows"]) == 2
    product_row = summary["rows"][0]
    assert product_row["product_id"] == 42
    assert product_row["display_name"] == "Glow Set"
    assert product_row["product_code"] == "glow-set-rjc"
    assert product_row["campaign_count"] == 2
    assert product_row["campaign_names"] == "Campaign A, Campaign B"
    assert product_row["result_count"] == 10
    assert product_row["spend_usd"] == 20.0
    assert product_row["purchase_value_usd"] == 50.0
    assert product_row["roas_purchase"] == 2.5
    assert product_row["cost_per_result_usd"] == 2.0
    assert product_row["shopify_order_count"] == 2
    assert product_row["shopify_quantity"] == 3
    assert product_row["shopify_revenue"] == 99.0
    assert product_row["dianxiaomi_order_count"] == 0
    assert product_row["dianxiaomi_units"] == 0
    assert product_row["dianxiaomi_total_sales"] == 0
    assert product_row["dianxiaomi_roas"] == 0.0

    unmatched_row = summary["rows"][1]
    assert unmatched_row["product_id"] is None
    assert unmatched_row["display_name"] == "Unmatched Campaign"
    assert summary["unmatched"][0]["campaign_name"] == "Unmatched Campaign"
    assert all("GROUP BY m.product_id, display_name, product_code" not in sql for sql in queries)


def test_get_meta_ad_summary_uses_daily_metrics_for_explicit_range(monkeypatch):
    report_start = oa._parse_meta_date("2026-04-01")
    report_end = oa._parse_meta_date("2026-04-18")
    queries = []

    monkeypatch.setattr(
        oa,
        "query_one",
        lambda sql, args=(): (_ for _ in ()).throw(AssertionError("batch lookup should not run")),
    )

    def fake_query(sql, args=()):
        queries.append((sql, args))
        if "FROM meta_ad_daily_campaign_metrics m" in sql and "LEFT JOIN media_products" in sql:
            assert "m.meta_business_date >= %s" in sql
            assert "m.meta_business_date <= %s" in sql
            assert args == (report_start, report_end)
            return [
                {
                    "product_id": 42,
                    "product_name": "Glow Set",
                    "media_product_code": "glow-set-rjc",
                    "matched_product_code": "glow-set-rjc",
                    "campaign_name": "Campaign A",
                    "result_count": 6,
                    "spend_usd": 15.0,
                    "purchase_value_usd": 45.0,
                    "link_clicks": 0,
                    "add_to_cart_count": 0,
                    "initiate_checkout_count": 0,
                    "impressions": 0,
                }
            ]
        if "FROM shopify_orders" in sql:
            return []
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql and "product_id IS NULL" in sql:
            assert "meta_business_date >= %s" in sql
            assert "meta_business_date <= %s" in sql
            return [
                {
                    "id": 7,
                    "campaign_name": "Unmatched Campaign",
                    "normalized_campaign_code": "unmatched-campaign",
                    "spend_usd": 3.0,
                    "result_count": 1,
                    "purchase_value_usd": 0.0,
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(oa, "query", fake_query)

    summary = oa.get_meta_ad_summary(start_date="2026-04-01", end_date="2026-04-18")

    assert summary["period"]["batch_id"] is None
    assert summary["period"]["report_start_date"] == report_start
    assert summary["period"]["report_end_date"] == report_end
    assert summary["period"]["source"] == "meta_ad_daily_campaign_metrics"
    assert summary["rows"][0]["product_id"] == 42
    assert summary["rows"][0]["roas_purchase"] == 3.0
    assert summary["unmatched"][0]["campaign_name"] == "Unmatched Campaign"
    assert all("meta_ad_campaign_metrics" not in sql for sql, _args in queries)


def test_get_meta_ad_summary_filters_by_search_query(monkeypatch):
    report_start = oa._parse_meta_date("2026-04-01")
    report_end = oa._parse_meta_date("2026-05-03")
    queries = []

    monkeypatch.setattr(
        oa,
        "query_one",
        lambda sql, args=(): (_ for _ in ()).throw(AssertionError("batch lookup should not run")),
    )

    def fake_query(sql, args=()):
        queries.append((sql, args))
        if "FROM meta_ad_daily_campaign_metrics m" in sql and "LEFT JOIN media_products" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql and "product_id IS NULL" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(oa, "query", fake_query)

    summary = oa.get_meta_ad_summary(
        start_date="2026-04-01",
        end_date="2026-05-03",
        q="water-blaster",
    )

    assert summary["rows"] == []
    main_sql, main_args = queries[0]
    assert "LOWER(m.campaign_name) LIKE LOWER(%s)" in main_sql
    assert "LOWER(m.normalized_campaign_code) LIKE LOWER(%s)" in main_sql
    assert "LOWER(COALESCE(m.matched_product_code, m.product_code, '')) LIKE LOWER(%s)" in main_sql
    assert "LOWER(COALESCE(mp.name, '')) LIKE LOWER(%s)" in main_sql
    assert "LOWER(COALESCE(mp.product_code, '')) LIKE LOWER(%s)" in main_sql
    assert main_args == (
        report_start,
        report_end,
        "%water-blaster%",
        "%water-blaster%",
        "%water-blaster%",
        "%water-blaster%",
        "%water-blaster%",
    )


def test_get_meta_ad_summary_uses_realtime_for_open_business_day(monkeypatch):
    from appcore.order_analytics import meta_ads as meta_ads_mod

    today = oa._parse_meta_date("2026-05-18")
    snapshot_at = __import__("datetime").datetime(2026, 5, 18, 17, 20)
    queries = []

    monkeypatch.setattr(meta_ads_mod, "current_meta_business_date", lambda: today)

    def fake_match(code):
        if code == "glow-rjc":
            return {"id": 42, "product_code": "glow-rjc", "name": "Glow Product"}
        return None

    monkeypatch.setattr(oa, "resolve_ad_product_match", fake_match)

    def fake_query(sql, args=()):
        queries.append((sql, args))
        if "FROM meta_ad_daily_campaign_metrics m" in sql and "LEFT JOIN media_products" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql and "product_id IS NULL" in sql:
            return []
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql and "MAX(snapshot_at)" in sql:
            return [{"ad_account_id": "1861285821213497", "snapshot_at": snapshot_at}]
        if "FROM meta_ad_realtime_daily_campaign_metrics" in sql and "snapshot_at=%s" in sql:
            return [
                {
                    "id": 1001,
                    "ad_account_id": "1861285821213497",
                    "ad_account_name": "Newjoyloo",
                    "campaign_name": "Glow RJC",
                    "normalized_campaign_code": "glow-rjc",
                    "result_count": 3,
                    "spend_usd": 50.0,
                    "purchase_value_usd": 80.0,
                    "impressions": 1000,
                    "clicks": 10,
                },
                {
                    "id": 1002,
                    "ad_account_id": "1861285821213497",
                    "ad_account_name": "Newjoyloo",
                    "campaign_name": "Unknown Campaign",
                    "normalized_campaign_code": "unknown-campaign",
                    "result_count": 1,
                    "spend_usd": 20.0,
                    "purchase_value_usd": 0.0,
                    "impressions": 500,
                    "clicks": 5,
                },
            ]
        if "FROM shopify_orders" in sql:
            return [{
                "product_id": 42,
                "shopify_order_count": 2,
                "shopify_quantity": 3,
                "shopify_revenue": 120.0,
            }]
        if "FROM dianxiaomi_order_lines" in sql:
            return [{
                "product_id": 42,
                "dianxiaomi_order_count": 2,
                "dianxiaomi_units": 3,
                "dianxiaomi_total_sales": 150.0,
            }]
        raise AssertionError(sql)

    monkeypatch.setattr(oa, "query", fake_query)

    summary = oa.get_meta_ad_summary(start_date="2026-05-18", end_date="2026-05-18")

    assert any("FROM meta_ad_realtime_daily_campaign_metrics" in sql for sql, _args in queries)
    assert summary["period"]["source"] == "meta_ad_realtime_daily_campaign_metrics"
    assert len(summary["rows"]) == 2
    row = next(r for r in summary["rows"] if r["product_id"] == 42)
    assert row["product_id"] == 42
    assert row["display_name"] == "Glow Product"
    assert row["spend_usd"] == 50.0
    assert row["purchase_value_usd"] == 80.0
    assert row["result_count"] == 3
    assert row["link_clicks"] == 10
    assert row["impressions"] == 1000
    assert row["shopify_order_count"] == 2
    assert row["dianxiaomi_total_sales"] == 150.0
    assert summary["unmatched"][0]["campaign_name"] == "Unknown Campaign"
    assert summary["unmatched"][0]["spend_usd"] == 20.0


def test_get_meta_ad_summary_filters_by_ad_account(monkeypatch):
    report_start = oa._parse_meta_date("2026-05-17")
    report_end = oa._parse_meta_date("2026-05-17")
    queries = []

    monkeypatch.setattr(
        oa,
        "query_one",
        lambda sql, args=(): (_ for _ in ()).throw(AssertionError("batch lookup should not run")),
    )

    def fake_query(sql, args=()):
        queries.append((sql, args))
        if "FROM meta_ad_daily_campaign_metrics m" in sql and "LEFT JOIN media_products" in sql:
            return []
        if "FROM meta_ad_daily_campaign_metrics" in sql and "product_id IS NULL" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(oa, "query", fake_query)

    summary = oa.get_meta_ad_summary(
        start_date="2026-05-17",
        end_date="2026-05-17",
        ad_account_id="act_1253003326160754",
    )

    assert summary["rows"] == []
    main_sql, main_args = queries[0]
    unmatched_sql, unmatched_args = queries[1]
    assert "m.ad_account_id = %s" in main_sql
    assert main_args == (report_start, report_end, "1253003326160754")
    assert "ad_account_id = %s" in unmatched_sql
    assert unmatched_args == (report_start, report_end, "1253003326160754")


def test_get_meta_ad_summary_merges_dianxiaomi_order_metrics(monkeypatch):
    report_start = oa._parse_meta_date("2026-04-01")
    report_end = oa._parse_meta_date("2026-04-18")

    monkeypatch.setattr(
        oa,
        "query_one",
        lambda sql, args=(): (_ for _ in ()).throw(AssertionError("batch lookup should not run")),
    )

    def fake_query(sql, args=()):
        if "FROM meta_ad_daily_campaign_metrics m" in sql and "LEFT JOIN media_products" in sql:
            return [
                {
                    "product_id": 42,
                    "product_name": "Glow Set",
                    "media_product_code": "glow-set-rjc",
                    "matched_product_code": "glow-set-rjc",
                    "campaign_name": "Campaign A",
                    "result_count": 6,
                    "spend_usd": 20.0,
                    "purchase_value_usd": 45.0,
                    "link_clicks": 0,
                    "add_to_cart_count": 0,
                    "initiate_checkout_count": 0,
                    "impressions": 0,
                }
            ]
        if "FROM shopify_orders" in sql:
            return []
        if "FROM dianxiaomi_order_lines" in sql:
            assert "meta_business_date >= %s" in sql
            assert "meta_business_date <= %s" in sql
            assert args == (42, report_start, report_end)
            return [
                {
                    "product_id": 42,
                    "dianxiaomi_order_count": 3,
                    "dianxiaomi_units": 7,
                    "dianxiaomi_total_sales": 125.0,
                }
            ]
        if "FROM meta_ad_daily_campaign_metrics" in sql and "product_id IS NULL" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(oa, "query", fake_query)

    summary = oa.get_meta_ad_summary(start_date="2026-04-01", end_date="2026-04-18")

    row = summary["rows"][0]
    assert row["dianxiaomi_order_count"] == 3
    assert row["dianxiaomi_units"] == 7
    assert row["dianxiaomi_total_sales"] == 125.0
    assert row["dianxiaomi_roas"] == 6.25


def test_data_analysis_page_has_ads_tab_and_renamed_title(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "数据分析" in body
    assert "订单导入" in body
    assert "订单分析" in body
    assert "广告分析" in body
    assert 'data-tab="ads"' in body
    assert 'id="panelAds"' in body


def test_data_analysis_page_has_meta_ad_accounts_tab(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "广告账户" in body
    assert 'data-tab="adAccounts"' in body
    assert 'id="panelAdAccounts"' in body
    assert 'id="metaAdAccountsBody"' in body
    assert 'id="metaAdSyncModal"' in body
    assert 'data-meta-sync-tab="settings"' in body
    assert 'data-meta-sync-tab="progress"' in body
    assert 'id="metaAdSyncStart"' in body
    assert "同步进度" in body


def test_meta_ad_accounts_tab_renders_timezone_column_and_datalist(authed_client_no_db):
    """AUT-28: 「广告账户」Tab 暴露 timezone 字段。

    Docs-anchor: docs/superpowers/specs/2026-05-09-meta-ads-account-timezone-and-async-fix.md
    «UI 接入（AUT-28）» 节。
    """
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert ">时区<" in body
    assert 'id="metaAdAccountTimezoneOptions"' in body
    for tz in (
        "America/Los_Angeles",
        "America/New_York",
        "Asia/Shanghai",
        "Europe/London",
        "UTC",
    ):
        assert f'value="{tz}"' in body
    assert 'data-maa-field="timezone"' in body
    assert 'list="metaAdAccountTimezoneOptions"' in body


def test_meta_ad_accounts_tab_renders_column_preset_choices(authed_client_no_db):
    """Docs-anchor: docs/superpowers/specs/2026-05-09-ads-purchase-value-order-fallback-design.md."""
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert ">列模板<" in body
    assert 'id="metaAdColumnPresetChoices"' in body
    assert 'data-maa-field="column_preset_choice"' in body
    assert "1680560372975676" in body
    assert "1645951873103193" in body
    assert "1658418688523178" in body


def test_meta_ad_accounts_api_reads_accounts(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as routes

    account = SimpleNamespace(
        code="Omurio",
        label="Omurio",
        account_id="1253003326160754",
        business_id="909367947900474",
        csv_prefix="Omurio",
        store_codes=("omurio",),
        enabled=True,
        note="",
        to_dict=lambda: {
            "code": "Omurio",
            "label": "Omurio",
            "account_id": "1253003326160754",
            "business_id": "909367947900474",
            "csv_prefix": "Omurio",
            "store_codes": ["omurio"],
            "enabled": True,
            "note": "",
        },
    )
    monkeypatch.setattr(
        routes,
        "meta_ad_accounts",
        SimpleNamespace(
            AVAILABLE_STORE_CODES=("newjoy", "omurio"),
            column_preset_choices=lambda: [
                {"label": "111", "value": "1680560372975676", "recommended_account_codes": ["newjoyloo"]},
            ],
            get_all_accounts=lambda: [account],
        ),
        raising=False,
    )

    response = authed_client_no_db.get("/order-analytics/meta-ad-accounts")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["available_store_codes"] == ["newjoy", "omurio"]
    assert payload["column_preset_choices"][0]["value"] == "1680560372975676"
    assert payload["accounts"][0]["code"] == "Omurio"
    assert payload["accounts"][0]["store_codes"] == ["omurio"]


def test_meta_ad_accounts_api_saves_accounts(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as routes

    saved = {}

    def fake_set_accounts(accounts):
        saved["accounts"] = accounts

    monkeypatch.setattr(
        routes,
        "meta_ad_accounts",
        SimpleNamespace(
            AVAILABLE_STORE_CODES=("newjoy", "omurio"),
            set_accounts=fake_set_accounts,
            get_all_accounts=lambda: [],
        ),
        raising=False,
    )

    response = authed_client_no_db.post(
        "/order-analytics/meta-ad-accounts",
        json={
            "accounts": [
                {
                    "code": "Omurio",
                    "label": "Omurio",
                    "account_id": "act_1253003326160754",
                    "business_id": "909367947900474",
                    "csv_prefix": "Omurio",
                    "store_codes": ["omurio"],
                    "enabled": True,
                    "note": "live",
                }
            ]
        },
    )

    assert response.status_code == 200
    assert saved["accounts"][0]["store_codes"] == ["omurio"]
    assert response.get_json()["ok"] is True


def test_meta_ad_accounts_api_round_trips_timezone(authed_client_no_db, monkeypatch):
    """AUT-28: GET 透出 timezone，POST 把 timezone 字段原样喂给 set_accounts。

    Docs-anchor: docs/superpowers/specs/2026-05-09-meta-ads-account-timezone-and-async-fix.md
    """
    from web.routes import order_analytics as routes

    saved = {}

    def fake_set_accounts(accounts):
        saved["accounts"] = accounts

    account_dict = {
        "code": "newjoyloo_bak",
        "label": "Newjoyloo Bak",
        "account_id": "1861285821213497",
        "business_id": "476723373113063",
        "csv_prefix": "newjoyloo",
        "store_codes": ["newjoy"],
        "enabled": True,
        "note": "",
        "sync_mode": "xhr_api",
        "timezone": "Asia/Shanghai",
    }
    account = SimpleNamespace(to_dict=lambda: dict(account_dict))
    monkeypatch.setattr(
        routes,
        "meta_ad_accounts",
        SimpleNamespace(
            AVAILABLE_STORE_CODES=("newjoy", "omurio"),
            get_all_accounts=lambda: [account],
            set_accounts=fake_set_accounts,
        ),
        raising=False,
    )

    get_response = authed_client_no_db.get("/order-analytics/meta-ad-accounts")
    assert get_response.status_code == 200
    assert get_response.get_json()["accounts"][0]["timezone"] == "Asia/Shanghai"

    post_response = authed_client_no_db.post(
        "/order-analytics/meta-ad-accounts",
        json={"accounts": [dict(account_dict, timezone="America/New_York")]},
    )
    assert post_response.status_code == 200
    assert saved["accounts"][0]["timezone"] == "America/New_York"


def test_meta_ad_accounts_api_rejects_invalid_timezone(authed_client_no_db, monkeypatch):
    """AUT-28: 非法 IANA 字符串由 _coerce_timezone 拒绝，路由把 ValueError 翻译成 400。"""
    from web.routes import order_analytics as routes

    def fake_set_accounts(accounts):
        raise ValueError(
            "invalid timezone for account 'x': timezone must be a valid IANA name "
            "(e.g. America/Los_Angeles), got 'Foo/Bar'"
        )

    monkeypatch.setattr(
        routes,
        "meta_ad_accounts",
        SimpleNamespace(
            AVAILABLE_STORE_CODES=("newjoy", "omurio"),
            set_accounts=fake_set_accounts,
            get_all_accounts=lambda: [],
        ),
        raising=False,
    )

    response = authed_client_no_db.post(
        "/order-analytics/meta-ad-accounts",
        json={
            "accounts": [
                {
                    "code": "x",
                    "label": "X",
                    "account_id": "1",
                    "business_id": "2",
                    "csv_prefix": "x",
                    "store_codes": ["newjoy"],
                    "enabled": True,
                    "timezone": "Foo/Bar",
                }
            ]
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"] == "invalid_account"
    assert "Foo/Bar" in payload["detail"]


def test_meta_ad_account_manual_sync_api_starts_job(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as routes

    started = {}

    def fake_start_job(**kwargs):
        started.update(kwargs)
        return {
            "job_id": "job-1",
            "status": "queued",
            "account": {"code": kwargs["account_code"], "label": "Newjoyloo"},
            "total_days": 2,
            "completed_days": 0,
        }

    monkeypatch.setattr(
        routes,
        "meta_ad_manual_sync",
        SimpleNamespace(
            DEFAULT_INTERVAL_SECONDS=20,
            ManualSyncAlreadyRunning=RuntimeError,
            ManualSyncValidationError=ValueError,
            start_job=fake_start_job,
        ),
        raising=False,
    )
    monkeypatch.setattr(routes, "start_background_task", lambda fn, job_id: None, raising=False)

    response = authed_client_no_db.post(
        "/order-analytics/meta-ad-accounts/newjoyloo/manual-sync",
        json={"start_date": "2026-05-01", "end_date": "2026-05-02", "interval_seconds": 20},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["job"]["job_id"] == "job-1"
    assert started["account_code"] == "newjoyloo"
    assert started["start_date"].isoformat() == "2026-05-01"
    assert started["end_date"].isoformat() == "2026-05-02"
    assert started["interval_seconds"] == 20


def test_meta_ad_account_manual_sync_status_api_returns_job(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as routes

    monkeypatch.setattr(
        routes,
        "meta_ad_manual_sync",
        SimpleNamespace(get_job=lambda job_id: {"job_id": job_id, "status": "running", "completed_days": 1}),
        raising=False,
    )

    response = authed_client_no_db.get("/order-analytics/meta-ad-sync-jobs/job-1")

    assert response.status_code == 200
    assert response.get_json()["job"]["status"] == "running"


def test_meta_ad_account_manual_sync_status_api_reports_missing_job(authed_client_no_db, monkeypatch):
    from web.routes import order_analytics as routes

    monkeypatch.setattr(
        routes,
        "meta_ad_manual_sync",
        SimpleNamespace(get_job=lambda job_id: None),
        raising=False,
    )

    response = authed_client_no_db.get("/order-analytics/meta-ad-sync-jobs/missing")

    assert response.status_code == 404
    assert response.get_json()["error"] == "job_not_found"


def test_data_analysis_tabs_and_type_controls_are_capsule_buttons(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "JS 已加载" not in body
    assert 'class="oa-tabs oa-tabs-topbar"' in body
    assert 'data-view-mode="month"' in body
    assert 'data-view-mode="week"' in body
    assert 'class="oad-row-action"' in body
    assert '<select id="viewMode"' not in body
    assert '<select id="adFrequency"' not in body
    assert 'data-ad-frequency' not in body


def test_analytics_range_controls_match_country_dashboard(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for key in ("today", "yesterday", "thisWeek", "lastWeek", "thisMonth", "lastMonth"):
        assert f'data-dashboard-range="{key}"' in body
        assert f'data-true-roas-range="{key}"' in body
        assert f'data-ad-range="{key}"' in body
    assert 'id="oadStartDate"' in body
    assert 'id="oadEndDate"' in body
    assert 'id="trueRoasStart"' in body
    assert 'id="trueRoasEnd"' in body
    assert 'id="adStartDate"' in body
    assert 'id="adEndDate"' in body
    assert 'id="adPeriodSelect"' not in body
    assert '/order-analytics/ad-summary?start_date=' in body


def test_ads_default_date_range_uses_meta_business_today_for_campaign(authed_client_no_db):
    """广告分析 Campaign 默认日期范围选择 Meta 广告日「今天」。

    Meta 今天由 Beijing 16:00 cutover 决定，例如 2026-05-18 16:00 前是
    2026-05-17。
    """
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "function adsDefaultStartIso(level)" in body
    assert "function adsDefaultEndIso(level)" in body
    assert "return formatDateInput(d);" in body
    assert "setAdRange('today', true);" in body
    assert "setInputValue('adStartDate', adsDefaultStartIso());" not in body
    assert "return year + '-03-01';" not in body
    assert "return year + '-05-03';" not in body
    assert "startListEl.value = adsDefaultStartIso(level);" in body
    assert "startDetailEl.value = adsDefaultStartIso(level);" in body
    assert ".value || adsDefaultStartIso(level);" in body
    assert "adsDaysAgoIso" not in body


def test_ads_adset_and_ad_default_to_meta_business_today(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "docs/superpowers/specs/2026-05-28-ads-level-realtime-default-today.md" in body
    assert "function adsLevelUsesRealtime(level)" in body
    assert "return ADS_LEVELS.indexOf(level) !== -1;" in body
    assert "return level === 'campaign';" not in body
    assert "docs/superpowers/specs/2026-05-28-ads-non-realtime-default-closed-day.md" not in body
    assert "window.orderAnalyticsMetaCalendar.addDays(d, -1)" not in body
    assert "endListEl.value = adsDefaultEndIso(level);" in body
    assert "endDetailEl.value = adsDefaultEndIso(level);" in body
    assert ".value || adsDefaultEndIso(level);" in body
    init_block = body[
        body.index("ADS_LEVELS.forEach(function(level) {"):
        body.index("adsBindSearch(level);")
    ]
    assert "adsSyncLevelRangeSelection(level);" in init_block


def test_ads_analysis_page_has_ad_account_filter_controls(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "广告户" in body
    assert 'id="adAccountFilter"' in body
    assert 'data-ads-account-filter="overview"' in body
    for level in ("campaign", "adset", "ad"):
        assert f'data-ads-account-filter="{level}"' in body
        assert f'data-ads-detail-account="{level}"' in body
    assert "adsPopulateAccountFilters" in body
    assert "getSelectedAdsAccountId" in body
    assert "'&ad_account_id=' + encodeURIComponent" in body


def test_ads_level_search_queries_bottom_list_without_dropdown(authed_client_no_db):
    """广告分析四个子 Tab 搜索框应直接查询底部列表，不再渲染下拉结果。

    Docs-anchor: docs/superpowers/specs/2026-05-11-ads-analytics-inline-search-list.md
    """
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-ads-search-input="campaign"' in body
    assert 'data-ads-search-input="adset"' in body
    assert 'data-ads-search-input="ad"' in body
    assert 'id="adOverviewSearchInput"' in body
    assert 'data-ads-search-results' not in body
    assert 'id="adRefresh">查询</button>' in body
    assert 'data-ads-list-refresh="campaign">查询</button>' in body
    assert 'data-ads-list-refresh="adset">查询</button>' in body
    assert 'data-ads-list-refresh="ad">查询</button>' in body
    assert "/order-analytics/ads/search" not in body
    assert "overviewQuery = overviewSearchInput ? overviewSearchInput.value.trim() : '';" in body
    assert "query = searchInput.value.trim();" in body
    assert "'&q=' + encodeURIComponent(query)" in body


def test_ads_analysis_page_has_unmatched_campaigns_subtab(authed_client_no_db):
    """Docs-anchor: docs/superpowers/specs/2026-06-01-ads-unmatched-campaign-tab-design.md"""
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-ads-subtab="unmatched-campaigns"' in body
    assert 'data-subpanel="unmatched-campaigns"' in body
    assert 'id="adUnmatchedSearchInput"' in body
    assert 'data-ads-account-filter="unmatched-campaigns"' in body
    assert 'id="adUnmatchedRefresh"' in body
    assert "function loadAdUnmatchedCampaigns()" in body
    assert "function renderAdUnmatchedCampaigns(rows)" in body
    assert "openAdMatchModal(row)" in body
    assert "未匹配产品广告计划" in body
    assert "只包含素材管理库里无法解析到产品的 Campaign" in body


def test_order_analytics_range_presets_use_shared_meta_calendar(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "window.orderAnalyticsMetaCalendar" in body
    assert "cutoverHourBj: 16" in body
    assert "timeZone: 'Asia/Shanghai'" in body
    assert "function resolveDashboardRange(range) {\n    return window.orderAnalyticsMetaCalendar.resolveRange(range);\n  }" in body
    assert "function resolveCountryRange(range) {\n    return window.orderAnalyticsMetaCalendar.resolveRange(range);\n  }" in body
    assert "function setDxmRange(range, skipLoad) {\n    var bounds = window.orderAnalyticsMetaCalendar.resolveRange(range || 'thisMonth');" in body
    assert "var lastMon = addDays(startOfWeek(window.orderAnalyticsMetaCalendar.today()), -7);" in body
    assert "var base = input.value ? new Date(input.value + 'T00:00:00') : window.orderAnalyticsMetaCalendar.today();" in body
    assert "var now = window.orderAnalyticsMetaCalendar.today();" in body


def test_ads_stats_card_shows_report_roas(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "statCard('购物转化价值', fmtMoney(s.total_purchase_value_usd))" in body
    assert "statCard('ROAS', fmtAdRoas(s.total_purchase_value_usd, s.total_spend_usd))" in body
    assert "function fmtAdRoas(purchaseValue, spend)" in body


def test_ads_analysis_table_includes_dianxiaomi_order_columns(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "'店小秘订单数'" in body
    assert "'店小秘销售件数'" in body
    assert "'店小秘总销售额'" in body
    assert "'店小秘 ROAS'" in body
    assert "row.dianxiaomi_order_count" in body
    assert "row.dianxiaomi_units" in body
    assert "row.dianxiaomi_total_sales" in body
    assert "row.dianxiaomi_roas" in body


def test_ad_upload_route_imports_meta_report(authed_client_no_db, monkeypatch):
    parsed_rows = [{"campaign_name": "demo", "report_start_date": oa._parse_meta_date("2026-04-01"), "report_end_date": oa._parse_meta_date("2026-04-07")}]

    monkeypatch.setattr("web.routes.order_analytics.oa.parse_meta_ad_file", lambda stream, filename: parsed_rows)
    monkeypatch.setattr(
        "web.routes.order_analytics.oa.import_meta_ad_rows",
        lambda rows, filename, file_bytes, import_frequency: {
            "batch_id": 9,
            "imported": 1,
            "updated": 0,
            "skipped": 0,
            "matched": 1,
        },
    )
    monkeypatch.setattr(
        "web.routes.order_analytics.oa.get_meta_ad_stats",
        lambda: {"total_rows": 1, "matched_rows": 1},
    )

    response = authed_client_no_db.post(
        "/order-analytics/ad-upload",
        data={
            "frequency": "weekly",
            "file": (io.BytesIO(b"meta-csv"), "meta.csv"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["batch_id"] == 9
    assert payload["imported"] == 1
    assert payload["matched"] == 1
    assert payload["total_rows"] == 1


def test_dashboard_endpoint_admin_only_redirects_when_anonymous():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    response = client.get("/order-analytics/dashboard")
    # 未登录 → 302 重定向到登录
    assert response.status_code in (302, 401)


def test_dashboard_endpoint_default_returns_json(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.order_analytics.oa.get_dashboard",
        lambda **kwargs: {
            "period": {"start": "2026-04-01", "end": "2026-04-25", "label": "2026 年 4 月（1-25 日）"},
            "compare_period": {"start": "2026-03-01", "end": "2026-03-25", "label": "..."},
            "country": None,
            "products": [],
            "summary": {"total_orders": 0, "total_revenue": 0, "total_spend": 0, "total_roas": None},
        },
    )
    response = authed_client_no_db.get("/order-analytics/dashboard?period=month&year=2026&month=4")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["period"]["start"] == "2026-04-01"
    assert payload["products"] == []


def test_dashboard_endpoint_passes_explicit_date_range(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_dashboard(**kwargs):
        captured.update(kwargs)
        return {
            "period": {"start": "2026-04-01", "end": "2026-04-18", "label": "x"},
            "compare_period": None,
            "country": None,
            "products": [],
            "summary": {},
        }

    monkeypatch.setattr("web.routes.order_analytics.oa.get_dashboard", fake_dashboard)

    response = authed_client_no_db.get(
        "/order-analytics/dashboard?start_date=2026-04-01&end_date=2026-04-18"
    )

    assert response.status_code == 200
    assert captured["period"] == "range"
    assert captured["start_date"] == "2026-04-01"
    assert captured["end_date"] == "2026-04-18"
    assert "year" not in captured or captured["year"] is None


def test_dashboard_endpoint_invalid_period_returns_400(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics/dashboard?period=year")
    assert response.status_code == 400
    assert "invalid_period" in response.get_data(as_text=True)


def test_dashboard_endpoint_passes_country_filter(authed_client_no_db, monkeypatch):
    captured = {}
    def fake_dashboard(**kwargs):
        captured.update(kwargs)
        return {"period": {"start": "2026-04-01", "end": "2026-04-25", "label": "x"},
                "compare_period": None, "country": "DE", "products": [], "summary": {}}
    monkeypatch.setattr("web.routes.order_analytics.oa.get_dashboard", fake_dashboard)

    response = authed_client_no_db.get(
        "/order-analytics/dashboard?period=month&year=2026&month=4&country=DE"
    )
    assert response.status_code == 200
    assert captured["country"] == "DE"
    assert captured["period"] == "month"


def test_dashboard_tab_is_default(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-tab="dashboard"' in body
    assert 'id="panelDashboard"' in body


def test_get_dashboard_defaults_to_order_count_sort(monkeypatch):
    monkeypatch.setattr(
        "appcore.order_analytics._resolve_period_range",
        lambda *args, **kwargs: (oa._parse_meta_date("2026-04-01"), oa._parse_meta_date("2026-04-30")),
    )
    monkeypatch.setattr(
        "appcore.order_analytics._aggregate_orders_by_product",
        lambda start, end, country=None: {
            1: {"orders": 1, "units": 1, "revenue": 500.0},
            2: {"orders": 3, "units": 3, "revenue": 100.0},
        },
    )
    monkeypatch.setattr("appcore.order_analytics._aggregate_ads_by_product", lambda start, end: {})
    monkeypatch.setattr("appcore.order_analytics._count_media_items_by_product", lambda: {})
    monkeypatch.setattr(
        "appcore.order_analytics._load_products",
        lambda ids, search=None: {
            1: {"id": 1, "name": "Low Orders", "product_code": "low"},
            2: {"id": 2, "name": "High Orders", "product_code": "high"},
        },
    )

    result = oa.get_dashboard(period="day", date_str="2026-04-20", compare=False)

    assert [row["product_id"] for row in result["products"]] == [2, 1]


def test_dashboard_tab_label_chinese(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")
    assert "产品看板" in response.get_data(as_text=True)


# ── 三级 tab：list / search / detail（Docs-anchor: docs/superpowers/specs/2026-05-08-ads-analytics-tabs-design.md）

def test_get_ads_level_list_rejects_invalid_level():
    import pytest

    with pytest.raises(ValueError):
        oa.get_ads_level_list(level="bogus")


def test_get_ads_level_list_aggregates_per_code(monkeypatch):
    captured: list[dict] = []

    def fake_query_one(sql, args=()):
        return {"total": 2}

    def fake_query(sql, args=()):
        captured.append({"sql": sql, "args": args})
        # broken-groups probe (spec 2026-05-09) returns nothing — old-account rows
        # have non-zero purchase, so no fallback is triggered.
        if "GROUP BY ad_account_id, LOWER(matched_product_code)" in sql:
            return []
        return [
            {
                "code": "abc-rjc", "name": "Glow Set",
                "ad_account_id": "1234", "ad_account_name": "newjoyloo",
                "matched_product_code": "abc-rjc",
                "spend_usd": 1000.0, "purchase_value_usd": 2000.0,
                "result_count": 50, "day_count": 7,
                "roas_purchase": 2.0,
            },
        ]

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "query", fake_query)
    result = oa.get_ads_level_list("campaign", start_date="2026-04-01", end_date="2026-04-14")
    assert result["level"] == "campaign"
    assert result["period"]["start_date"] == "2026-04-01"
    assert result["period"]["end_date"] == "2026-04-14"
    assert result["rows"][0]["code"] == "abc-rjc"
    assert result["rows"][0]["roas_purchase"] == 2.0
    assert result["rows"][0]["purchase_value_source"] == "meta"
    assert result["total"] == 2
    main_sql = next(
        c["sql"] for c in captured if "GROUP BY normalized_campaign_code, ad_account_id" in c["sql"]
    )
    assert "FROM meta_ad_daily_campaign_metrics" in main_sql
    # Spec 2026-05-09: 没有兜底命中 → status 仍为 ok。
    assert result["data_quality"]["status"] == "ok"


def test_get_ads_level_list_filters_by_search_query(monkeypatch):
    captured: list[dict] = []

    def fake_query_one(sql, args=()):
        captured.append({"sql": sql, "args": args})
        return {"total": 0}

    def fake_query(sql, args=()):
        captured.append({"sql": sql, "args": args})
        return []

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_ads_level_list(
        "ad",
        start_date="2026-04-01",
        end_date="2026-05-03",
        q="water-blaster",
    )

    assert result["rows"] == []
    main_sql = next(c["sql"] for c in captured if "FROM meta_ad_daily_ad_metrics" in c["sql"])
    assert "LOWER(ad_name) LIKE LOWER(%s)" in main_sql
    assert "LOWER(normalized_ad_code) LIKE LOWER(%s)" in main_sql
    assert "LOWER(COALESCE(matched_product_code, '')) LIKE LOWER(%s)" in main_sql
    assert "%water-blaster%" in captured[0]["args"]


def test_get_ads_level_list_filters_by_ad_account(monkeypatch):
    captured: list[dict] = []
    report_start = oa._parse_meta_date("2026-05-17")
    report_end = oa._parse_meta_date("2026-05-17")

    def fake_query_one(sql, args=()):
        captured.append({"sql": sql, "args": args})
        return {"total": 0}

    def fake_query(sql, args=()):
        captured.append({"sql": sql, "args": args})
        return []

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_ads_level_list(
        "campaign",
        start_date="2026-05-17",
        end_date="2026-05-17",
        ad_account_id="act_1253003326160754",
    )

    assert result["rows"] == []
    total_query = captured[0]
    list_query = captured[1]
    assert "ad_account_id = %s" in total_query["sql"]
    assert total_query["args"] == (report_start, report_end, "1253003326160754")
    assert "ad_account_id = %s" in list_query["sql"]
    assert list_query["args"] == (report_start, report_end, "1253003326160754", 50, 0)


def test_get_ads_level_list_filters_adsets_by_campaign_parent(monkeypatch):
    today = oa.current_meta_business_date()
    captured: list[dict] = []

    def fake_query_one(sql, args=()):
        captured.append({"sql": sql, "args": args})
        return {"total": 0}

    def fake_query(sql, args=()):
        captured.append({"sql": sql, "args": args})
        return []

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_ads_level_list(
        "adset",
        start_date=today.isoformat(),
        end_date=today.isoformat(),
        parent_level="campaign",
        parent_code="sonic-lens-refresher-rjc",
    )

    assert result["parent"] == {
        "level": "campaign",
        "code": "sonic-lens-refresher-rjc",
    }
    union_query = next(q for q in captured if "UNION ALL" in q["sql"])
    assert "AND normalized_adset_code LIKE %s" in union_query["sql"]
    assert "AND m.normalized_campaign_code = %s" in union_query["sql"]
    assert "sonic-lens-refresher-rjc%" in union_query["args"]
    assert "sonic-lens-refresher-rjc" in union_query["args"]


def test_get_ads_level_list_filters_ads_by_adset_parent(monkeypatch):
    captured: list[dict] = []
    report_start = oa._parse_meta_date("2026-05-17")
    report_end = oa._parse_meta_date("2026-05-17")

    def fake_query_one(sql, args=()):
        captured.append({"sql": sql, "args": args})
        return {"total": 0}

    def fake_query(sql, args=()):
        captured.append({"sql": sql, "args": args})
        return []

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_ads_level_list(
        "ad",
        start_date="2026-05-17",
        end_date="2026-05-17",
        parent_level="adset",
        parent_code="glow-go-insect-set-rjc",
    )

    assert result["parent"] == {
        "level": "adset",
        "code": "glow-go-insect-set-rjc",
    }
    total_query = captured[0]
    list_query = captured[1]
    assert "normalized_ad_code LIKE %s" in total_query["sql"]
    assert total_query["args"] == (report_start, report_end, "glow-go-insect-set-rjc%")
    assert "normalized_ad_code LIKE %s" in list_query["sql"]
    assert list_query["args"] == (report_start, report_end, "glow-go-insect-set-rjc%", 50, 0)


def test_get_ads_level_list_rejects_invalid_parent_filter():
    import pytest

    with pytest.raises(ValueError):
        oa.get_ads_level_list(
            "adset",
            start_date="2026-05-17",
            end_date="2026-05-17",
            parent_level="adset",
            parent_code="glow-go-insect-set-rjc",
        )


def test_search_ads_by_level_rejects_empty_q():
    import pytest

    with pytest.raises(ValueError):
        oa.search_ads_by_level("campaign", q="")


def test_search_ads_by_level_targets_correct_table(monkeypatch):
    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {
                "code": "glow-go-rjc",
                "name": "Glow Go RJC",
                "last_active_date": __import__("datetime").date(2026, 5, 7),
                "total_spend_usd_30d": 4321.55,
            },
        ]

    monkeypatch.setattr(oa, "query", fake_query)
    result = oa.search_ads_by_level("adset", q="glow")
    assert result["level"] == "adset"
    assert result["rows"][0]["last_active_date"] == "2026-05-07"
    assert "FROM meta_ad_daily_adset_metrics" in captured["sql"]
    assert "adset_name LIKE %s" in captured["sql"]
    assert captured["args"][0] == "%glow%"


def test_get_ads_level_detail_includes_realtime_today_for_campaign(monkeypatch):
    today = oa.current_meta_business_date()
    yesterday = today - __import__("datetime").timedelta(days=1)

    def fake_query(sql, args=()):
        # Daily rows query (yesterday only)
        return [
            {
                "meta_business_date": yesterday,
                "name": "Glow Go RJC",
                "ad_account_id": "1234",
                "ad_account_name": "newjoyloo",
                "spend_usd": 100.0,
                "purchase_value_usd": 200.0,
                "result_count": 5,
                "raw_json": '{"link_click_cost":0.50,"cpm":12.34,"impressions":10000,"link_clicks":200}',
            },
        ]

    realtime_called = {"called": False}

    def fake_query_one(sql, args=()):
        if "realtime" in sql:
            realtime_called["called"] = True
            return {
                "spend_usd": 50.0, "purchase_value_usd": 80.0,
                "result_count": 3, "impressions": 5000, "clicks": 100,
                "snapshot_at": None,
                "campaign_name": "Glow Go RJC",
                "ad_account_id": "1234",
                "ad_account_name": "newjoyloo",
            }
        return None

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(oa, "query_one", fake_query_one)
    result = oa.get_ads_level_detail("campaign", code="glow-go-rjc",
                                      start_date=yesterday.isoformat(),
                                      end_date=today.isoformat())
    assert realtime_called["called"]
    dates = [row["date"] for row in result["rows"]]
    assert today.isoformat() in dates
    today_row = next(r for r in result["rows"] if r["date"] == today.isoformat())
    assert today_row["is_realtime"] is True
    assert today_row["spend_usd"] == 50.0
    # CPC = 50 / 100 = 0.5
    assert today_row["cpc_usd"] == 0.5
    # eCPM = 50 / 5000 * 1000 = 10.0
    assert today_row["ecpm_usd"] == 10.0
    # All rows have null budget (Q2=B placeholder)
    for row in result["rows"]:
        assert row["budget_usd"] is None


def test_get_ads_level_detail_includes_realtime_today_for_adset(monkeypatch):
    today = oa.current_meta_business_date()

    def fake_query(sql, args=()):
        assert "FROM meta_ad_daily_adset_metrics" in sql
        return []

    captured_realtime_sql = []

    def fake_query_one(sql, args=()):
        captured_realtime_sql.append(sql)
        if "meta_ad_realtime_daily_adset_metrics" in sql:
            return {
                "spend_usd": 50.0,
                "purchase_value_usd": 80.0,
                "result_count": 3,
                "impressions": 5000,
                "clicks": 100,
                "snapshot_at": None,
                "name": "Glow Set",
                "ad_account_id": "1234",
                "ad_account_name": "newjoyloo",
            }
        return None

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(oa, "query_one", fake_query_one)
    result = oa.get_ads_level_detail("adset", code="glow-set",
                                      start_date=today.isoformat(),
                                      end_date=today.isoformat())
    assert result["supports_realtime"] is True
    assert result["name"] == "Glow Set"
    assert any("normalized_adset_code = %s" in sql for sql in captured_realtime_sql)
    assert any("meta_ad_realtime_daily_adset_metrics" in sql for sql in captured_realtime_sql)
    assert result["rows"][0]["date"] == today.isoformat()
    assert result["rows"][0]["is_realtime"] is True
    assert result["rows"][0]["spend_usd"] == 50.0


def test_get_ads_level_detail_filters_by_ad_account(monkeypatch):
    captured = {}
    day = oa._parse_meta_date("2026-05-17")

    def fake_query(sql, args=()):
        if "FROM meta_ad_daily_adset_metrics" in sql and "WHERE normalized_adset_code = %s" in sql:
            captured["sql"] = sql
            captured["args"] = args
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(oa, "query_one", lambda sql, args=(): None)

    result = oa.get_ads_level_detail(
        "adset",
        code="water-blaster",
        start_date="2026-05-17",
        end_date="2026-05-17",
        ad_account_id="act_1253003326160754",
    )

    assert result["rows"] == []
    assert "ad_account_id = %s" in captured["sql"]
    assert captured["args"] == ("water-blaster", day, day, "1253003326160754")


def test_ads_list_route_400_for_invalid_level(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics/ads/list?level=bogus")
    assert response.status_code == 400
    assert b"invalid_param" in response.data


def test_ads_search_route_400_for_missing_q(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics/ads/search?level=campaign")
    assert response.status_code == 400
    assert b"q is required" in response.data


def test_ads_detail_route_400_for_missing_code(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics/ads/detail?level=campaign")
    assert response.status_code == 400
    assert b"code is required" in response.data


def test_ad_summary_route_passes_search_and_account_query_to_data_layer(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_summary(batch_id=None, start_date=None, end_date=None, q=None, ad_account_id=None):
        captured.update({
            "batch_id": batch_id,
            "start_date": start_date,
            "end_date": end_date,
            "q": q,
            "ad_account_id": ad_account_id,
        })
        return {"period": None, "rows": [], "unmatched": []}

    monkeypatch.setattr(oa, "get_meta_ad_summary", fake_summary)
    response = authed_client_no_db.get(
        "/order-analytics/ad-summary?start_date=2026-04-01&end_date=2026-05-03"
        "&q=water-blaster&ad_account_id=act_1253003326160754"
    )

    assert response.status_code == 200, response.data
    assert captured == {
        "batch_id": None,
        "start_date": "2026-04-01",
        "end_date": "2026-05-03",
        "q": "water-blaster",
        "ad_account_id": "act_1253003326160754",
    }


def test_get_ads_level_detail_parses_nested_raw_json(monkeypatch):
    """Production rows store metrics under raw_json["rows"][0]; flat fixtures must still work."""
    today = oa.current_meta_business_date()
    yesterday = today - __import__("datetime").timedelta(days=1)

    def fake_query(sql, args=()):
        return [
            {
                "meta_business_date": yesterday,
                "name": "Glow Go RJC",
                "ad_account_id": "1234",
                "ad_account_name": "newjoyloo",
                "spend_usd": 100.0,
                "purchase_value_usd": 200.0,
                "result_count": 5,
                # Production-style nested structure
                "raw_json": '{"rows": [{"已花费金额 (USD)": "100", '
                            '"CPM（千次展示费用） (USD)": "12.34", '
                            '"单次链接点击费用 - 独立用户 (USD)": "0.50", '
                            '"展示次数": "10000", "链接点击量": "200", '
                            '"加入购物车次数": "30", "结账发起次数": "12", '
                            '"视频平均播放时长": "00:00:08"}], "merged_rows": 1}',
            },
        ]

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(oa, "query_one", lambda sql, args=(): None)
    result = oa.get_ads_level_detail("campaign", code="glow-go-rjc",
                                      start_date=yesterday.isoformat(),
                                      end_date=yesterday.isoformat())
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["cpc_usd"] == 0.5
    assert row["ecpm_usd"] == 12.34
    assert row["impressions"] == 10000
    assert row["link_clicks"] == 200
    assert row["add_to_cart_count"] == 30
    assert row["initiate_checkout_count"] == 12
    assert row["video_avg_play_time"] == 8.0


def test_parse_raw_json_field_unwraps_rows_envelope():
    inner = {"展示次数": "5", "链接点击量": "1"}
    parsed = oa.meta_ads._parse_raw_json_field({"rows": [inner], "merged_rows": 1})
    assert parsed is inner


def test_parse_raw_json_field_passthrough_for_flat_dict():
    flat = {"link_clicks": 99}
    parsed = oa.meta_ads._parse_raw_json_field(flat)
    assert parsed == flat


# ── 购买金额按订单口径兜底（spec: 2026-05-09-ads-purchase-value-order-fallback-design.md） ──

class _FakeAccount:
    def __init__(self, account_id: str, store_codes: tuple[str, ...]):
        self.account_id = account_id
        self.store_codes = store_codes


def _capture_calls(handlers):
    """Build a fake `query` that dispatches based on SQL substring keys; records calls."""
    calls = []

    def fake_query(sql, args=()):
        calls.append({"sql": sql, "args": args})
        for needle, response in handlers.items():
            if needle in sql:
                if callable(response):
                    return response(sql, args)
                return response
        return []

    return fake_query, calls


def test_fill_purchase_value_from_orders_no_broken_groups_returns_zero(monkeypatch):
    rows = [{
        "ad_account_id": "old_account",
        "matched_product_code": "abc-rjc",
        "spend_usd": 1000.0,
        "purchase_value_usd": 1500.0,
        "roas_purchase": 1.5,
    }]

    def fake_query(sql, args=()):
        if "GROUP BY ad_account_id, LOWER(matched_product_code)" in sql:
            return []  # no broken groups
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    stats = oa.fill_purchase_value_from_orders(
        rows,
        level="ad",
        start_date=__import__("datetime").date(2026, 4, 25),
        end_date=__import__("datetime").date(2026, 5, 7),
        accounts_loader=lambda: [],
    )
    assert stats["fallback_row_count"] == 0
    assert rows[0]["purchase_value_usd"] == 1500.0
    assert rows[0]["purchase_value_source"] == "meta"


def test_fill_purchase_value_from_orders_allocates_revenue_proportional_to_spend(monkeypatch):
    """Omurio (account 1253...) 一共 2 条 ad，全部 purchase=0；订单表里 product 当期营收 1000，
    应当按 spend 比例分摊：spend 600 → 600；spend 400 → 400。"""
    rows = [
        {
            "code": "ad-a",
            "ad_account_id": "1253003326160754",
            "matched_product_code": "fully-automatic-water-blaster",
            "spend_usd": 600.0,
            "purchase_value_usd": 0.0,
            "roas_purchase": 0.0,
        },
        {
            "code": "ad-b",
            "ad_account_id": "1253003326160754",
            "matched_product_code": "fully-automatic-water-blaster",
            "spend_usd": 400.0,
            "purchase_value_usd": 0.0,
            "roas_purchase": 0.0,
        },
    ]

    def fake_query(sql, args=()):
        if "GROUP BY ad_account_id, LOWER(matched_product_code)" in sql:
            return [{
                "ad_account_id": "1253003326160754",
                "product_code": "fully-automatic-water-blaster",
                "group_spend": 1000.0,
                "group_purchase": 0.0,
            }]
        if "FROM dianxiaomi_order_lines" in sql:
            return [{
                "product_code_lc": "fully-automatic-water-blaster",
                "revenue": 1000.0,
            }]
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    stats = oa.fill_purchase_value_from_orders(
        rows,
        level="ad",
        start_date=__import__("datetime").date(2026, 4, 25),
        end_date=__import__("datetime").date(2026, 5, 7),
        accounts_loader=lambda: [_FakeAccount("1253003326160754", ("omurio",))],
    )
    assert stats["fallback_row_count"] == 2
    assert stats["fallback_revenue_total_usd"] == 1000.0
    assert rows[0]["purchase_value_usd"] == 600.0
    assert rows[0]["roas_purchase"] == 1.0  # 600 / 600
    assert rows[0]["purchase_value_source"] == "order_fallback"
    assert rows[1]["purchase_value_usd"] == 400.0
    assert rows[1]["roas_purchase"] == 1.0  # 400 / 400
    assert rows[1]["purchase_value_source"] == "order_fallback"


def test_fill_purchase_value_pool_aware_subtracts_other_accounts_meta_purchase(monkeypatch):
    """同一 store 下两个账户：A=正常(spend $1000, Meta purchase $1100)、B=broken(spend $200)；
    订单营收 $1100。剩余可分摊 = max(0, 1100 - 1100) = 0 → broken 账户拿到 $0。

    避免在「老户已经全额或超额上报」的产品上重复给 broken 账户算购买金额。
    """
    rows = [{
        "code": "ad-broken",
        "ad_account_id": "B_BROKEN",
        "matched_product_code": "shared-product",
        "spend_usd": 200.0,
        "purchase_value_usd": 0.0,
        "roas_purchase": 0.0,
    }]

    def fake_query(sql, args=()):
        if "GROUP BY ad_account_id, LOWER(matched_product_code)" in sql:
            return [
                {
                    "ad_account_id": "A_NORMAL", "product_code": "shared-product",
                    "group_spend": 1000.0, "group_purchase": 1100.0,
                },
                {
                    "ad_account_id": "B_BROKEN", "product_code": "shared-product",
                    "group_spend": 200.0, "group_purchase": 0.0,
                },
            ]
        if "FROM dianxiaomi_order_lines" in sql:
            return [{"product_code_lc": "shared-product", "revenue": 1100.0}]
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    stats = oa.fill_purchase_value_from_orders(
        rows,
        level="ad",
        start_date=__import__("datetime").date(2026, 4, 25),
        end_date=__import__("datetime").date(2026, 5, 7),
        accounts_loader=lambda: [
            _FakeAccount("A_NORMAL", ("newjoy",)),
            _FakeAccount("B_BROKEN", ("newjoy",)),
        ],
    )
    assert stats["fallback_row_count"] == 0
    assert rows[0]["purchase_value_usd"] == 0.0


def test_fill_purchase_value_pool_aware_distributes_remaining_revenue(monkeypatch):
    """同一 store 下两个账户：A=正常(spend $1000, Meta purchase $400)、B=broken(spend $200)；
    订单营收 $1000。剩余 = $1000 - $400 = $600；broken 在 pool 中的占比 = 200/1200 ≈ 16.67%；
    derived = 600 × 200/1200 = $100。
    """
    rows = [{
        "code": "ad-broken",
        "ad_account_id": "B_BROKEN",
        "matched_product_code": "shared-product",
        "spend_usd": 200.0,
        "purchase_value_usd": 0.0,
        "roas_purchase": 0.0,
    }]

    def fake_query(sql, args=()):
        if "GROUP BY ad_account_id, LOWER(matched_product_code)" in sql:
            return [
                {
                    "ad_account_id": "A_NORMAL", "product_code": "shared-product",
                    "group_spend": 1000.0, "group_purchase": 400.0,
                },
                {
                    "ad_account_id": "B_BROKEN", "product_code": "shared-product",
                    "group_spend": 200.0, "group_purchase": 0.0,
                },
            ]
        if "FROM dianxiaomi_order_lines" in sql:
            return [{"product_code_lc": "shared-product", "revenue": 1000.0}]
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    stats = oa.fill_purchase_value_from_orders(
        rows,
        level="ad",
        start_date=__import__("datetime").date(2026, 4, 25),
        end_date=__import__("datetime").date(2026, 5, 7),
        accounts_loader=lambda: [
            _FakeAccount("A_NORMAL", ("newjoy",)),
            _FakeAccount("B_BROKEN", ("newjoy",)),
        ],
    )
    assert stats["fallback_row_count"] == 1
    assert rows[0]["purchase_value_usd"] == 100.0
    assert rows[0]["purchase_value_source"] == "order_fallback"


def test_fill_purchase_value_from_orders_skips_groups_with_meta_purchase(monkeypatch):
    """老户里某个 ad row 真零转化（同 product 其它 ad 有转化）→ 不应被覆盖。"""
    rows = [
        {
            "code": "ad-zero",
            "ad_account_id": "old_account",
            "matched_product_code": "glow-rjc",
            "spend_usd": 100.0,
            "purchase_value_usd": 0.0,  # 真零，但同组其它行有转化
            "roas_purchase": 0.0,
        },
    ]

    def fake_query(sql, args=()):
        if "GROUP BY ad_account_id, LOWER(matched_product_code)" in sql:
            return []  # 这个 group 不进 broken_groups（因为同 product 其它 ad 有转化）
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    stats = oa.fill_purchase_value_from_orders(
        rows,
        level="ad",
        start_date=__import__("datetime").date(2026, 4, 25),
        end_date=__import__("datetime").date(2026, 5, 7),
        accounts_loader=lambda: [_FakeAccount("old_account", ("newjoy",))],
    )
    assert stats["fallback_row_count"] == 0
    assert rows[0]["purchase_value_usd"] == 0.0
    assert rows[0]["purchase_value_source"] == "meta"


def test_fill_purchase_value_from_orders_skips_unknown_account(monkeypatch):
    rows = [{
        "code": "ad-x",
        "ad_account_id": "unknown_account_999",
        "matched_product_code": "abc",
        "spend_usd": 100.0,
        "purchase_value_usd": 0.0,
        "roas_purchase": 0.0,
    }]

    def fake_query(sql, args=()):
        if "GROUP BY ad_account_id, LOWER(matched_product_code)" in sql:
            return [{
                "ad_account_id": "unknown_account_999",
                "product_code": "abc",
                "group_spend": 100.0,
                "group_purchase": 0.0,
            }]
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    stats = oa.fill_purchase_value_from_orders(
        rows,
        level="ad",
        start_date=__import__("datetime").date(2026, 4, 25),
        end_date=__import__("datetime").date(2026, 5, 7),
        accounts_loader=lambda: [_FakeAccount("known_other", ("newjoy",))],
    )
    # account_id 不在 accounts loader 中 → 跳过；保持原值。
    assert stats["fallback_row_count"] == 0
    assert rows[0]["purchase_value_usd"] == 0.0


def test_fill_purchase_value_from_orders_skips_when_no_matched_product_code(monkeypatch):
    rows = [{
        "code": "ad-x",
        "ad_account_id": "1253003326160754",
        "matched_product_code": None,
        "spend_usd": 100.0,
        "purchase_value_usd": 0.0,
        "roas_purchase": 0.0,
    }]

    def fake_query(sql, args=()):
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    stats = oa.fill_purchase_value_from_orders(
        rows,
        level="ad",
        start_date=__import__("datetime").date(2026, 4, 25),
        end_date=__import__("datetime").date(2026, 5, 7),
        accounts_loader=lambda: [_FakeAccount("1253003326160754", ("omurio",))],
    )
    assert stats["fallback_row_count"] == 0
    assert rows[0]["purchase_value_usd"] == 0.0
    assert rows[0]["purchase_value_source"] == "meta"


def test_fill_purchase_value_uses_account_store_codes_in_revenue_query(monkeypatch):
    """订单营收查询必须按账户绑定的 store_codes 过滤，不能写死。"""
    rows = [{
        "code": "ad-x",
        "ad_account_id": "1253003326160754",
        "matched_product_code": "abc",
        "spend_usd": 100.0,
        "purchase_value_usd": 0.0,
        "roas_purchase": 0.0,
    }]

    revenue_args_seen = {}

    def fake_query(sql, args=()):
        if "GROUP BY ad_account_id, LOWER(matched_product_code)" in sql:
            return [{
                "ad_account_id": "1253003326160754",
                "product_code": "abc",
                "group_spend": 100.0,
                "group_purchase": 0.0,
            }]
        if "FROM dianxiaomi_order_lines" in sql:
            revenue_args_seen["sql"] = sql
            revenue_args_seen["args"] = args
            return [{"product_code_lc": "abc", "revenue": 200.0}]
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    oa.fill_purchase_value_from_orders(
        rows,
        level="ad",
        start_date=__import__("datetime").date(2026, 4, 25),
        end_date=__import__("datetime").date(2026, 5, 7),
        accounts_loader=lambda: [_FakeAccount("1253003326160754", ("omurio",))],
    )
    args = revenue_args_seen["args"]
    # 第一个绑定参数 = store_codes 元素 → "omurio"
    assert "omurio" in args
    # 不允许把 newjoy 当做 omurio 账户的兜底来源
    assert "newjoy" not in args


def test_get_ads_level_list_data_quality_signals_fallback_used(monkeypatch):
    """端到端：list 层 Omurio 整组缺购买金额时，data_quality.status = 'fallback_used'。"""
    fake_query, _ = _capture_calls({
        "GROUP BY normalized_ad_code, ad_account_id": [
            {
                "code": "fully-automatic-water-blaster(2026.03.31).mp4",
                "name": "Water Blaster Ad",
                "ad_account_id": "1253003326160754",
                "ad_account_name": "Omurio",
                "matched_product_code": "fully-automatic-water-blaster",
                "spend_usd": 1000.0, "purchase_value_usd": 0.0,
                "result_count": 0, "day_count": 5,
                "roas_purchase": None,
            },
        ],
        "GROUP BY ad_account_id, LOWER(matched_product_code)": [{
            "ad_account_id": "1253003326160754",
            "product_code": "fully-automatic-water-blaster",
            "group_spend": 1000.0,
            "group_purchase": 0.0,
        }],
        "FROM dianxiaomi_order_lines": [{
            "product_code_lc": "fully-automatic-water-blaster",
            "revenue": 800.0,
        }],
    })

    def fake_query_one(sql, args=()):
        return {"total": 1}

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(oa, "query_one", fake_query_one)
    real_fallback = oa.fill_purchase_value_from_orders
    monkeypatch.setattr(
        oa,
        "fill_purchase_value_from_orders",
        lambda rows, **kwargs: real_fallback(
            rows,
            **{**kwargs, "accounts_loader": lambda: [_FakeAccount("1253003326160754", ("omurio",))]},
        ),
    )

    result = oa.get_ads_level_list("ad", start_date="2026-04-25", end_date="2026-05-07")
    assert result["data_quality"]["status"] == "fallback_used"
    assert result["data_quality"]["purchase_value"]["fallback_row_count"] == 1
    assert result["data_quality"]["purchase_value"]["fallback_revenue_total_usd"] == 800.0
    assert result["rows"][0]["purchase_value_usd"] == 800.0
    assert result["rows"][0]["purchase_value_source"] == "order_fallback"


def test_get_ads_level_detail_applies_order_fallback_to_historical_days(monkeypatch):
    """详情页历史天数（非 realtime）也应套用兜底；realtime today 走 Meta 实时表，不动。"""
    today = oa.current_meta_business_date()
    yesterday = today - __import__("datetime").timedelta(days=1)

    daily_rows = [
        {
            "meta_business_date": yesterday,
            "name": "Water Blaster Ad",
            "ad_account_id": "1253003326160754",
            "ad_account_name": "Omurio",
            "matched_product_code": "fully-automatic-water-blaster",
            "spend_usd": 200.0,
            "purchase_value_usd": 0.0,
            "result_count": 0,
            "raw_json": "{}",
        },
    ]

    def fake_query(sql, args=()):
        if "FROM meta_ad_daily_ad_metrics" in sql and "matched_product_code" not in sql.split("FROM")[0]:
            # legacy fall-through: not in current spec
            return []
        if "GROUP BY ad_account_id, LOWER(matched_product_code)" in sql:
            return [{
                "ad_account_id": "1253003326160754",
                "product_code": "fully-automatic-water-blaster",
                "group_spend": 200.0,
                "group_purchase": 0.0,
            }]
        if "FROM dianxiaomi_order_lines" in sql:
            return [{"product_code_lc": "fully-automatic-water-blaster", "revenue": 250.0}]
        if "FROM meta_ad_daily_ad_metrics" in sql:
            return daily_rows
        return []

    def fake_query_one(sql, args=()):
        return None

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(oa, "query_one", fake_query_one)
    real_fallback = oa.fill_purchase_value_from_orders
    monkeypatch.setattr(
        oa,
        "fill_purchase_value_from_orders",
        lambda rows, **kwargs: real_fallback(
            rows,
            **{**kwargs, "accounts_loader": lambda: [_FakeAccount("1253003326160754", ("omurio",))]},
        ),
    )

    result = oa.get_ads_level_detail("ad", code="water-blaster",
                                      start_date=yesterday.isoformat(),
                                      end_date=yesterday.isoformat())
    yesterday_row = next(r for r in result["rows"] if r["date"] == yesterday.isoformat())
    assert yesterday_row["purchase_value_usd"] == 250.0
    assert yesterday_row["purchase_value_source"] == "order_fallback"
    assert result["data_quality"]["status"] == "fallback_used"


def test_ads_list_route_passes_params_to_data_layer(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list(
        level,
        start_date,
        end_date,
        page,
        page_size,
        sort_by,
        sort_dir,
        q,
        ad_account_id,
        parent_level=None,
        parent_code=None,
    ):
        captured.update({
            "level": level, "start_date": start_date, "end_date": end_date,
            "page": page, "page_size": page_size,
            "sort_by": sort_by, "sort_dir": sort_dir, "q": q,
            "ad_account_id": ad_account_id,
            "parent_level": parent_level,
            "parent_code": parent_code,
        })
        return {"level": level, "rows": [], "total": 0, "page": page, "page_size": page_size, "has_more": False}

    monkeypatch.setattr(oa, "get_ads_level_list", fake_list)
    response = authed_client_no_db.get(
        "/order-analytics/ads/list?level=adset"
        "&start_date=2026-04-01&end_date=2026-04-14"
        "&page=2&page_size=25&sort_by=roas_purchase&sort_dir=asc"
        "&q=water-blaster&ad_account_id=1253003326160754"
        "&parent_level=campaign&parent_code=sonic-lens-refresher-rjc"
    )
    assert response.status_code == 200, response.data
    assert captured["level"] == "adset"
    assert captured["start_date"] == "2026-04-01"
    assert captured["end_date"] == "2026-04-14"
    assert captured["page"] == 2
    assert captured["page_size"] == 25
    assert captured["sort_by"] == "roas_purchase"
    assert captured["sort_dir"] == "asc"
    assert captured["q"] == "water-blaster"
    assert captured["ad_account_id"] == "1253003326160754"
    assert captured["parent_level"] == "campaign"
    assert captured["parent_code"] == "sonic-lens-refresher-rjc"


def test_ads_detail_route_passes_account_to_data_layer(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_detail(level, code, start_date, end_date, ad_account_id):
        captured.update({
            "level": level,
            "code": code,
            "start_date": start_date,
            "end_date": end_date,
            "ad_account_id": ad_account_id,
        })
        return {"level": level, "code": code, "rows": []}

    monkeypatch.setattr(oa, "get_ads_level_detail", fake_detail)
    response = authed_client_no_db.get(
        "/order-analytics/ads/detail?level=ad&code=water-blaster"
        "&start_date=2026-05-17&end_date=2026-05-17"
        "&ad_account_id=act_1253003326160754"
    )

    assert response.status_code == 200, response.data
    assert captured == {
        "level": "ad",
        "code": "water-blaster",
        "start_date": "2026-05-17",
        "end_date": "2026-05-17",
        "ad_account_id": "act_1253003326160754",
    }


def test_get_ads_level_list_includes_realtime_today_for_campaign(monkeypatch):
    today = oa.current_meta_business_date()
    captured_queries = []

    def fake_query_one(sql, args=()):
        return {"total": 1}

    def fake_query(sql, args=()):
        captured_queries.append({"sql": sql, "args": args})
        # broken-groups query returns empty list
        if "GROUP BY ad_account_id, LOWER(matched_product_code)" in sql:
            return []
        return [
            {
                "code": "abc-rjc", "name": "Realtime Glow Set",
                "ad_account_id": "1234", "ad_account_name": "newjoyloo",
                "matched_product_code": None,  # From realtime UNION it is NULL
                "spend_usd": 150.0, "purchase_value_usd": 300.0,
                "result_count": 8, "day_count": 1,
                "roas_purchase": 2.0,
            },
        ]

    # Mock resolve_ad_product_match to return matched product
    monkeypatch.setattr(oa.meta_ads, "resolve_ad_product_match", lambda name: {"product_code": "abc-rjc"})
    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "query", fake_query)

    # end_date is today, level is campaign, which triggers UNION
    result = oa.get_ads_level_list("campaign", start_date=today.isoformat(), end_date=today.isoformat())

    assert result["level"] == "campaign"
    assert result["rows"][0]["code"] == "abc-rjc"
    # Auto product association filling verification:
    assert result["rows"][0]["matched_product_code"] == "abc-rjc"
    assert result["rows"][0]["spend_usd"] == 150.0

    # Ensure SQL contained UNION ALL and selected from both daily & realtime tables
    union_query = next(q for q in captured_queries if "UNION ALL" in q["sql"])
    assert "meta_ad_realtime_daily_campaign_metrics" in union_query["sql"]
    assert "meta_ad_daily_campaign_metrics" in union_query["sql"]


def test_get_ads_level_list_adset_and_ad_include_realtime_today(monkeypatch):
    today = oa.current_meta_business_date()
    captured_queries: list[dict] = []

    def fake_query_one(sql, args=()):
        captured_queries.append({"sql": sql, "args": args})
        return {"total": 0}

    def fake_query(sql, args=()):
        captured_queries.append({"sql": sql, "args": args})
        return []

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "query", fake_query)

    cases = [
        ("adset", "meta_ad_daily_adset_metrics", "meta_ad_realtime_daily_adset_metrics",
         "normalized_adset_code", "adset_name"),
        ("ad", "meta_ad_daily_ad_metrics", "meta_ad_realtime_daily_ad_metrics",
         "normalized_ad_code", "ad_name"),
    ]
    for level, daily_table, realtime_table, code_col, name_col in cases:
        captured_queries.clear()
        result = oa.get_ads_level_list(
            level,
            start_date=today.isoformat(),
            end_date=today.isoformat(),
        )

        assert result["level"] == level
        assert result["data_quality"]["status"] == "ok"
        union_sql = next(q["sql"] for q in captured_queries if "UNION ALL" in q["sql"])
        assert daily_table in union_sql
        assert realtime_table in union_sql
        assert f"m.{code_col} AS code" in union_sql
        assert f"m.{name_col} AS name" in union_sql
