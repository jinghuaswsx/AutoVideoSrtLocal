-- Add task_id to media_items for task-to-artifact traceability
-- Design: docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md
ALTER TABLE media_items
  ADD COLUMN task_id INT DEFAULT NULL AFTER bulk_task_id,
  ADD KEY idx_task (task_id);
