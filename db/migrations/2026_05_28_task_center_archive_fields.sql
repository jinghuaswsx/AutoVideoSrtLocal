-- Task center archive visibility fields.
-- Docs-anchor: docs/superpowers/specs/2026-05-28-task-center-archive-tab-design.md#数据模型

ALTER TABLE tasks
  ADD COLUMN archived_at DATETIME DEFAULT NULL,
  ADD COLUMN archived_by INT DEFAULT NULL,
  ADD KEY idx_archived_at (archived_at);
