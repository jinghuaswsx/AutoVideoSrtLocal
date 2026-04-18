"""bulk_translate_associations 辅助函数测试。

前置: MySQL 运行 + 本期迁移已应用。
"""
import pytest

from appcore.db import execute, query_one
from appcore.bulk_translate_associations import (
    mark_auto_translated,
    mark_manually_edited,
)

TEST_PRODUCT_ID = 9_999_991  # 取一个不大可能碰撞的 id


@pytest.fixture
def clean_copies():
    """每个测试前后清空该测试 product 的 copywritings。"""
    execute("DELETE FROM media_copywritings WHERE product_id = %s",
            (TEST_PRODUCT_ID,))
    yield
    execute("DELETE FROM media_copywritings WHERE product_id = %s",
            (TEST_PRODUCT_ID,))


def _insert_copy(lang: str, title: str = "t") -> int:
    """插入一条最小 copywriting 行,返回新行 id。"""
    return execute(
        "INSERT INTO media_copywritings (product_id, lang, idx, title) "
        "VALUES (%s, %s, 1, %s)",
        (TEST_PRODUCT_ID, lang, title),
    )


def test_mark_auto_translated_sets_all_three_fields(clean_copies):
    """应同时设置 source_ref_id + bulk_task_id + auto_translated=1。"""
    src_id = _insert_copy("en", "source")
    tgt_id = _insert_copy("de", "Willkommen")

    rows = mark_auto_translated(
        table="media_copywritings",
        target_id=tgt_id,
        source_ref_id=src_id,
        bulk_task_id="task_xxx",
    )
    assert rows == 1

    row = query_one(
        "SELECT source_ref_id, bulk_task_id, auto_translated "
        "FROM media_copywritings WHERE id = %s",
        (tgt_id,),
    )
    assert row["source_ref_id"] == src_id
    assert row["bulk_task_id"] == "task_xxx"
    assert row["auto_translated"] == 1


def test_mark_auto_translated_idempotent(clean_copies):
    """连续调用两次应该不报错且结果一致。"""
    src_id = _insert_copy("en", "source")
    tgt_id = _insert_copy("de", "Willkommen")

    mark_auto_translated("media_copywritings", tgt_id, src_id, "task_1")
    mark_auto_translated("media_copywritings", tgt_id, src_id, "task_1")
    row = query_one(
        "SELECT bulk_task_id, auto_translated FROM media_copywritings WHERE id=%s",
        (tgt_id,),
    )
    assert row["bulk_task_id"] == "task_1"
    assert row["auto_translated"] == 1


def test_mark_manually_edited_sets_timestamp(clean_copies):
    """manually_edited_at 从 NULL 变为非 NULL。"""
    src_id = _insert_copy("en", "source")
    tgt_id = _insert_copy("de", "Willkommen")
    mark_auto_translated("media_copywritings", tgt_id, src_id, "task_1")

    before = query_one(
        "SELECT manually_edited_at FROM media_copywritings WHERE id=%s",
        (tgt_id,),
    )
    assert before["manually_edited_at"] is None

    rows = mark_manually_edited("media_copywritings", tgt_id)
    assert rows == 1

    after = query_one(
        "SELECT manually_edited_at, auto_translated, source_ref_id "
        "FROM media_copywritings WHERE id=%s",
        (tgt_id,),
    )
    assert after["manually_edited_at"] is not None
    # 关键:人工编辑后 auto_translated 和 source_ref_id 仍保留
    assert after["auto_translated"] == 1
    assert after["source_ref_id"] == src_id


def test_rejects_unknown_table():
    """白名单之外的表名必须报错,防 SQL 注入。"""
    with pytest.raises(ValueError, match="Unsupported table"):
        mark_auto_translated(
            table="users; DROP TABLE users; --",
            target_id=1, source_ref_id=1, bulk_task_id="x",
        )
    with pytest.raises(ValueError, match="Unsupported table"):
        mark_manually_edited(table="random_table", target_id=1)


def test_rejects_sql_injection_attempts():
    """即使字符串里只是普通别的表名,也要拒绝。"""
    with pytest.raises(ValueError):
        mark_auto_translated(
            table="projects",
            target_id=1, source_ref_id=1, bulk_task_id="x",
        )


def test_bulk_task_id_can_be_null(clean_copies):
    """bulk_task_id 允许为 None(e.g. 测试/未挂到父任务场景)。"""
    src_id = _insert_copy("en", "source")
    tgt_id = _insert_copy("de", "Willkommen")
    rows = mark_auto_translated("media_copywritings", tgt_id, src_id, None)
    assert rows == 1
    row = query_one(
        "SELECT bulk_task_id, auto_translated FROM media_copywritings WHERE id=%s",
        (tgt_id,),
    )
    assert row["bulk_task_id"] is None
    assert row["auto_translated"] == 1
