from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_video_workbench_page_route_renders_first_version(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.medias.pages.medias.get_product",
        lambda pid: {"id": pid, "name": "Demo Product", "product_code": "demo-rjc"},
    )
    monkeypatch.setattr(
        "web.routes.medias.pages._routes_module",
        lambda: SimpleNamespace(_can_access_product=lambda product: True),
    )

    response = authed_client_no_db.get("/medias/product/video_workbench/321")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "视频素材决策台 - Demo Product" in html
    assert "/medias/api/product/${productId}/video-workbench" in html
    assert "/medias/api/product/${productId}/video-workbench/ad-detail" in html
    assert "fetchJson('/mk-import/video'" in html
    assert "const endpoint = '/tasks/api/parent';" in html
    assert "fetchJson(endpoint" in html
    assert "先加入素材库后才能创建小语种任务" in html
    assert "media_product_id: xiaoContext.productId" in html
    assert "media_item_id: xiaoContext.itemId" in html
    assert "X-CSRFToken" in html
    assert "vwAdModal" in html
    assert "vwImportProgressModal" in html
    assert "vwXiaoModal" in html
    assert "历史匹配本地素材" in html
    assert "翻译版本" in html
    assert "订单量情况" in html
    assert "AI 8国评估建议" in html
    assert 'data-action="ai-start"' in html
    assert "translated_versions" in html
    assert "target_country_versions" in html


def test_video_workbench_import_flow_matches_mk_progress_contract():
    template = (ROOT / "web" / "templates" / "medias_product_video_workbench.html").read_text(encoding="utf-8")

    assert "docs/superpowers/specs/2026-06-08-medias-workbench-mk-card-flow-alignment.md" in template
    assert "{key: 'productOwner', title: '选择产品负责人'" in template
    assert "{key: 'domains', title: '选择发布域名'" in template
    assert "{key: 'next', title: '后续任务入口'" in template
    assert "product_owner_id" in template
    assert "data-vw-import-domain-save" in template
    assert "enabled_domain_ids" in template
    assert "下一步：创建小语种任务" in template
    assert "发布域名已确认，可以创建小语种任务" in template
    assert "发布域名确认后创建小语种任务" in template
    assert "setImportActionVisible('next', true)" in template


def test_video_workbench_small_language_modal_matches_mk_contract():
    template = (ROOT / "web" / "templates" / "medias_product_video_workbench.html").read_text(encoding="utf-8")

    assert 'id="vwXiaoProductImage"' in template
    assert 'id="vwXiaoProductLinkStatus"' in template
    assert 'id="vwXiaoProductCode"' in template
    assert 'id="vwXiaoSpends"' in template
    assert "产品负责人用于素材归属" in template
    assert "小语种翻译负责人用于当前语言任务，可与产品负责人不同" in template
    assert "data-vw-force-lang" in template
    assert "强制创建" in template
    assert "紧急任务" in template
    assert "language_assignments: languageAssignments(selection)" in template
    assert "translator_id: selection.translatorId" in template
    assert "raw_processor_id: selection.rawProcessorId" in template
    assert "force: !!selection.force" in template
    assert "is_urgent: !!selection.isUrgent" in template
    assert "任务已创建，父任务 #" in template
    assert "请求失败" in template
    assert "打开任务 #" in template


def test_video_workbench_page_requires_login(authed_client_no_db):
    raw_client = authed_client_no_db.application.test_client()

    response = raw_client.get("/medias/product/video_workbench/321")

    assert response.status_code == 302


def test_product_list_has_separate_supplement_and_workbench_entries():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    template = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "data-material-workbench" in script
    assert "素材工作台" in script
    assert "window.open(`/medias/product/addvideo/${pid}`, '_blank');" in script
    assert "window.open(`/medias/product/video_workbench/${pid}`, '_blank');" in script
    assert ".material-workbench-btn" in template


