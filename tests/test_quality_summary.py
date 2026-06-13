from __future__ import annotations


def test_summarize_recent_groups_by_project_type_and_target_lang():
    from appcore import quality_assessment as qa

    captured = {}

    def fake_query(sql, args=None):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {
                "project_type": "omni_translate",
                "target_lang": "de",
                "translation_score": 80,
                "tts_score": 90,
                "translation_dimensions": '{"ending_integrity": 80}',
            },
            {
                "project_type": "omni_translate",
                "target_lang": "de",
                "translation_score": 68,
                "tts_score": 88,
                "translation_dimensions": '{"ending_integrity": 80}',
            },
            {
                "project_type": "omni_translate_v2",
                "target_lang": "fr",
                "translation_score": 88,
                "tts_score": 92,
                "translation_dimensions": '{"ending_integrity": 55}',
            },
        ]

    rows = qa.summarize_recent(days=30, query_func=fake_query)

    assert "translation_quality_assessments" in captured["sql"]
    assert "JSON_EXTRACT" in captured["sql"]
    assert captured["args"] == (30,)
    assert rows == [
        {
            "project_type": "omni_translate",
            "target_lang": "de",
            "n": 2,
            "avg_translation": 74.0,
            "avg_tts": 89.0,
            "red_count": 1,
            "red_rate": 50.0,
        },
        {
            "project_type": "omni_translate_v2",
            "target_lang": "fr",
            "n": 1,
            "avg_translation": 88.0,
            "avg_tts": 92.0,
            "red_count": 1,
            "red_rate": 100.0,
        },
    ]


def test_admin_quality_summary_route_renders_for_admin(authed_client_no_db, monkeypatch):
    from web.routes import admin_quality_assessment as route

    monkeypatch.setattr(
        route.quality_assessment,
        "summarize_recent",
        lambda days=30: [
            {
                "project_type": "omni_translate",
                "target_lang": "de",
                "n": 2,
                "avg_translation": 74.0,
                "avg_tts": 89.0,
                "red_count": 1,
                "red_rate": 50.0,
            }
        ],
    )

    resp = authed_client_no_db.get("/admin/translation-quality")

    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "translation-quality-summary" in html
    assert "omni_translate" in html
    assert "de" in html
    assert "50.0%" in html


def test_admin_quality_summary_route_rejects_non_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/admin/translation-quality")

    assert resp.status_code in {302, 403}
    assert resp.status_code != 200
