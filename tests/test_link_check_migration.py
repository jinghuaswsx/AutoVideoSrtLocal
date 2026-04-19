from pathlib import Path
import re


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


def test_schema_sql_projects_type_includes_link_check_and_current_superset():
    text = Path("db/schema.sql").read_text(encoding="utf-8")
    enum_values = _extract_enum_values(
        text,
        r"CREATE TABLE IF NOT EXISTS projects\s*\(.*?\btype\s+ENUM\((.*?)\)\s+NOT NULL DEFAULT 'translation'",
    )
    missing = EXPECTED_PROJECT_TYPES - enum_values
    assert not missing, f"db/schema.sql 的 projects.type 缺少枚举: {sorted(missing)}"


def test_link_check_migration_exists_and_keeps_full_projects_type_superset():
    path = Path("db/migrations/2026_04_19_add_link_check_project_type.sql")
    assert path.exists(), f"缺少 migration 文件: {path}"

    text = path.read_text(encoding="utf-8")
    enum_values = _extract_enum_values(
        text,
        r"ALTER TABLE projects\s+MODIFY COLUMN type ENUM\((.*?)\)\s+NOT NULL DEFAULT 'translation'",
    )
    missing = EXPECTED_PROJECT_TYPES - enum_values
    assert not missing, f"{path} 的 projects.type 缺少枚举: {sorted(missing)}"
