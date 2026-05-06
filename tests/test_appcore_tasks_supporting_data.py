from __future__ import annotations


def test_list_enabled_target_languages_excludes_english_and_uppercases(monkeypatch):
    from appcore import tasks

    captured = {}

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"code": "de"}, {"code": "ja"}]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_enabled_target_languages() == [
        {"code": "DE"},
        {"code": "JA"},
    ]
    assert "FROM media_languages" in captured["sql"]
    assert "enabled=1" in captured["sql"]
    assert "code <> 'en'" in captured["sql"]
    assert captured["args"] == ()


def test_list_product_english_items_filters_product_and_preserves_payload(monkeypatch):
    from appcore import tasks

    captured = {}

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {"id": 9, "filename": "source-a.mp4", "object_key": "unused/a.mp4"},
            {"id": 8, "filename": "source-b.mp4", "object_key": "unused/b.mp4"},
        ]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_product_english_items(417) == [
        {"id": 9, "filename": "source-a.mp4"},
        {"id": 8, "filename": "source-b.mp4"},
    ]
    assert "FROM media_items" in captured["sql"]
    assert "product_id=%s" in captured["sql"]
    assert "lang='en'" in captured["sql"]
    assert "deleted_at IS NULL" in captured["sql"]
    assert captured["args"] == (417,)
