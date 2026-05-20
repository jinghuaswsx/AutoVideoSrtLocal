-- Docs-anchor: docs/superpowers/specs/2026-05-20-user-work-scope-translation-design.md
-- Mark the initial translation-work users without creating accounts.

SET @translation_work_scope_names = JSON_ARRAY('周干琴', '顾倩', '王舒溦', '王健', '蔡靖华');

UPDATE users
SET permissions = JSON_SET(
  CASE
    WHEN JSON_VALID(COALESCE(CAST(permissions AS CHAR), '{}'))
    THEN COALESCE(permissions, JSON_OBJECT())
    ELSE JSON_OBJECT()
  END,
  '$.can_translate', JSON_EXTRACT('true', '$'),
  '$.work_scope_translation', JSON_EXTRACT('true', '$')
)
WHERE JSON_CONTAINS(@translation_work_scope_names, JSON_QUOTE(username));

SET @has_translation_work_scope_xingming = (
  SELECT COUNT(*)
  FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'users'
    AND COLUMN_NAME = 'xingming'
);

SET @translation_work_scope_sql = IF(
  @has_translation_work_scope_xingming > 0,
  'UPDATE users SET permissions = JSON_SET(CASE WHEN JSON_VALID(COALESCE(CAST(permissions AS CHAR), ''{}'')) THEN COALESCE(permissions, JSON_OBJECT()) ELSE JSON_OBJECT() END, ''$.can_translate'', JSON_EXTRACT(''true'', ''$''), ''$.work_scope_translation'', JSON_EXTRACT(''true'', ''$'')) WHERE JSON_CONTAINS(@translation_work_scope_names, JSON_QUOTE(xingming))',
  'SELECT 1'
);

PREPARE translation_work_scope_stmt FROM @translation_work_scope_sql;
EXECUTE translation_work_scope_stmt;
DEALLOCATE PREPARE translation_work_scope_stmt;
