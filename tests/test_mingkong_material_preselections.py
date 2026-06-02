from __future__ import annotations

import json
from datetime import date, datetime

import pytest


def test_normalize_countries_dedupes_and_uppercases():
    from appcore import mingkong_material_preselections as svc

    assert svc.normalize_countries([" de ", "FR", "de", "", None]) == ["DE", "FR"]


def test_upsert_requires_at_least_one_country(monkeypatch):
    from appcore import mingkong_material_preselections as svc

    with pytest.raises(ValueError, match="至少选择一个语言"):
        svc.upsert_preselection(
            {"material_key": "a" * 64, "selected_countries": []},
            user_id=7,
        )


def test_upsert_persists_normalized_countries_and_trimmed_note(monkeypatch):
    from appcore import mingkong_material_preselections as svc

    calls = []

    def fake_execute(sql, args=()):
        calls.append((sql, args))
        return 1

    def fake_query_one(sql, args=()):
        assert "FROM mingkong_material_preselections" in sql
        assert args == ("a" * 64,)
        return {
            "material_key": "a" * 64,
            "product_code": "cool-widget",
            "mk_product_id": 901,
            "product_name": "可折叠收纳盒",
            "product_english_name": "Hat Organizer",
            "selected_countries_json": '["DE", "FR"]',
            "operator_note": "优先做德法",
            "processed_at": None,
        }

    monkeypatch.setattr(svc, "execute", fake_execute)
    monkeypatch.setattr(svc, "query_one", fake_query_one)

    result = svc.upsert_preselection(
        {
            "material_key": "a" * 64,
            "product_code": "cool-widget",
            "mk_product_id": 901,
            "product_name": "可折叠收纳盒",
            "product_english_name": "Hat Organizer",
            "selected_countries": ["de", "FR", "de"],
            "operator_note": "  优先做德法  ",
        },
        user_id=7,
    )

    assert "ON DUPLICATE KEY UPDATE" in calls[0][0]
    assert json.dumps(["DE", "FR"], ensure_ascii=False) in calls[0][1]
    assert "优先做德法" in calls[0][1]
    assert result["preselection"]["selected_countries"] == ["DE", "FR"]
    assert result["preselection"]["operator_note"] == "优先做德法"


def test_enrich_items_with_preselection(monkeypatch):
    from appcore import mingkong_material_preselections as svc

    monkeypatch.setattr(
        svc,
        "query",
        lambda sql, args=(): [
            {
                "material_key": "a" * 64,
                "selected_countries_json": '["DE", "FR"]',
                "operator_note": "优先做德法",
                "processed_at": None,
            }
        ],
    )

    items = [{"material_key": "a" * 64}, {"material_key": "b" * 64}]
    result = svc.enrich_items_with_preselection(items)

    assert result[0]["is_preselected"] is True
    assert result[0]["preselection"]["selected_countries"] == ["DE", "FR"]
    assert result[0]["preselection"]["operator_note"] == "优先做德法"
    assert result[1]["is_preselected"] is False
    assert result[1]["preselection"] is None


def test_list_preselections_filters_processed_and_imported(monkeypatch):
    from appcore import mingkong_material_preselections as svc

    captured = []

    def fake_query(sql, args=()):
        captured.append((sql, args))
        if "COUNT(*)" in sql:
            return [{"cnt": 1}]
        return [
            {
                "material_key": "a" * 64,
                "product_code": "cool-widget",
                "video_path": "uploads/a.mp4",
                "media_item_id": 44,
                "selected_countries_json": '["DE"]',
                "operator_note": "",
                "processed_at": "2026-06-02 10:00:00",
            }
        ]

    monkeypatch.setattr(svc, "query", fake_query)

    result = svc.list_preselections(
        {
            "library_status": "imported",
            "processed_status": "processed",
            "keyword": "cool",
            "page": "1",
            "page_size": "20",
        }
    )

    list_sql = captured[1][0]
    assert "media_item_id IS NOT NULL" in list_sql
    assert "processed_at IS NOT NULL" in list_sql
    assert "LIKE %s" in list_sql
    assert result["total"] == 1
    assert result["items"][0]["has_local_material_in_library"] is True
    assert result["items"][0]["preselection"]["processed_at"] == "2026-06-02 10:00:00"


