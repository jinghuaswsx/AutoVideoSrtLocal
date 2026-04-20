from pathlib import Path
import re


SCHEMA_PATH = Path("db/schema.sql")
MIGRATION_PATH = Path("db/migrations/2026_04_19_add_link_check_project_type.sql")

SCHEMA_PROJECTS_TYPE_PATTERN = (
    r"CREATE TABLE IF NOT EXISTS projects\s*\(.*?\btype\s+ENUM\((.*?)\)\s+"
    r"NOT NULL DEFAULT 'translation'"
)
MIGRATION_PROJECTS_TYPE_PATTERN = (
    r"ALTER TABLE projects\s+MODIFY COLUMN type ENUM\((.*?)\)\s+"
    r"NOT NULL DEFAULT 'translation'"
)

EXPECTED_PROJECT_TYPES = {
    "translation",
    "de_translate",
    "fr_translate",
    "copywriting",
    "video_creation",
    "video_review",
    "text_translate",
    "subtitle_removal",
    "translate_lab",
    "image_translate",
    "multi_translate",
    "bulk_translate",
    "copywriting_translate",
    "link_check",
}


def _extract_enum_values(text: str, pattern: str) -> set[str]:
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    assert match, f"未找到匹配枚举定义: {pattern}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _read_project_type_enum(path: Path, pattern: str) -> set[str]:
    return _extract_enum_values(path.read_text(encoding="utf-8"), pattern)


def _assert_project_types_match(actual: set[str], source_label: str) -> None:
    missing = sorted(EXPECTED_PROJECT_TYPES - actual)
    extra = sorted(actual - EXPECTED_PROJECT_TYPES)
    assert not missing and not extra, (
        f"{source_label} 的 projects.type 枚举漂移: "
        f"缺少枚举={missing}, 多余枚举={extra}"
    )


def test_assert_project_types_match_reports_missing_and_extra_values():
    actual = {"translation", "legacy_type"}

    try:
        _assert_project_types_match(actual, "example.sql")
    except AssertionError as exc:
        message = str(exc)
    else:
        raise AssertionError("预期 _assert_project_types_match 在集合漂移时失败")

    assert "example.sql" in message
    assert "缺少枚举" in message
    assert "多余枚举" in message
    assert "de_translate" in message
    assert "legacy_type" in message


def test_schema_sql_projects_type_includes_link_check_and_current_superset():
    enum_values = _read_project_type_enum(SCHEMA_PATH, SCHEMA_PROJECTS_TYPE_PATTERN)
    _assert_project_types_match(enum_values, "db/schema.sql")


def test_link_check_migration_exists_and_keeps_full_projects_type_superset():
    assert MIGRATION_PATH.exists(), f"缺少 migration 文件: {MIGRATION_PATH}"

    enum_values = _read_project_type_enum(
        MIGRATION_PATH,
        MIGRATION_PROJECTS_TYPE_PATTERN,
    )
    _assert_project_types_match(enum_values, str(MIGRATION_PATH))


def test_schema_sql_and_link_check_migration_keep_identical_projects_type_sets():
    schema_enum_values = _read_project_type_enum(
        SCHEMA_PATH,
        SCHEMA_PROJECTS_TYPE_PATTERN,
    )
    migration_enum_values = _read_project_type_enum(
        MIGRATION_PATH,
        MIGRATION_PROJECTS_TYPE_PATTERN,
    )

    assert schema_enum_values == migration_enum_values, (
        f"{SCHEMA_PATH} 与 {MIGRATION_PATH} 的 projects.type 枚举集合不一致: "
        f"仅 schema 有 {sorted(schema_enum_values - migration_enum_values)}, "
        f"仅 migration 有 {sorted(migration_enum_values - schema_enum_values)}"
    )