def test_build_product_video_workbench_dedupes_language_ad_rows(monkeypatch):
    from web.routes.medias import material_supplement as route

    monkeypatch.setattr(
        "appcore.mingkong_materials._enrich_material_yesterday_delta",
        lambda rows, **kwargs: None,
    )

    def fake_query(sql, params=None):
        if "FROM media_products" in sql:
            return [{"id": 321, "name": "Demo", "product_code": "demo-rjc"}]
        if "FROM mingkong_material_daily_snapshots s" in sql:
            return [
                {
                    "material_key": "mk-1",
                    "video_path": "/video/demo.mp4",
                    "video_name": "demo.mp4",
                    "video_image_path": "/cover/demo.jpg",
                    "cumulative_90_spend": "120.00",
                    "video_ads_count": 3,
                    "video_author": "maker",
                    "video_upload_time": "2026-06-01",
                    "yesterday_spend_delta": "12.00",
                    "snapshot_date": "2026-06-06",
                    "snapshot_at": datetime(2026, 6, 6, 10, 0, 0),
                    "mk_product_id": 7,
                    "mk_product_name": "Demo CN",
                    "mk_product_link": "https://example.test/products/demo",
                    "main_image": "",
                }
            ]
        if "FROM media_items" in sql and "WHERE product_id = %s" in sql:
            return [
                {"id": 10, "lang": "en", "filename": "demo.mp4", "display_name": "demo.mp4", "object_key": "items/demo.mp4", "task_id": None},
                {"id": 11, "lang": "de", "filename": "demo-de-1.mp4", "display_name": "de1", "object_key": "items/de1.mp4", "task_id": 101},
                {"id": 12, "lang": "de", "filename": "demo-de-2.mp4", "display_name": "de2", "object_key": "items/de2.mp4", "task_id": 102},
                {"id": 13, "lang": "fr", "filename": "demo-fr.mp4", "display_name": "fr", "object_key": "items/fr.mp4", "task_id": None},
            ]
        if "FROM media_item_mk_bindings" in sql:
            return [{"media_item_id": 10, "mk_video_path": "/video/demo.mp4"}]
        if "FROM media_product_lang_ad_summary_cache" in sql:
            return [
                {"lang": "de", "ad_spend_usd": "210.00", "active_7d_ad_spend_usd": "5.00", "purchase_value_usd": "273.00", "ad_roas": "1.3", "pushed_video_count": 2, "item_count": 2},
                {"lang": "fr", "ad_spend_usd": "90.00", "active_7d_ad_spend_usd": "0.00", "purchase_value_usd": "0.00", "ad_roas": "0", "pushed_video_count": 1, "item_count": 1},
            ]
        if "FROM media_product_ad_summary_cache" in sql:
            return [{"ad_spend_usd": "300.00", "active_7d_ad_spend_usd": "5.00", "overall_roas": "0.91", "delivery_status": "active"}]
        raise AssertionError(sql)

    payload = route.build_product_video_workbench(321, query_fn=fake_query)

    assert payload["summary"]["total_mk_videos"] == 1
    assert payload["summary"]["in_library"] == 1
    assert payload["lang_coverage"]["de"]["item_count"] == 2
    card = payload["cards"][0]
    assert card["media_item_id"] == 10
    assert card["library_match_source"] == "media_item_mk_bindings"
    lang_codes = [row["lang"] for row in card["lang_ad_summary"]]
    assert lang_codes.count("de") == 1
    assert lang_codes.count("fr") == 1
    de_row = next(row for row in card["lang_ad_summary"] if row["lang"] == "de")
    assert de_row["media_item_ids"] == [11, 12]
    assert de_row["item_count"] == 2


