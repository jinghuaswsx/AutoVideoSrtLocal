from __future__ import annotations

import pytest


class _FakeLockCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, args=None):
        self._conn.statements.append((sql, args))

    def fetchone(self):
        if self._conn.fetchone_results:
            return self._conn.fetchone_results.pop(0)
        return None


class _FakeLockConn:
    def __init__(self, fetchone_results=None):
        self.fetchone_results = list(fetchone_results or [])
        self.statements = []
        self.closed = False

    def cursor(self):
        return _FakeLockCursor(self)

    def close(self):
        self.closed = True


def test_sync_video_cover_result_marks_auto_translated(monkeypatch):
    from appcore import bulk_translate_backfill as mod

    captured = {}
    monkeypatch.setattr(
        mod.medias,
        "upsert_raw_source_translation",
        lambda product_id, source_ref_id, lang, cover_object_key: captured.update(
            {
                "product_id": product_id,
                "source_ref_id": source_ref_id,
                "lang": lang,
                "cover_object_key": cover_object_key,
            }
        )
        or 901,
    )
    monkeypatch.setattr(
        mod,
        "mark_auto_translated",
        lambda table, target_id, source_ref_id, bulk_task_id: captured.update(
            {
                "table": table,
                "target_id": target_id,
                "bulk_task_id": bulk_task_id,
            }
        )
        or 1,
    )

    mod.sync_video_cover_result(
        parent_task_id="bt-1",
        product_id=77,
        lang="de",
        source_raw_id=301,
        cover_object_key="1/medias/77/cover_de_raw301.png",
    )

    assert captured["table"] == "media_raw_source_translations"
    assert captured["target_id"] == 901
    assert captured["bulk_task_id"] == "bt-1"


def test_sync_detail_images_result_marks_applied_rows(monkeypatch):
    from appcore import bulk_translate_backfill as mod

    monkeypatch.setattr(
        mod.task_state,
        "get",
        lambda task_id: {
            "_user_id": 1,
            "medias_context": {"product_id": 77, "target_lang": "de"},
        },
    )
    monkeypatch.setattr(
        mod,
        "apply_translated_detail_images_from_task",
        lambda task, allow_partial, user_id: {"applied_ids": [501, 502]},
    )
    monkeypatch.setattr(
        mod.medias,
        "list_detail_images",
        lambda product_id, lang: [
            {"id": 501, "source_detail_image_id": 101},
            {"id": 502, "source_detail_image_id": 102},
        ],
    )

    marked = []
    monkeypatch.setattr(
        mod,
        "mark_auto_translated",
        lambda table, target_id, source_ref_id, bulk_task_id: marked.append(
            (table, target_id, source_ref_id, bulk_task_id)
        )
        or 1,
    )

    applied = mod.sync_detail_images_result(
        parent_task_id="bt-1",
        child_task_id="img-1",
    )

    assert applied == [501, 502]
    assert marked == [
        ("media_product_detail_images", 501, 101, "bt-1"),
        ("media_product_detail_images", 502, 102, "bt-1"),
    ]


def test_sync_video_result_creates_item_and_marks_auto_translated(monkeypatch):
    from appcore import bulk_translate_backfill as mod

    lock_conn = _FakeLockConn([{"ok": 1}, {"released": 1}])
    monkeypatch.setattr(mod, "get_conn", lambda: lock_conn, raising=False)
    monkeypatch.setattr(mod, "query_one", lambda sql, args=None: None, raising=False)
    monkeypatch.setattr(
        mod.medias,
        "get_raw_source",
        lambda raw_id: {
            "id": raw_id,
            "user_id": 1,
            "display_name": "EN Raw",
            "duration_seconds": 90.0,
            "cover_object_key": "1/medias/77/raw_cover.png",
        },
    )
    created = {}
    monkeypatch.setattr(
        mod.medias,
        "create_item",
        lambda **kwargs: created.update(kwargs) or 701,
    )
    executed = []
    monkeypatch.setattr(mod, "execute", lambda sql, args=None: executed.append(args) or 1)
    marked = []
    monkeypatch.setattr(
        mod,
        "mark_auto_translated",
        lambda table, target_id, source_ref_id, bulk_task_id: marked.append(
            (table, target_id, source_ref_id, bulk_task_id)
        )
        or 1,
    )

    target_id = mod.sync_video_result(
        parent_task_id="bt-1",
        product_id=77,
        lang="de",
        source_raw_id=301,
        video_object_key="1/medias/77/de_result.mp4",
        cover_object_key="1/medias/77/de_cover.png",
    )

    assert target_id == 701
    assert created["object_key"] == "1/medias/77/de_result.mp4"
    assert created["cover_object_key"] == "1/medias/77/de_cover.png"
    assert executed == [(301, 701)]
    assert marked == [("media_items", 701, 301, "bt-1")]
    assert lock_conn.closed is True


