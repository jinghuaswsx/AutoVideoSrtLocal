from __future__ import annotations

import io

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

    unmatched_row = summary["rows"][1]
    assert unmatched_row["product_id"] is None
    assert unmatched_row["display_name"] == "Unmatched Campaign"
    assert summary["unmatched"][0]["campaign_name"] == "Unmatched Campaign"
    assert all("GROUP BY m.product_id, display_name, product_code" not in sql for sql in queries)


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


def test_ads_stats_card_shows_report_roas(authed_client_no_db):
    response = authed_client_no_db.get("/order-analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "statCard('购物转化价值', fmtMoney(s.total_purchase_value_usd))" in body
    assert "statCard('ROAS', fmtAdRoas(s.total_purchase_value_usd, s.total_spend_usd))" in body
    assert "function fmtAdRoas(purchaseValue, spend)" in body


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
