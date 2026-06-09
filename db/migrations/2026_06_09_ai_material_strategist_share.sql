-- Public sharing for AI material strategist reports.
-- Docs anchor: docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#公开分享报告

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ai_material_strategist_projects'
      AND COLUMN_NAME = 'share_token'
  ),
  'SELECT 1',
  'ALTER TABLE ai_material_strategist_projects ADD COLUMN share_token VARCHAR(80) NULL AFTER progress_json'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ai_material_strategist_projects'
      AND COLUMN_NAME = 'share_enabled_at'
  ),
  'SELECT 1',
  'ALTER TABLE ai_material_strategist_projects ADD COLUMN share_enabled_at DATETIME NULL AFTER share_token'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ai_material_strategist_projects'
      AND INDEX_NAME = 'uk_ai_material_project_share_token'
  ),
  'SELECT 1',
  'ALTER TABLE ai_material_strategist_projects ADD UNIQUE KEY uk_ai_material_project_share_token (share_token)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