def test_build_product_video_workbench_matches_legacy_library_item_by_exact_filename(monkeypatch):
    from web.routes.medias import material_supplement as route

    monkeypatch.setattr(
        "appcore.mingkong_materials._enrich_material_yesterday_delta",
        lambda rows, **kwargs: None,
    )

    def fake_query(sql, params=None):
        if "FROM media_products" in sql:
            return [{"id": 320, "name": "Legacy Product", "product_code": "legacy-rjc"}]
        if "FROM mingkong_material_daily_snapshots s" in sql:
            return [
                {
                    "material_key": "mk-legacy",
                    "video_path": "uploads2/202510/1761711986.mp4",
                    "video_name": "2025.10.29-手机屏幕放大器-原素材-补充素材-指派-G-苏齐齐.mp4",
                    "video_image_path": "/cover/legacy.jpg",
                    "cumulative_90_spend": "8060.00",
                    "video_ads_count": 10,
                    "video_author": "苏齐齐",
                    "video_upload_time": "2025-10-29",
                    "yesterday_spend_delta": "0.00",
                    "snapshot_date": "2026-06-06",
                    "snapshot_at": datetime(2026, 6, 6, 10, 0, 0),
                    "mk_product_id": 3200,
                    "mk_product_name": "手机屏幕放大器",
                    "mk_product_link": "",
                    "main_image": "",
                }
            ]
        if "FROM media_items" in sql and "WHERE product_id = %s" in sql:
            return [
                {
                    "id": 191,
                    "lang": "en",
                    "filename": "2025.10.29-手机屏幕放大器-原素材-补充素材-指派-G-苏齐齐.mp4",
                    "display_name": "2025.10.29-手机屏幕放大器-原素材-补充素材-指派-G-苏齐齐.mp4",
                    "object_key": "legacy/191.mp4",
                    "task_id": None,
                    "created_at": datetime(2026, 4, 17, 17, 7, 34),
                },
                {
                    "id": 193,
                    "lang": "de",
                    "filename": "2026.04.16-手机屏幕放大器-原素材-补充素材(德语)-指派-蔡靖华.mp4",
                    "display_name": "2026.04.16-手机屏幕放大器-原素材-补充素材(德语)-指派-蔡靖华.mp4",
                    "object_key": "legacy/193.mp4",
                    "task_id": None,
                    "created_at": datetime(2026, 4, 17, 17, 25, 12),
                },
            ]
        if "FROM media_item_mk_bindings" in sql:
            return []
        if "FROM media_product_lang_ad_summary_cache" in sql:
            return [{"lang": "de", "ad_spend_usd": "2348.08", "active_7d_ad_spend_usd": "0.00", "purchase_value_usd": "3257.30", "ad_roas": "1.387", "pushed_video_count": 0, "item_count": 1}]
        if "FROM media_product_ad_summary_cache" in sql:
            return [{"ad_spend_usd": "3583.78", "active_7d_ad_spend_usd": "10.00", "overall_roas": "1.6", "delivery_status": "active"}]
        raise AssertionError(sql)

    payload = route.build_product_video_workbench(320, query_fn=fake_query)

    assert payload["summary"]["total_mk_videos"] == 1
    assert payload["summary"]["in_library"] == 1
    card = payload["cards"][0]
    assert card["in_library"] is True
    assert card["media_item_id"] == 191
    assert card["library_match_source"] == "media_items_legacy_product_scope"
    assert card["library_match_reason"] == "video_name:filename"
    assert card["bound_item"]["match_source"] == "media_items_legacy_product_scope"


