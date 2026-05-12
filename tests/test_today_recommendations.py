from __future__ import annotations

from pathlib import Path

import appcore.today_recommendations as tr


def test_today_recommendations_migration_declares_tables():
    body = Path("db/migrations/2026_05_12_xuanpin_today_recommendations.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS xuanpin_today_recommendation_runs" in body
    assert "CREATE TABLE IF NOT EXISTS xuanpin_today_recommendations" in body
    assert "UNIQUE KEY uk_reco_date_candidate" in body
    assert "recommended_countries JSON" in body


def test_candidate_key_is_stable_and_material_specific():
    first = tr.candidate_key_for("abc", "videos/a.mp4", "a.mp4")
    second = tr.candidate_key_for("abc", "videos/a.mp4", "a.mp4")
    other = tr.candidate_key_for("abc", "videos/b.mp4", "b.mp4")

    assert first == second
    assert first != other
    assert len(first) == 64


def test_list_recommendations_hides_adopted_and_serializes(monkeypatch):
    captured = {}

    monkeypatch.setattr(tr, "guard_against_windows_local_mysql", lambda: None)
    monkeypatch.setattr(tr, "latest_recommendation_date", lambda: "2026-05-12")

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {
                "id": 1,
                "recommendation_date": "2026-05-12",
                "ranking_snapshot_date": "2026-05-11",
                "recommended_countries": '["de","fr"]',
                "ai_detail_json": '{"base_score": 88}',
                "mk_video_metadata_json": '{"video_path":"x.mp4"}',
                "created_at": None,
                "updated_at": None,
                "adopted_at": None,
            }
        ]

    monkeypatch.setattr(tr, "query", fake_query)

    rows = tr.list_recommendations(limit=100)

    assert "status<>%s" in captured["sql"]
    assert captured["args"] == ("2026-05-12", tr.STATUS_ADOPTED, 100)
    assert rows[0]["recommended_countries"] == ["de", "fr"]
    assert rows[0]["ai_detail"] == {"base_score": 88}
    assert rows[0]["mk_video_metadata"] == {"video_path": "x.mp4"}


def test_adopt_recommendations_creates_task_and_marks_adopted(monkeypatch):
    calls = {}

    monkeypatch.setattr(tr, "guard_against_windows_local_mysql", lambda: None)
    monkeypatch.setattr("appcore.new_product_review._resolve_translator", lambda translator_id: {"id": translator_id})
    monkeypatch.setattr(tr, "_ensure_product", lambda row, translator_id: 101)
    monkeypatch.setattr(tr, "_ensure_media_item", lambda row, product_id, translator_id: 202)

    def fake_query(sql, args=()):
        return [
            {
                "id": 7,
                "status": tr.STATUS_PENDING,
                "recommended_countries": '["de","fr"]',
                "mk_video_metadata_json": "{}",
            }
        ]

    def fake_create_parent_task(**kwargs):
        calls["task"] = kwargs
        return 303

    def fake_execute(sql, args=()):
        calls["execute"] = (sql, args)
        return 1

    monkeypatch.setattr(tr, "query", fake_query)
    monkeypatch.setattr(tr.tasks, "create_parent_task", fake_create_parent_task)
    monkeypatch.setattr(tr, "execute", fake_execute)

    result = tr.adopt_recommendations(
        recommendation_ids=[7],
        translator_id=5,
        actor_user_id=1,
    )

    assert result["adopted"][0]["task_id"] == 303
    assert calls["task"] == {
        "media_product_id": 101,
        "media_item_id": 202,
        "countries": ["DE", "FR"],
        "translator_id": 5,
        "created_by": 1,
    }
    assert calls["execute"][1][0] == tr.STATUS_ADOPTED
