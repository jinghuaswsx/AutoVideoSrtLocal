"""验证 bulk_translate 迁移后 schema 正确。

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md
迁移文件: db/migrations/2026_04_18_bulk_translate_schema.sql
"""
import pytest

from appcore.db import query, query_one


def test_projects_type_includes_bulk_translate():
    """projects.type 枚举必须包含 bulk_translate 与 copywriting_translate。"""
    row = query_one(
        "SHOW COLUMNS FROM projects WHERE Field = 'type'"
    )
    assert row is not None
    col_type = row["Type"]
    assert "bulk_translate" in col_type, \
        f"projects.type 缺少 bulk_translate 枚举,当前: {col_type}"
    assert "copywriting_translate" in col_type, \
        f"projects.type 缺少 copywriting_translate 枚举,当前: {col_type}"


@pytest.mark.parametrize("table", [
    "media_copywritings",
    "media_product_detail_images",
    "media_items",
    "media_product_covers",
])
def test_material_tables_have_tracking_columns(table):
    """四张素材表都必须新增关联追踪字段。"""
    rows = query(f"SHOW COLUMNS FROM {table}")
    cols = {r["Field"] for r in rows}
    for needed in ("source_ref_id", "bulk_task_id",
                   "auto_translated", "manually_edited_at"):
        assert needed in cols, f"{table} 缺少列: {needed}"


def test_video_translate_profiles_table_exists():
    """media_video_translate_profiles 表及其关键列存在。"""
    rows = query(
        "SELECT TABLE_NAME FROM information_schema.tables "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        ("media_video_translate_profiles",),
    )
    assert rows, "media_video_translate_profiles 表不存在"

    col_rows = query("SHOW COLUMNS FROM media_video_translate_profiles")
    cols = {r["Field"] for r in col_rows}
    expected = {"id", "user_id", "product_id", "lang", "params_json",
                "created_at", "updated_at"}
    missing = expected - cols
    assert not missing, f"表 media_video_translate_profiles 缺少列: {missing}"


def test_video_translate_profiles_unique_scope():
    """uk_scope 唯一索引 (user_id, product_id, lang) 必须存在。"""
    rows = query("SHOW INDEX FROM media_video_translate_profiles WHERE Key_name = 'uk_scope'")
    assert rows, "唯一索引 uk_scope 不存在"
    cols_in_order = sorted((r["Seq_in_index"], r["Column_name"]) for r in rows)
    names = [c for _, c in cols_in_order]
    assert names == ["user_id", "product_id", "lang"], \
        f"uk_scope 索引列顺序不正确: {names}"