def test_build_product_video_workbench_payload_includes_versions_orders_and_ai(monkeypatch):
    from web.routes.medias import material_supplement as route

    monkeypatch.setattr(
        "appcore.mingkong_materials._enrich_material_yesterday_delta",
        lambda rows, **kwargs: None,
    )

    detail = {
        "evaluated_at": "2026-06-08T08:00:00Z",
        "countries": [
            {"lang": "de", "country": "德国", "is_suitable": True, "score": 86, "recommendation": "做", "summary": "德国建议做"},
            {"lang": "ja", "country": "日本", "is_suitable": False, "score": 58, "recommendation": "不做", "summary": "日本谨慎"},
        ],
    }

    def fake_query(sql, params=None):
        if "FROM media_products" in sql:
            return [{
                "id": 321,
                "name": "Demo",
                "product_code": "demo-rjc",
                "ai_score": "72",
                "ai_evaluation_result": "部分适合推广",
                "ai_evaluation_detail": json.dumps(detail, ensure_ascii=False),
            }]
        if "FROM mingkong_material_daily_snapshots s" in sql:
            return [{
                "material_key": "mk-1",
                "video_path": "/video/demo.mp4",
                "video_name": "demo.mp4",
                "video_image_path": "/cover/demo.jpg",
                "cumulative_90_spend": "120.00",
                "video_ads_count": 3,
                "video_author": "maker",
                "video_upload_time": "2026-06-01",
                "yesterday_spend_delta": "12.00",
                "snapshot_date": "2026-06-06",
                "snapshot_at": datetime(2026, 6, 6, 10, 0, 0),
                "mk_product_id": 7,
                "mk_product_name": "Demo CN",
                "mk_product_link": "https://example.test/products/demo",
                "main_image": "",
            }]
        if "FROM media_items" in sql and "WHERE product_id = %s" in sql:
            return [
                {"id": 10, "product_id": 321, "lang": "en", "filename": "demo.mp4", "display_name": "demo.mp4", "object_key": "items/demo.mp4", "task_id": None, "source_raw_id": 500, "source_ref_id": None, "auto_translated": 0},
                {"id": 11, "product_id": 321, "lang": "de", "filename": "demo-de.mp4", "display_name": "de", "object_key": "items/de.mp4", "task_id": 101, "source_raw_id": 500, "source_ref_id": None, "auto_translated": 0},
                {"id": 12, "product_id": 321, "lang": "pt", "filename": "demo-pt.mp4", "display_name": "pt", "object_key": "items/pt.mp4", "task_id": 102, "source_raw_id": 500, "source_ref_id": None, "auto_translated": 0},
            ]
        if "FROM media_item_mk_bindings" in sql:
            return [{"media_item_id": 10, "mk_video_path": "/video/demo.mp4"}]
        if "FROM media_product_lang_ad_summary_cache" in sql:
            return []
        if "FROM media_product_ad_summary_cache" in sql:
            return [{"ad_spend_usd": "0", "active_7d_ad_spend_usd": "0", "overall_roas": None, "delivery_status": "never"}]
        raise AssertionError(sql)

    def attach(rows):
        for row in rows:
            perf = route._empty_ad_performance()
            if row["lang"] == "de":
                perf.update({"total_spend_usd": 40.0, "last_7d_spend_usd": 10.0, "purchase_value_usd": 80.0, "roas": 2.0})
            if row["lang"] == "pt":
                perf.update({"total_spend_usd": 20.0, "last_30d_spend_usd": 20.0, "purchase_value_usd": 10.0, "roas": 0.5})
            row["ad_performance"] = perf

    def order_report(product_id):
        empty = route._empty_order_stats_row()
        return {
            "product_id": product_id,
            "total": {**empty, "today_orders": 3, "last_30d_orders": 21},
            "by_lang": {
                "de": {**empty, "today_orders": 2, "last_7d_orders": 7},
                "pt": {**empty, "yesterday_orders": 1, "last_30d_orders": 5},
            },
        }

    payload = route.build_product_video_workbench(
        321,
        query_fn=fake_query,
        attach_ad_plan_details_fn=attach,
        order_report_fn=order_report,
    )

    assert payload["ai_evaluation"]["target_country_codes"] == ["DE", "FR", "IT", "ES", "JP", "PT", "SE", "NL"]
    assert payload["ai_evaluation"]["evaluated_count"] == 2
    assert payload["ai_evaluation"]["pending_count"] == 6
    card = payload["cards"][0]
    assert card["mk_video"]["material_key"] == "mk-1"
    assert card["translation_summary"]["translated_country_codes"] == ["DE", "PT"]
    assert "NL" in card["translation_summary"]["missing_country_codes"]
    assert card["translated_versions"][0]["lang"] == "all"
    assert card["translated_versions"][0]["ad_performance"]["total_spend_usd"] == 60.0
    de_version = next(row for row in card["translated_versions"] if row["lang"] == "de")
    assert de_version["country_code"] == "DE"
    assert de_version["order_stats"]["today_orders"] == 2
    pt_target = next(row for row in card["target_country_versions"] if row["country_code"] == "PT")
    assert pt_target["status"] == "translated"


def test_build_video_workbench_ad_detail_summarizes_date_range():
    from web.routes.medias import material_supplement as route

    captured = {}

    def fake_query(sql, params=None):
        if "FROM media_products" in sql:
            return [{"id": 321, "name": "Demo", "product_code": "demo-rjc"}]
        if "FROM media_items" in sql:
            return [{"id": 10, "filename": "demo.mp4", "display_name": "Demo Display"}]
        if "FROM mingkong_material_daily_snapshots" in sql:
            return [{"video_name": "demo.mp4"}]
        if "FROM meta_ad_daily_ad_metrics m" in sql:
            captured["sql"] = sql
            captured["params"] = params
            return [
                {
                    "id": 1,
                    "ad_account_id": "act_1",
                    "ad_account_name": "Meta",
                    "activity_date": date(2026, 6, 5),
                    "report_date": date(2026, 6, 5),
                    "campaign_name": "demo-rjc",
                    "normalized_ad_code": "demo.mp4-de",
                    "ad_name": "Demo Ad demo.mp4",
                    "market_country": "DE",
                    "spend_usd": "100.00",
                    "purchase_value_usd": "150.00",
                    "result_count": 3,
                },
                {
                    "id": 2,
                    "ad_account_id": "act_1",
                    "ad_account_name": "Meta",
                    "activity_date": date(2026, 6, 4),
                    "report_date": date(2026, 6, 4),
                    "campaign_name": "demo-rjc",
                    "normalized_ad_code": "demo-display-fr",
                    "ad_name": "Demo Display FR",
                    "market_country": "FR",
                    "spend_usd": "50.00",
                    "purchase_value_usd": "25.00",
                    "result_count": 1,
                },
            ]
        raise AssertionError(sql)

    payload = route.build_video_workbench_ad_detail(
        321,
        {
            "media_item_id": "10",
            "video_path": "/video/demo.mp4",
            "date_from": "2026-06-01",
            "date_to": "2026-06-06",
        },
        query_fn=fake_query,
        today=date(2026, 6, 6),
    )

    assert "BETWEEN %s AND %s" in captured["sql"]
    assert captured["params"][1:3] == ["2026-06-01", "2026-06-06"]
    assert payload["summary"] == {
        "spend_usd": 150.0,
        "purchase_value_usd": 175.0,
        "result_count": 4,
        "roas": 1.1667,
        "matched_ad_count": 2,
    }
    assert payload["rows"][0]["match_reason"] == "filename"
    assert payload["rows"][0]["roas"] == 1.5