def test_mark_processed_sets_task_marker(monkeypatch):
    from appcore import mingkong_material_preselections as svc

    calls = []

    monkeypatch.setattr(svc, "execute", lambda sql, args=(): calls.append((sql, args)) or 1)
    monkeypatch.setattr(
        svc,
        "query_one",
        lambda sql, args=(): {
            "material_key": "a" * 64,
            "selected_countries_json": '["DE"]',
            "operator_note": "",
            "processed_parent_task_id": 9001,
            "processed_by": 8,
            "processed_at": "2026-06-02 10:00:00",
        },
    )

    result = svc.mark_processed("a" * 64, parent_task_id=9001, user_id=8)

    assert "processed_at = CURRENT_TIMESTAMP" in calls[0][0]
    assert calls[0][1] == (8, 9001, "a" * 64)
    assert result["preselection"]["processed_parent_task_id"] == 9001


def test_yesterday_top100_response_enriches_preselection(monkeypatch):
    import appcore.mingkong_materials as mm

    monkeypatch.setattr(mm, "guard_against_windows_local_mysql", lambda: None)
    monkeypatch.setattr(
        mm,
        "_latest_snapshot_identity",
        lambda *args, **kwargs: {
            "snapshot_date": "2026-06-02",
            "snapshot_at": "2026-06-02 05:00:00",
            "snapshot_slot": "0500",
        },
    )
    monkeypatch.setattr(mm, "query_one", lambda sql, args=(): {"cnt": 1})
    monkeypatch.setattr(mm, "_run_summary", lambda snapshot, snapshot_at: None)
    monkeypatch.setattr(mm, "_enrich_cached_ad_statuses", lambda items: items)
    monkeypatch.setattr(mm, "enrich_and_fetch_english_titles", lambda items: items)

    def fake_query(sql, args=()):
        return [
            {
                "snapshot_date": date(2026, 6, 2),
                "snapshot_at": datetime(2026, 6, 2, 5, 0, 0),
                "snapshot_slot": "0500",
                "previous_snapshot_date": date(2026, 6, 1),
                "previous_snapshot_at": datetime(2026, 6, 1, 5, 0, 0),
                "previous_snapshot_slot": "0500",
                "comparison_interval_seconds": 86400,
                "ranking_snapshot_date": date(2026, 6, 2),
                "rank_position": 1,
                "display_position": 1,
                "material_key": "a" * 64,
                "product_code": "cool-widget",
                "product_name": "可折叠收纳盒",
                "product_url": "https://shop.example/products/cool-widget",
                "mk_product_id": 901,
                "mk_product_name": "可折叠收纳盒",
                "mk_product_link": "https://shop.example/products/cool-widget-rjc",
                "main_image": "uploads/main.jpg",
                "video_name": "winner.mp4",
                "video_path": "uploads/winner.mp4",
                "video_image_path": "uploads/winner.jpg",
                "current_cumulative_90_spend": 1000,
                "previous_cumulative_90_spend": 800,
                "yesterday_spend_delta": 200,
                "video_ads_count": 3,
                "mk_video_metadata_json": "{}",
                "is_new_material": 0,
                "is_new_top100_entry": 0,
                "created_at": None,
            }
        ]

    def fake_preselection_enrich(items, **kwargs):
        items[0]["is_preselected"] = True
        items[0]["preselection"] = {
            "selected_countries": ["DE", "FR"],
            "operator_note": "优先做德法",
            "processed_at": None,
        }
        return items

    monkeypatch.setattr(mm, "query", fake_query)
    monkeypatch.setattr(mm, "enrich_items_with_preselection", fake_preselection_enrich, raising=False)

    result = mm.list_yesterday_top100(page=1, page_size=20)

    assert result["items"][0]["is_preselected"] is True
    assert result["items"][0]["preselection"]["selected_countries"] == ["DE", "FR"]
    assert result["items"][0]["preselection"]["operator_note"] == "优先做德法"
