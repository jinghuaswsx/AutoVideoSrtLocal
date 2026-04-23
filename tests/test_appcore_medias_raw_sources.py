import pytest

from appcore import medias
from appcore.db import execute as db_execute, query_one


def _hard_cleanup_product(product_id: int) -> None:
    db_execute("DELETE FROM media_raw_sources WHERE product_id=%s", (product_id,))
    db_execute("DELETE FROM media_items WHERE product_id=%s", (product_id,))
    db_execute("DELETE FROM media_copywritings WHERE product_id=%s", (product_id,))
    db_execute("DELETE FROM media_product_covers WHERE product_id=%s", (product_id,))
    db_execute("DELETE FROM media_product_detail_images WHERE product_id=%s", (product_id,))
    db_execute("DELETE FROM media_products WHERE id=%s", (product_id,))


@pytest.fixture
def user_id():
    row = query_one("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    assert row, "No users in DB; create one before running these tests."
    return int(row["id"])


def _mk_product(user_id: int) -> int:
    return medias.create_product(user_id=user_id, name="raw-src-test-prod")


def test_create_and_list_raw_source(user_id):
    pid = _mk_product(user_id)
    try:
        rid = medias.create_raw_source(
            pid,
            user_id=user_id,
            display_name="v1",
            video_object_key=f"{user_id}/medias/{pid}/raw_sources/abc_v.mp4",
            cover_object_key=f"{user_id}/medias/{pid}/raw_sources/abc_c.cover.jpg",
            duration_seconds=12.3,
            file_size=111,
            width=1280,
            height=720,
        )
        rows = medias.list_raw_sources(pid)
        assert len(rows) == 1
        assert rows[0]["id"] == rid
        assert rows[0]["display_name"] == "v1"
    finally:
        _hard_cleanup_product(pid)


def test_get_raw_source_honors_soft_delete(user_id):
    pid = _mk_product(user_id)
    try:
        rid = medias.create_raw_source(
            pid,
            user_id=user_id,
            display_name=None,
            video_object_key="k1",
            cover_object_key="k2",
        )
        assert medias.get_raw_source(rid) is not None
        assert medias.soft_delete_raw_source(rid) == 1
        assert medias.get_raw_source(rid) is None
        assert medias.list_raw_sources(pid) == []
    finally:
        _hard_cleanup_product(pid)


def test_update_raw_source_whitelist(user_id):
    pid = _mk_product(user_id)
    try:
        rid = medias.create_raw_source(
            pid,
            user_id,
            display_name="a",
            video_object_key="k1",
            cover_object_key="k2",
        )
        medias.update_raw_source(rid, display_name="b", sort_order=5)
        row = medias.get_raw_source(rid)
        assert row["display_name"] == "b"
        assert row["sort_order"] == 5
        medias.update_raw_source(rid, video_object_key="HACK")
        row = medias.get_raw_source(rid)
        assert row["video_object_key"] == "k1"
    finally:
        _hard_cleanup_product(pid)


def test_count_raw_sources_by_product(user_id):
    p1 = _mk_product(user_id)
    p2 = _mk_product(user_id)
    try:
        for _ in range(3):
            medias.create_raw_source(
                p1,
                user_id,
                display_name=None,
                video_object_key="v",
                cover_object_key="c",
            )
        medias.create_raw_source(
            p2,
            user_id,
            display_name=None,
            video_object_key="v",
            cover_object_key="c",
        )
        assert medias.count_raw_sources_by_product([p1, p2]) == {p1: 3, p2: 1}
    finally:
        _hard_cleanup_product(p1)
        _hard_cleanup_product(p2)


def test_collect_refs_includes_raw_sources(user_id):
    pid = _mk_product(user_id)
    try:
        medias.create_raw_source(
            pid,
            user_id,
            display_name=None,
            video_object_key="vvv",
            cover_object_key="ccc",
        )
        refs = medias.collect_media_object_references()
        keys = {r["object_key"]: r["sources"] for r in refs}
        assert "vvv" in keys and "raw_source_video" in keys["vvv"]
        assert "ccc" in keys and "raw_source_cover" in keys["ccc"]
    finally:
        _hard_cleanup_product(pid)


def test_list_raw_sources_attaches_video_translation_status(monkeypatch):
    rows_by_sql = []

    def fake_query(sql, args=None):
        rows_by_sql.append((sql, args))
        if "FROM media_raw_sources" in sql:
            return [{
                "id": 88,
                "product_id": 123,
                "display_name": "clean source",
                "video_object_key": "raw.mp4",
                "cover_object_key": "raw.jpg",
            }]
        if "FROM media_items" in sql:
            return [{
                "id": 701,
                "source_raw_id": 88,
                "lang": "de",
                "filename": "de-final.mp4",
                "display_name": "German final",
                "auto_translated": 1,
                "bulk_task_id": "bt-1",
                "created_at": None,
            }]
        raise AssertionError(sql)

    monkeypatch.setattr(medias, "query", fake_query)

    rows = medias.list_raw_sources(123)

    assert rows[0]["translations"]["de"]["status"] == "translated"
    assert rows[0]["translations"]["de"]["item_id"] == 701
    assert rows[0]["translations"]["de"]["display_name"] == "German final"