def test_build_video_workbench_ad_detail_falls_back_to_country_when_name_terms_do_not_match():
    from web.routes.medias import material_supplement as route

    ad_queries = []

    def fake_query(sql, params=None):
        if "FROM media_products" in sql:
            return [{"id": 704, "name": "Instant Snap Iodine Swabs", "product_code": "instant-snap-iodine-swabs-rjc"}]
        if "FROM media_items" in sql:
            return [
                {
                    "id": 1913,
                    "filename": "2026.05.26--一次性碘伏棉片-原素材-陈兆阳.mp4",
                    "display_name": "2026.05.26--一次性碘伏棉片-原素材-陈兆阳.mp4",
                }
            ]
        if "FROM mingkong_material_daily_snapshots" in sql:
            return [{"video_name": "2026.05.26--一次性碘伏棉片-原素材-陈兆阳.mp4"}]
        if "FROM media_product_lang_ad_summary_cache" in sql:
            return [{"lang": "de", "ad_spend_usd": "143.00"}]
        if "FROM meta_ad_daily_ad_metrics m" in sql:
            ad_queries.append((sql, params))
            if "UPPER(COALESCE(m.market_country" not in sql:
                return []
            return [
                {
                    "id": 77,
                    "ad_account_id": "act_de",
                    "ad_account_name": "Meta DE",
                    "activity_date": date(2026, 6, 5),
                    "report_date": date(2026, 6, 5),
                    "campaign_name": "instant-snap-iodine-swabs-rjc",
                    "normalized_ad_code": "instant-snap-iodine-swabs-rjc-de",
                    "ad_name": "Instant Snap Iodine Swabs DE",
                    "market_country": "DE",
                    "spend_usd": "143.00",
                    "purchase_value_usd": "386.10",
                    "result_count": 6,
                }
            ]
        raise AssertionError(sql)

    payload = route.build_video_workbench_ad_detail(
        704,
        {
            "media_item_id": "1913",
            "video_path": "/video/iodine-swabs.mp4",
            "date_from": "2026-05-10",
            "date_to": "2026-06-08",
        },
        query_fn=fake_query,
        today=date(2026, 6, 8),
    )

    assert len(ad_queries) == 2
    assert "LIKE %s" in ad_queries[0][0]
    assert "UPPER(COALESCE(m.market_country" in ad_queries[1][0]
    assert "DE" in ad_queries[1][1]
    assert payload["summary"] == {
        "spend_usd": 143.0,
        "purchase_value_usd": 386.1,
        "result_count": 6,
        "roas": 2.7,
        "matched_ad_count": 1,
    }
    assert payload["rows"][0]["market_country"] == "DE"
    assert payload["rows"][0]["match_reason"] == "product_lang_country_fallback"


def test_video_workbench_ad_detail_rejects_too_wide_date_range():
    from web.routes.medias import material_supplement as route

    with pytest.raises(ValueError, match="日期范围不能超过"):
        route.build_video_workbench_ad_detail(
            321,
            {"date_from": "2025-01-01", "date_to": "2026-06-06"},
            query_fn=lambda sql, params=None: [{"id": 321, "name": "Demo", "product_code": "demo-rjc"}],
            today=date(2026, 6, 6),
        )
