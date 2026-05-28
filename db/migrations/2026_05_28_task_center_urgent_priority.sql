-- db/migrations/2026_05_28_task_center_urgent_priority.sql
-- 任务中心紧急任务标记；详见 docs/superpowers/specs/2026-05-28-task-center-urgent-priority-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tasks'
      AND COLUMN_NAME = 'is_urgent'
  ),
  'SELECT 1',
  'ALTER TABLE tasks ADD COLUMN is_urgent TINYINT(1) NOT NULL DEFAULT 0 AFTER last_reason'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tasks'
      AND INDEX_NAME = 'idx_urgent_created'
  ),
  'SELECT 1',
  'ALTER TABLE tasks ADD KEY idx_urgent_created (is_urgent, created_at, id)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
