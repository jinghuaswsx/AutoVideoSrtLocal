-- Add persisted progress for AI material strategist project runs.
-- Docs anchor: docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#运行进度与单任务锁

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ai_material_strategist_projects'
      AND COLUMN_NAME = 'progress_json'
  ),
  'SELECT 1',
  'ALTER TABLE ai_material_strategist_projects ADD COLUMN progress_json JSON NULL AFTER summary_json'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