def test_sync_video_result_reuses_existing_item_for_same_object_key(monkeypatch):
    from appcore import bulk_translate_backfill as mod

    lock_conn = _FakeLockConn([{"ok": 1}, {"released": 1}])
    monkeypatch.setattr(mod, "get_conn", lambda: lock_conn, raising=False)
    monkeypatch.setattr(
        mod,
        "query_one",
        lambda sql, args=None: {"id": 701, "cover_object_key": "old_cover.png"},
        raising=False,
    )
    monkeypatch.setattr(
        mod.medias,
        "get_raw_source",
        lambda raw_id: {
            "id": raw_id,
            "user_id": 1,
            "display_name": "EN Raw",
            "duration_seconds": 90.0,
            "file_size": 1234,
            "cover_object_key": "1/medias/77/raw_cover.png",
        },
    )
    monkeypatch.setattr(
        mod.medias,
        "create_item",
        lambda **kwargs: pytest.fail("duplicate sync should reuse existing media_items row"),
    )
    executed = []
    monkeypatch.setattr(mod, "execute", lambda sql, args=None: executed.append((sql, args)) or 1)
    marked = []
    monkeypatch.setattr(
        mod,
        "mark_auto_translated",
        lambda table, target_id, source_ref_id, bulk_task_id: marked.append(
            (table, target_id, source_ref_id, bulk_task_id)
        )
        or 1,
    )

    target_id = mod.sync_video_result(
        parent_task_id="bt-1",
        product_id=77,
        lang="es",
        source_raw_id=301,
        video_object_key="1/medias/77/es_result.mp4",
        cover_object_key="1/medias/77/es_cover.png",
    )

    assert target_id == 701
    assert executed == [
        (
            "UPDATE media_items SET source_raw_id=%s, cover_object_key=%s WHERE id=%s",
            (301, "1/medias/77/es_cover.png", 701),
        )
    ]
    assert marked == [("media_items", 701, 301, "bt-1")]
    assert lock_conn.closed is True


def test_sync_video_result_uses_material_filename_rule_for_translated_video(monkeypatch):
    from appcore import bulk_translate_backfill as mod

    lock_conn = _FakeLockConn([{"ok": 1}, {"released": 1}])
    monkeypatch.setattr(mod, "get_conn", lambda: lock_conn, raising=False)
    monkeypatch.setattr(mod, "query_one", lambda sql, args=None: None, raising=False)

    source_filename = "2026.03.25-可堆叠棒球帽收纳盒-原素材-补充素材-B-指派-张晴-去字幕.mp4"
    source_key = f"1/medias/6/raw_sources/a1b2c3d4e5f6_{source_filename}"
    expected = "2026.03.25-可堆叠棒球帽收纳盒-原素材-补充素材(意大利语)-指派-蔡靖华.mp4"

    monkeypatch.setattr(
        mod.medias,
        "get_raw_source",
        lambda raw_id: {
            "id": raw_id,
            "user_id": 1,
            "display_name": "",
            "video_object_key": source_key,
            "duration_seconds": 90.0,
            "file_size": 1234,
        },
    )
    monkeypatch.setattr(
        mod.medias,
        "get_product",
        lambda product_id: {"id": product_id, "name": "可堆叠棒球帽收纳盒"},
    )
    monkeypatch.setattr(
        mod.medias,
        "list_languages",
        lambda: [
            {"code": "en", "name_zh": "英语"},
            {"code": "it", "name_zh": "意大利语"},
        ],
    )
    created = {}
    monkeypatch.setattr(
        mod.medias,
        "create_item",
        lambda **kwargs: created.update(kwargs) or 701,
    )
    monkeypatch.setattr(mod, "execute", lambda sql, args=None: 1)
    monkeypatch.setattr(mod, "mark_auto_translated", lambda *args, **kwargs: 1)

    mod.sync_video_result(
        parent_task_id="bt-1",
        product_id=6,
        lang="it",
        source_raw_id=301,
        video_object_key=f"1/medias/6/it_a1b2c3d4e5f6_{source_filename}",
        cover_object_key="1/medias/6/it_cover.png",
    )

    assert created["filename"] == expected
    assert created["display_name"] == expected
